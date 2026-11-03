"""🤖 Optimiseur autonome : backtest nocturne, choix, application automatique aux comptes, message, annulation, journal.

Réglages (gardés en base, modifiables depuis Telegram) : opt_on (1/0), opt_hour (heure locale de lancement),
opt_grp (prochain groupe), opt_day (dernière nuit faite). Journal détaillé : data\\logs\\optim.log.
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

    # ---- réglages
    @property
    def on(self) -> bool:
        return self.ctx.db.meta_get("opt_on", "1") != "0"

    @property
    def hour(self) -> int:
        try:
            return int(self.ctx.db.meta_get("opt_hour", "23") or 23) % 24
        except ValueError:
            return 23

    def group_index(self) -> int:
        try:
            return int(self.ctx.db.meta_get("opt_grp", "0") or 0) % len(optim.GROUPS)
        except ValueError:
            return 0

    def set_on(self, on: bool) -> None:
        self.ctx.db.meta_set("opt_on", "1" if on else "0")
        log.info("optimiseur %s", "activé" if on else "désactivé")

    def next_hour(self) -> int:
        h = HOURS[(HOURS.index(self.hour) + 1) % len(HOURS)] if self.hour in HOURS else 23
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
            await self._send("⚠️ <b>Optimiseur</b> : configure d'abord le terminal de backtest (📈 Rapports → 🧪 Backtest).")
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
        gi = self.group_index()
        vs: list[optim.Variant] = []
        for k in range(len(optim.GROUPS)):                     # groupe sans objet (ex. grille fixe) : groupe suivant
            gi = (self.group_index() + k) % len(optim.GROUPS)
            vs = optim.variants(params, optim.GROUPS[gi][0])
            if vs:
                break
        self.ctx.db.meta_set("opt_grp", str((gi + 1) % len(optim.GROUPS)))
        gkey, gtitle, gparams = optim.GROUPS[gi]
        if not vs:
            self.note("aucun réglage à tester")
            return
        self.note(f"groupe {gtitle} : {len(vs)} variante(s) — " + " · ".join(v.name for v in vs))
        cur = "; ".join(optim.label_of(p, params[p]) for p in (gparams or list(optim.FILTERS.values())) if p in params)
        self.note(f"valeurs actuelles : {cur}")
        d_from, d_to = bt.date_range(optim.SAFETY_DAYS)
        inputs = await self.r._bt_inputs(ref.id)
        if isinstance(inputs, str):
            self.note(f"backtest impossible : {inputs}")
            return
        bparams, label, deposit, currency, leverage, comm, slip = inputs
        rid = self.ctx.db.bt_create("opt", d_from, d_to, "reel", f"🤖 optimiseur {gtitle} · {label}")
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
        dec = optim.decide(rows, vs, d_to)
        for r in dec.table:
            log.info("  %-34s 30 j : ✅ %d/%d %+.2f € | 60 j : 💀 %d DD %.0f %% %+.2f € %s", r.name, r.won30, r.n30, r.net30,
                     r.lost60, r.dd60, r.net60, "sûr" if r.safe else "NON SÛR")
        applied: dict = {}
        lines = [f"🤖 <b>Optimiseur · {esc(gtitle)}</b> · nuit du {self._now().strftime('%d/%m')}",
                 f"<i>{len(vs)} variantes testées · choix sur {optim.CHOOSE_DAYS} j, sécurité sur {optim.SAFETY_DAYS} j · backtest #{rid}</i>", ""]
        if dec.change is not None:
            v = dec.change
            ok_n = await self._apply(running, v.param, dec.applied)
            applied = {v.param: [v.old, dec.applied]}
            lines.append(f"✅ <b>Changé</b> : {esc(optim.label_of(v.param, v.old).split(' : ')[0])} "
                         f"<b>{esc(optim.value_text(v.param, v.old))} → {esc(optim.value_text(v.param, dec.applied))}</b>"
                         + (f" (meilleur testé : {esc(optim.value_text(v.param, v.new))})" if dec.applied != v.new else ""))
            lines.append(f"   pourquoi : {esc(dec.reason)}")
            lines.append(f"   appliqué à {ok_n}/{len(running)} compte(s)" + (" + preset ⭐" if self.r.dash.store.active else ""))
            self.note(f"CHANGEMENT {v.param} : {v.old} → {dec.applied} ({dec.reason}) ; appliqué à {ok_n}/{len(running)}")
        else:
            lines.append(f"⏸ <b>Aucun changement</b> : {esc(dec.note)}")
            self.note(f"aucun changement : {dec.note}")
        ref_row = dec.table[0] if dec.table else None
        if ref_row is not None:
            lines.append(f"\n📊 Réglages actuels : 30 j ✅ {ref_row.won30}/{ref_row.n30} · {money(ref_row.net30, currency, True)} · "
                         f"60 j 💀 {ref_row.lost60} · DD {ref_row.dd60:.0f} %")
        top = sorted([r for r in dec.table[1:] if r.n30], key=lambda r: (not r.safe, -r.net30))[:6]
        for r in top:
            lines.append(f"{'🟢' if r.safe else '💀'} {esc(r.name)} : ✅ {r.won30}/{r.n30} · {money(r.net30, currency, True)} · "
                         f"DD {r.dd60:.0f} %" + (f" · 💀 {r.lost60}" if r.lost60 else ""))
        nxt = optim.GROUPS[self.group_index()][1]
        lines.append(f"\n<i>Prochaine nuit ({self.hour:02d}:00) : {esc(nxt)}</i>")
        oid = self.ctx.db.opt_add(gkey, "change" if applied else "inchange", "\n".join(lines), json.dumps(applied), rid)
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
                 f"État : {'✅ activé' if self.on else '⏸ désactivé'} · chaque nuit à <b>{self.hour:02d}:00</b>",
                 f"Prochain groupe : {esc(optim.GROUPS[self.group_index()][1])}"]
        if self.running:
            lines.append(f"⏳ En cours depuis {int((time.time() - self.started) / 60)} min : {esc(self.stage)}")
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
                    k, (o, n) = next(iter(ap.items()))
                    what = f"{optim.label_of(k, o).split(' : ')[0]} {optim.value_text(k, o)} → {optim.value_text(k, n)}"
                    what += " (annulé)" if r["reverted"] else ""
                else:
                    what = "aucun changement" if r["status"] == "inchange" else f"échec : {r['summary'][:60]}"
                lines.append(f"• {when} · {esc(r['grp'])} : {esc(what)}")
        else:
            lines += ["", "<i>Aucune nuit d'optimisation pour l'instant.</i>"]
        return "\n".join(lines)


GUIDE = """❓ <b>L'optimiseur, en bref</b>

