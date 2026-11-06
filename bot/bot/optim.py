"""🤖 Optimiseur autonome : backtest nocturne, choix, application automatique aux comptes, message, annulation, journal.

Réglages (gardés en base, modifiables depuis Telegram) : opt_on (1/0), opt_hour (heure locale de lancement),
opt_grp (prochaine catégorie), opt_day (dernière nuit faite), opt_scale / opt_idle (portée de recherche et passages
sans changement par réglage). Journal détaillé : data\\logs\\optim.log.
"""
from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core import backtest as bt, labo, optim
from core.fsio import atomic_write, safe_remove
from . import actions as act
from . import presets_ui as pui
from .render import esc, money

log = logging.getLogger("scalpx.optim")
HOURS = [20, 21, 22, 23, 0, 1, 2, 3, 4, 5]
DEFAULT_HOUR = 21                    # départ à 21 h : la nuit entière pour tester une catégorie (pas de limite de durée)


def setup_log(data_dir: Path) -> Path:
    """Journal propre à l'optimiseur (en plus de bot.log), lisible depuis Telegram."""
    p = Path(data_dir) / "logs" / "optim.log"
    if not any(isinstance(h, logging.FileHandler) and Path(getattr(h, "baseFilename", "")) == p for h in log.handlers):
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            h = logging.handlers.RotatingFileHandler(p, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
            h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            log.addHandler(h)
            log.setLevel(logging.INFO)
        except OSError:
            pass
    return p


class Optimizer:
    def __init__(self, router) -> None:
        self.r = router
        self.ctx = router.ctx
        self.running = False
        self.stage = ""
        self.started = 0.0
        self.path = setup_log(self.ctx.cfg.paths.data)
        if self.ctx.db.meta_get("opt_v3", "") != "1":          # bot 3.0.0 : départ à 21 h, compteurs de portée remis à zéro
            self.ctx.db.meta_set("opt_hour", str(DEFAULT_HOUR))
            self.ctx.db.meta_set("opt_scale", "{}")
            self.ctx.db.meta_set("opt_idle", "{}")
            self.ctx.db.meta_set("opt_v3", "1")

    # ---- réglages
    @property
    def on(self) -> bool:
        return self.ctx.db.meta_get("opt_on", "1") != "0"

    @property
    def hour(self) -> int:
        try:
            return int(self.ctx.db.meta_get("opt_hour", str(DEFAULT_HOUR)) or DEFAULT_HOUR) % 24
        except ValueError:
            return DEFAULT_HOUR

    def group_index(self) -> int:
        try:
            return int(self.ctx.db.meta_get("opt_grp", "0") or 0) % len(optim.GROUPS)
        except ValueError:
            return 0

    def schedule_safety(self, lost: bool, was_safety: bool) -> int:
        """Nuit 🛟 Sécurité seulement si le compte est VRAIMENT perdu (💀) sur 60 j, et au plus une fois par tour complet :
        après une nuit de sécurité, les 4 groupes normaux passent d'abord, même si le danger persiste. Renvoie l'attente."""
        try:
            wait = int(self.ctx.db.meta_get("opt_safety_wait", "0") or 0)
        except ValueError:
            wait = 0
        if was_safety:
            wait = len(optim.GROUPS)
        elif wait > 0:
            wait -= 1
        self.ctx.db.meta_set("opt_safety_wait", str(wait))
        self.ctx.db.meta_set("opt_safety", "1" if (lost and not was_safety and wait == 0) else "0")
        return wait

    def _counts(self, key: str) -> dict[str, int]:
        try:
            d = json.loads(self.ctx.db.meta_get(key, "{}") or "{}")
            return {k: int(v) for k, v in d.items()}
        except (ValueError, TypeError, AttributeError):
            return {}

    def scale(self) -> dict[str, int]:
        """Portée de recherche par réglage (en crans), élargie après 3 passages sans changement."""
        return self._counts("opt_scale")

    def idle(self) -> dict[str, int]:
        """Passages consécutifs sans changement, par réglage."""
        return self._counts("opt_idle")

    @property
    def safety_next(self) -> bool:
        return self.ctx.db.meta_get("opt_safety", "0") == "1"

    def set_on(self, on: bool) -> None:
        self.ctx.db.meta_set("opt_on", "1" if on else "0")
        log.info("optimiseur %s", "activé" if on else "désactivé")

    def next_hour(self) -> int:
        h = HOURS[(HOURS.index(self.hour) + 1) % len(HOURS)] if self.hour in HOURS else DEFAULT_HOUR
        self.ctx.db.meta_set("opt_hour", str(h))
        log.info("heure de l'optimiseur : %02d:00", h)
        return h

    def _now(self) -> datetime:
        return datetime.now(ZoneInfo(self.ctx.cfg.reports.timezone))

    def due(self) -> bool:
        """À lancer maintenant ? (activé, heure atteinte depuis moins de 50 min, pas encore fait pour cette nuit)."""
        if not self.on or self.running:
            return False
        n = self._now()
        return n.hour == self.hour and n.minute < 50 and self.ctx.db.meta_get("opt_day", "") != n.strftime("%Y-%m-%d")

    def note(self, text: str) -> None:
        self.stage = text
        log.info(text)

    # ---- une nuit
    async def run(self, manual: bool = False) -> None:
        if self.running:
            return
        if self.r.bt.busy():
            if manual:
                self.r.dash.flash("⏳ Un backtest est en cours : l'optimiseur attend sa fin.")
            log.info("optimiseur : un backtest est déjà en cours, nouvel essai à la prochaine minute")
            return
        if not manual:
            self.ctx.db.meta_set("opt_day", self._now().strftime("%Y-%m-%d"))
        self.running, self.started = True, time.time()
        try:
            await self._run(manual)
        except Exception as exc:
            log.exception("optimiseur : erreur inattendue")
            await self._send(f"❌ <b>Optimiseur</b> : erreur inattendue ({esc(exc)}). Détail : 🤖 Optimiseur → 📜 Journal.")
        finally:
            self.running, self.stage = False, ""
            await self.r.dash.refresh(force=True)

    async def _send(self, text: str, kb=None, silent: bool = True) -> None:
        try:
            await self.r.sender.send(self.r.chat_id, text, kb or [[("🤖 Optimiseur", "d:opt"), ("✖ Fermer", "d:close")]], silent)
        except Exception:
            log.exception("optimiseur : message Telegram")

    async def _run(self, manual: bool) -> None:
        self.note("━━━━ nuit d'optimisation" + (" (lancée à la main)" if manual else ""))
        if not self.r.bt.configured():
            self.note("terminal de backtest non configuré : rien à faire")
            await self._send("⚠️ <b>Optimiseur</b> : configure d'abord le terminal de test (🤖 Optimiseur → 🔑 Terminal de test).")
            return
        if self.ctx.deploy.running:
            self.note("déploiement de l'EA en cours : optimisation reportée à demain")
            return
        running = [i for i in self.ctx.db.list() if self.ctx.mgr.is_running(i)]
        if not running:
            self.note("aucun terminal lancé : rien à optimiser")
            return
        ref = running[0]
        self.note(f"compte de référence : {ref.label} ({ref.id})")
        params = await pui.read_raw(self.ctx, ref)
        if not params:
            self.note("réglages du compte de référence illisibles (PARAMS sans réponse)")
            await self._send(f"⚠️ <b>Optimiseur</b> : réglages de {esc(ref.label)} illisibles, nuit annulée.")
            return
        vs: list[optim.Variant] = []
        safety = self.safety_next
        if safety:                                             # nuit 🛟 Sécurité (hors rotation : opt_grp inchangé)
            gkey, gtitle = optim.SAFETY[0], optim.SAFETY[1]
            vs = optim.variants(params, gkey)
            self.note("🛟 nuit de sécurité : la nuit précédente, les réglages actuels perdaient le compte ou frôlaient la perte")
        if not vs:
            safety = False
            gi = self.group_index()
            for k in range(len(optim.GROUPS)):                 # groupe sans objet (ex. grille fixe) : groupe suivant
                gi = (self.group_index() + k) % len(optim.GROUPS)
                vs = optim.variants(params, optim.GROUPS[gi][0], self.scale())
                if vs:
                    break
            self.ctx.db.meta_set("opt_grp", str((gi + 1) % len(optim.GROUPS)))
            gkey, gtitle = optim.GROUPS[gi][0], optim.GROUPS[gi][1]
        if not vs:
            self.note("aucun réglage à tester")
            return
        nv = len(optim.tested(vs))
        self.note(f"catégorie {gtitle} : {nv} variante(s) — " + " · ".join(v.name for v in optim.tested(vs)))
        shown = [p for p in optim.group_params(gkey) if optim.applies(params, p)]
        if gkey == "filtres":
            shown += [k for k in optim.FILTERS.values() if k in params]
        cur = "; ".join(optim.label_of(p, params[p]) for p in shown if p in params)
        if gkey == "objectif":
            cur += "; créneaux : " + optim.slots_label(optim._masks(params, optim.SLOT_KEYS))
        self.note(f"valeurs actuelles : {cur}")
        d_from, d_to = bt.date_range(optim.SAFETY_DAYS)
        inputs = await self.r._bt_inputs(ref.id)
        if isinstance(inputs, str):
            self.note(f"backtest impossible : {inputs}")
            return
        bparams, label, deposit, currency, leverage, comm, slip = inputs
        rid = self.ctx.db.bt_create("opt", d_from, d_to, "reel", f"🤖 optimiseur {gtitle} · {label}")
        self.note(f"démarrage {self._now().strftime('%H:%M')} : pas de limite de durée, le test va jusqu'au bout")
        scen = Path(self.ctx.cfg.paths.common()) / f"bt_{rid}_scen.txt"
        atomic_write(scen, optim.scenario_file(vs))
        from mt5.backtester import Run
        run = Run(rid, "opt", d_from, d_to, "reel", label)
        self.note(f"backtest #{rid} : {bt.human_date(d_from)} → {bt.human_date(d_to)}, ticks réels, dépôt {deposit:.2f} {currency}")
        last = {"stage": "", "pct": -10}

        async def quiet(r) -> None:                            # pas de message Telegram la nuit : tout va au journal
            pct = int(bt.progress_frac(r.progress, r.d_from, r.d_to) * 100)
            if r.stage != last["stage"]:
                last["stage"] = r.stage
                log.info("backtest #%s : %s", rid, r.stage)
            if pct >= last["pct"] + 10:
                last["pct"] = pct
                self.stage = f"backtest #{rid} : {pct} %"
                log.info("backtest #%s : %s %% (jour simulé %s)", rid, pct,
                         bt.human_date(int((r.progress or {}).get("sim", r.d_from) or r.d_from)))
            if r.last_line and "error" in r.last_line.lower():
                log.info("MT5 : %s", r.last_line)

        try:
            run = await self.r.bt.execute(run, params=bparams, deposit=deposit, currency=currency, leverage=leverage,
                                          comm=comm, slip=slip, notify=quiet, poll_s=self.ctx.cfg.backtest.poll_s)
        finally:
            safe_remove(scen)
        if run.result is not None:
            run.result.params = dict(bparams)
        self.ctx.db.bt_update(rid, "ok" if run.ok else "erreur", run.result.to_json() if run.result else "", run.error)
        if not run.ok:
            self.note(f"backtest #{rid} en échec : {run.error}")
            self.ctx.db.opt_add(gkey, "erreur", run.error, "", rid)
            await self._send(f"❌ <b>Optimiseur · {esc(gtitle)}</b> : backtest en échec ({esc(run.error)}). Aucun réglage changé.")
            return
        rows = labo.rows_of(self.ctx.db.labo_since(0, f"bt_{rid}"), "SERIE")
        self.note(f"backtest #{rid} terminé en {int((time.time() - self.started) / 60)} min : {len(rows)} objectifs virtuels")
        if optim.ea_too_old(rows, vs):
            need = "7.8.0" if gkey == "continu" else "7.7.0"
            self.note(f"EA du testeur trop ancien (< {need}) : options des variantes ignorées, nuit annulée")
            self.ctx.db.opt_add(gkey, "erreur", f"EA du testeur < {need}", "", rid)
            await self._send(f"❌ <b>Optimiseur · {esc(gtitle)}</b> : l'EA du testeur est trop ancien ({need} ou plus requis). "
                             "Installe la mise à jour de l'EA ; aucun réglage changé.")
            return
        dec = optim.decide(rows, vs, d_to, params)
        wider: list[str] = []
        if not safety:                                         # portée de recherche des réglages testés pour le prochain tour
            keys = sorted({k for v in vs for k in v.changes if k in optim.NUM})
            changed = set(dec.change.changes) if dec.change is not None else set()
            before = self.scale()
            sc, idl = optim.next_scale(before, self.idle(), keys, changed)
            self.ctx.db.meta_set("opt_scale", json.dumps(sc))
            self.ctx.db.meta_set("opt_idle", json.dumps(idl))
            wider = [k for k in keys if sc.get(k, 1) > before.get(k, 1)]
            if wider:
                self.note(f"{optim.IDLE_PASSES} passages sans changement : au prochain tour, recherche plus loin (" +
                          ", ".join(f"{k} ±{sc[k]} crans" for k in wider) + ")")
        # après une nuit normale où les réglages actuels sont dangereux : la nuit suivante est une nuit 🛟 Sécurité
        # (après une nuit de sécurité, on revient à la rotation, qui revérifiera)
        lost = bool(dec.table) and dec.table[0].lost60 > 0
        wait = self.schedule_safety(lost, safety)
        if lost and not safety and wait > 0:
            self.note(f"compte perdu sur 60 j, mais nuit de sécurité déjà faite récemment : prochaine possible dans {wait} nuit(s)")
        for r in dec.table:
            log.info("  %-34s 30 j : ✅ %d/%d %+.2f € | 60 j : 💀 %d DD %.0f %% %+.2f € %s", r.name, r.won30, r.n30, r.net30,
                     r.lost60, r.dd60, r.net60, "sûr" if r.safe else "NON SÛR")
        applied: dict = {}
        lines = [f"🤖 <b>Optimiseur · {esc(gtitle)}</b> · nuit du {self._now().strftime('%d/%m')}",
                 f"<i>{nv} variantes testées · choix sur {optim.CHOOSE_DAYS} j, sécurité sur {optim.SAFETY_DAYS} j · "
                 f"backtest #{rid} ({int((time.time() - self.started) / 60)} min)</i>", ""]
        if dec.change is not None:
            v = dec.change
            oks = []
            for param, (old, new) in v.changes.items():
                oks.append(await self._apply(running, param, new))
                applied[param] = [old, new]
            what = " · ".join(f"{optim.label_of(p_, o).split(' : ')[0]} <b>{esc(optim.value_text(p_, o))} → {esc(optim.value_text(p_, n))}</b>"
                              for p_, (o, n) in v.changes.items())
            lines.append(f"✅ <b>Changé</b> : {what}" + (" <i>(combinaison testée ensemble)</i>" if len(v.changes) > 1 else ""))
            lines.append(f"   pourquoi : {esc(dec.reason)}")
            lines.append(f"   appliqué à {min(oks)}/{len(running)} compte(s)" + (" + preset ⭐" if self.r.dash.store.active else ""))
            self.note("CHANGEMENT " + " ; ".join(f"{p_} : {o} → {n}" for p_, (o, n) in v.changes.items())
                      + f" ({dec.reason}) ; appliqué à {min(oks)}/{len(running)}")
        else:
            lines.append(f"⏸ <b>Aucun changement</b> : {esc(dec.note)}")
            self.note(f"aucun changement : {dec.note}")
        sp = dec.slots
        if sp is not None:                                     # 🕐 créneaux : retraits / ajouts
            if sp.changes:
                n_ok = []
                for key, (old, new) in sp.changes.items():
                    n_ok.append(await self._apply(running, key, new))
                    applied[key] = [old, new]
                cur_m = optim._masks(params, optim.SLOT_KEYS)
                new_m = [int(sp.changes.get(k, (str(m), str(m)))[1]) for k, m in zip(optim.SLOT_KEYS, cur_m)]
                lines.append(f"🕐 <b>Créneaux</b> : {esc(optim.slots_label(cur_m))} → <b>{esc(optim.slots_label(new_m))}</b> "
                             f"(appliqué à {min(n_ok)}/{len(running)} compte(s))")
                self.note("CRÉNEAUX " + optim.slots_label(cur_m) + " → " + optim.slots_label(new_m))
            else:
                lines.append(f"🕐 <b>Créneaux</b> : inchangés (aucun au-dessus de {optim.SLOT_DD:.0f} % de drawdown, "
                             "aucune heure libre sûre et gagnante)")
                self.note("créneaux inchangés")
            for ln in sp.lines:
                lines.append(f"   {esc(ln)}")
                self.note("  " + ln)
        ref_row = dec.table[0] if dec.table else None
        if ref_row is not None:
            lines.append(f"\n📊 Réglages actuels : 30 j ✅ {ref_row.won30}/{ref_row.n30} · {money(ref_row.net30, currency, True)} · "
                         f"60 j 💀 {ref_row.lost60} · DD {ref_row.dd60:.0f} %")
        top = sorted([r for r in dec.table[1:] if r.n30 and not r.name.startswith("Réglages actuels")],
                     key=lambda r: (not r.safe, -r.net30))[:6]
        for r in top:
            lines.append(f"{'🟢' if r.safe else '💀'} {esc(r.name)} : ✅ {r.won30}/{r.n30} · {money(r.net30, currency, True)} · "
                         f"DD {r.dd60:.0f} %" + (f" · 💀 {r.lost60}" if r.lost60 else ""))
        nxt = optim.SAFETY[1] if self.safety_next else optim.GROUPS[self.group_index()][1]
        if wider:
            sc = self.scale()
            lines.append(f"🔭 {optim.IDLE_PASSES} passages sans changement : au prochain tour, je cherche plus loin ("
                         + ", ".join(f"{esc(optim.NUM[k][0])} ±{sc.get(k, 1)} crans" for k in wider) + ").")
        lines.append(f"\n<i>Prochaine nuit ({self.hour:02d}:00) : {esc(nxt)}</i>")
        oid = self.ctx.db.opt_add(gkey, "change" if applied else "inchange", "\n".join(lines), json.dumps(applied), rid)
        self.note(f"nuit terminée à {self._now().strftime('%H:%M')}")
        kb = ([[("↩️ Annuler ce changement", f"d:opu:{oid}")]] if applied else []) + [[("🤖 Optimiseur", "d:opt"), ("✖ Fermer", "d:close")]]
        await self._send("\n".join(lines), kb, silent=True)

    async def _apply(self, insts, key: str, raw: str) -> int:
        """Réglage appliqué à tous les comptes lancés + preset ⭐ actif (et .set de BASE)."""
        res = await asyncio.gather(*[act.send_cmd(self.ctx, i, "SET", [key, raw]) for i in insts], return_exceptions=True)
        ok = 0
        for i, r in zip(insts, res):
            good = not isinstance(r, Exception) and getattr(r, "ok", False)
            ok += 1 if good else 0
            if not good:
                log.warning("SET %s=%s → %s : %s", key, raw, i.label, r if isinstance(r, Exception) else getattr(r, "message", "?"))
        store = self.r.dash.store
        if store.active:
            store.set_value(store.active, key, raw)
            try:
                self.r.sync_base_set()
            except Exception:
                log.exception("preset ⭐ → .set de BASE")
        return ok

    async def revert(self, oid: int) -> str:
        row = self.ctx.db.opt_get(oid)
        if row is None or row["reverted"]:
            return "Déjà annulé."
        try:
            applied = json.loads(row["applied"] or "{}")
        except ValueError:
            applied = {}
        if not applied:
            return "Rien à annuler."
        running = [i for i in self.ctx.db.list() if self.ctx.mgr.is_running(i)]
        done = []
        for key, (old, new) in applied.items():
            n = await self._apply(running, key, old)
            done.append(f"{optim.label_of(key, old)} ({n}/{len(running)} compte(s))")
        self.ctx.db.opt_mark_reverted(oid)
        log.info("ANNULATION de l'optimisation #%s : %s", oid, "; ".join(done))
        return "↩️ Annulé : " + "; ".join(done)

    def status_text(self) -> str:
        lines = ["🤖 <b>Optimiseur automatique</b>",
                 f"État : {'✅ activé' if self.on else '⏸ désactivé'} · chaque nuit à <b>{self.hour:02d}:00</b> (sans limite de durée)",
                 f"Prochaine catégorie : {esc(optim.SAFETY[1] if self.safety_next else optim.GROUPS[self.group_index()][1])}",
                 "Cycle : " + " → ".join(t for _, t, _ in optim.GROUPS)]
        acc = self.r.bt.account() if getattr(self.r, "bt", None) is not None else None
        lines.append(f"🔑 Terminal de test : compte {acc['login']} · {esc(acc.get('server', ''))}" if acc
                     else "🔑 Terminal de test : <b>à configurer</b> (bouton 🔑 ci-dessous)")
        if self.running:
            lines.append(f"⏳ En cours depuis {int((time.time() - self.started) / 60)} min : {esc(self.stage)}")
        sc = {k: v for k, v in self.scale().items() if v > 1}
        if sc:
            lines.append("🔭 Recherche élargie : " + ", ".join(f"{esc(optim.NUM[k][0])} ±{v} crans" for k, v in sc.items() if k in optim.NUM))
        rows = self.ctx.db.opt_list(6)
        if rows:
            lines += ["", "<b>Dernières nuits</b>"]
            for r in rows:
                when = time.strftime("%d/%m %H:%M", time.localtime(r["ts"]))
                try:
                    ap = json.loads(r["applied"] or "{}")
                except ValueError:
                    ap = {}
                if ap:
                    what = " · ".join(f"{optim.label_of(k, o).split(' : ')[0]} {optim.value_text(k, o)} → {optim.value_text(k, n)}"
                                      for k, (o, n) in ap.items())
                    what += " (annulé)" if r["reverted"] else ""
                else:
                    what = "aucun changement" if r["status"] == "inchange" else f"échec : {r['summary'][:60]}"
                lines.append(f"• {when} · {esc(optim.group_title(r['grp']))} : {esc(what)}")
        else:
            lines += ["", "<i>Aucune nuit d'optimisation pour l'instant.</i>"]
        return "\n".join(lines)


GUIDE = """❓ <b>L'optimiseur, en bref</b>

<b>Ce qu'il fait</b> : chaque nuit à <b>21 h</b> (réglable), il rejoue les <b>60 derniers jours</b> dans le testeur avec tes réglages actuels et jusqu'à 38 variantes (des robots fictifs joués en parallèle sur les mêmes prix). Pas de limite de durée : le test va jusqu'au bout, même après 2 h. Une <b>catégorie</b> par nuit, 5 nuits à la suite :
🧱 <b>Grille et lots</b> (ATR de la grille, minimum, maximum · ordres en attente, multiplicateur de lot, trades avant multiplication)
📐 <b>Signal et TP</b> (EMA rapide, EMA lente, période ATR · multiplicateur ATR du TP, TP minimum, TP maximum)
🛡️ <b>Filtres</b> (chacun activé ou désactivé · ATR min/max, volume min/max, extension max, corps de bougie, bougies du signal récent)
🎯 <b>Objectif et créneaux</b> (% de l'objectif · créneaux)
🔁 <b>Mode continu</b> (objectifs lancés dès que tous les filtres sont OK à la clôture M5, au lieu des créneaux — ou l'inverse —, seul ou avec l'objectif à ±1 cran)

<b>Comment il choisit</b>
• Il ne garde qu'une variante qui <b>ne perd jamais le compte</b> sur 60 jours et 30 jours, avec un drawdown sous 90 %.
• Si tes réglages actuels sont sûrs, la variante doit <b>gagner nettement plus sur les 30 derniers jours</b> sans faire moins bien sur 60 jours.
• À résultat presque égal, il préfère la valeur <b>entourée de bons résultats</b> (ses voisines gagnent aussi) : plus robuste quand le marché change.
• Si tes réglages actuels <b>perdent vraiment le compte</b> (💀) sur 60 jours, la nuit suivante est une nuit 🛟 <b>Sécurité</b> (au plus une par tour) : lot moins multiplié, moins d'ordres, grille plus large ; il prend la variante sûre la plus rentable si elle garde au moins <b>80 % de ton gain</b>.

<b>Les crans</b> : un cran est le pas d'un réglage (ex. 0,1 pour l'ATR de la grille). Chaque variante bouge un réglage d'<b>un cran</b>, seul ou combiné à un autre réglage de la même famille. La meilleure variante sûre est appliquée ; les nuits suivantes, il reteste autour de la nouvelle valeur. Après <b>3 passages sans changement</b> d'un réglage, il cherche <b>deux fois plus loin</b> (2 puis 4 crans), puis revient à 1 cran.

<b>Les créneaux</b> (nuit 🎯) : un créneau est <b>retiré</b> s'il dépasse <b>80 % de drawdown</b> sur 60 jours ; chaque <b>heure pile libre</b> est testée seule et <b>ajoutée</b> si elle reste sous 80 %, ne perd jamais le compte et gagne de l'argent. Il ne retire jamais le dernier créneau. Les coupures ne sont jamais touchées.

<b>Ce que tu reçois</b> : un message par nuit avec l'ancienne valeur, la nouvelle, la raison, les meilleures variantes et un bouton <b>↩️ Annuler ce changement</b>. Le réglage est appliqué à tous les comptes lancés et au preset ⭐ actif.

<b>Les boutons</b>
• ✅/⏸ : activer ou suspendre l'optimiseur.
• 🕐 : changer l'heure de lancement.
• ▶️ Lancer maintenant : une nuit tout de suite.
• 🔑 Terminal de test : le compte du broker utilisé par le testeur (démo ou réel, aucun ordre passé).
• 📜 Journal : tout ce qu'il a fait, ligne par ligne ; 📎 le fichier complet à me transmettre en cas de problème.

<b>Limites</b> : le passé ne garantit pas l'avenir ; un backtest ne connaît pas ton slippage exact (il reprend celui mesuré sur tes vrais ordres). Le SL d'urgence et les coupures ne sont jamais touchés."""