<b>Ce qu'il fait</b> : chaque nuit, à l'heure choisie, il rejoue les <b>60 derniers jours</b> dans le testeur avec tes réglages actuels et une dizaine de variantes (des robots fictifs du labo). Il ne touche qu'à un <b>groupe</b> de réglages par nuit, à tour de rôle :
🧱 Grille (multiplicateur ATR, minimum, maximum) → 📦 Lots (ordres en attente, multiplicateur de lot) → 🎯 Objectif et bougies du signal récent → 🛡️ Filtres (chacun activé ou désactivé).

<b>Comment il choisit</b>
• Il ne garde qu'une variante qui <b>ne perd jamais le compte</b> sur 60 jours et 30 jours, avec un drawdown sous 90 %.
• Si tes réglages actuels sont sûrs, la variante doit <b>gagner nettement plus sur les 30 derniers jours</b> (marché récent) sans faire moins bien sur 60 jours.
• Si tes réglages actuels perdent le compte ou frôlent la perte, il prend la variante sûre la plus rentable.
• Il ne change qu'<b>un seul réglage, d'un seul cran</b>, par nuit. Les nuits suivantes, il reteste autour de la nouvelle valeur : il s'affine tout seul et suit le marché.

<b>Ce que tu reçois</b> : un message par nuit avec l'ancienne valeur, la nouvelle, la raison, les meilleures variantes et un bouton <b>↩️ Annuler ce changement</b>. Le réglage est appliqué à tous les comptes lancés et au preset ⭐ actif.

<b>Les boutons</b>
• ✅/⏸ : activer ou suspendre l'optimiseur.
• 🕐 : changer l'heure de lancement (le test dure ~20-40 min, finis-le avant tes créneaux).
• ▶️ Lancer maintenant : une nuit tout de suite.
• 📜 Journal : tout ce qu'il a fait, ligne par ligne ; 📎 le fichier complet à me transmettre en cas de problème.

<b>Limites</b> : le passé ne garantit pas l'avenir ; un backtest ne connaît pas ton slippage exact. Le SL d'urgence n'est jamais touché."""
