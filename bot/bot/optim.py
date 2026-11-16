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

from core import backtest as bt, labo, optim, presets as ps
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
        self.series_n = 0                  # Bot 3.6.0 : 🔁 série d'optimisations (0 = aucune)
        self.series_i = 0
        self.series_stop = False
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
        """À lancer maintenant ? (activé, heure atteinte depuis moins de 50 min, pas encore fait pour cette nuit).
        Pendant une 🔁 série, l'optimisation de la nuit est sautée (la série respecte déjà le cycle)."""
        if not self.on:
            return False
        n = self._now()
        today = n.strftime("%Y-%m-%d")
        if not (n.hour == self.hour and n.minute < 50 and self.ctx.db.meta_get("opt_day", "") != today):
            return False
        if self.series_active:
            self.ctx.db.meta_set("opt_day", today)
            log.info("optimisation de la nuit sautée : série en cours (%d/%d)", self.series_i, self.series_n)
            return False
        return not self.running

    # ---- 🔁 série d'optimisations (Bot 3.6.0)
    SERIES_CHOICES = (2, 3, 5, 10, 15, 20, 30, 50)       # Bot 3.6.1 : jusqu'à 50

    @property
    def series_active(self) -> bool:
        return self.series_n > 0

    def series_tag(self) -> str:
        return f" · série {self.series_i}/{self.series_n}" if self.series_active else ""

    async def run_series(self, n: int) -> None:
        """n optimisations à la suite, dans l'ordre du cycle (sécurité comprise). S'arrête sur demande (après
        l'optimisation en cours), sur un backtest en échec ou si rien n'a pu être lancé."""
        if self.running or self.series_active or n < 1:
            return
        self.series_n, self.series_i, self.series_stop = n, 0, False
        t0 = time.time()
        log.info("━━━━━━━━ SÉRIE de %d optimisations", n)
        await self._send(f"🔁 <b>Série de {n} optimisations</b> lancée : elles s'enchaînent dans l'ordre du cycle "
                         f"(1re : {esc(optim.SAFETY[1] if self.safety_next else optim.GROUPS[self.group_index()][1])}). "
                         "Un message à chaque optimisation, puis un résumé. L'optimisation de la nuit est sautée pendant la série.")
        ids: list[int] = []
        why = ""
        try:
            for i in range(1, n + 1):
                if self.series_stop:
                    why = "arrêtée à ta demande"
                    break
                self.series_i = i
                while self.r.bt.busy() or self.running:         # un test manuel tourne : on attend sa fin
                    self.stage = f"série {i}/{n} : attente de la fin du test en cours"
                    await asyncio.sleep(20)
                last = self.ctx.db.opt_list(1)
                before = last[0]["id"] if last else 0
                log.info("━━━━ série : optimisation %d/%d", i, n)
                await self.run(manual=True)
                new = self.ctx.db.opt_list(1)
                if not new or new[0]["id"] == before:
                    why = "rien n'a pu être lancé (voir 📜 Journal)"
                    break
                ids.append(new[0]["id"])
                if new[0]["status"] == "erreur":
                    why = "backtest en échec"
                    break
        except Exception as exc:
            log.exception("série d'optimisations")
            why = f"erreur inattendue ({exc})"
        finally:
            done = len(ids)
            rows = [self.ctx.db.opt_get(x) for x in ids]
            nch = sum(1 for r in rows if r is not None and r["status"] == "change")
            mins = int((time.time() - t0) / 60)
            head = "✅" if not why else "⏹" if why.startswith("arrêtée") else "⚠️"
            lines = [f"{head} <b>Série d'optimisations terminée</b> : {done}/{n} en {mins // 60} h {mins % 60:02d}"
                     + (f" · {esc(why)}" if why else ""),
                     f"{nch} changement(s) appliqué(s) à Martin et aux comptes."]
            for k, r in enumerate(rows, 1):
                if r is None:
                    continue
                g = next((t for key, t, _ in optim.GROUPS if key == r["grp"]), optim.SAFETY[1] if r["grp"] == optim.SAFETY[0] else r["grp"])
                st = {"change": "✅ changé", "inchange": "⏸ aucun changement", "erreur": "❌ échec"}.get(r["status"], r["status"])
                lines.append(f"{k}. {esc(g)} : {st}")
            lines.append(f"Prochaine : {esc(optim.SAFETY[1] if self.safety_next else optim.GROUPS[self.group_index()][1])}")
            log.info("SÉRIE terminée : %d/%d%s", done, n, f" ({why})" if why else "")
            self.series_n = self.series_i = 0
            self.series_stop = False
            await self._send("\n".join(lines))
            try:
                await self.r.dash.refresh(force=True)
            except Exception:
                pass

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
        self.note("━━━━ nuit d'optimisation" + (self.series_tag() if self.series_active else " (lancée à la main)" if manual else ""))
        if not self.r.bt.configured():
            self.note("terminal de backtest non configuré : rien à faire")
            await self._send("⚠️ <b>Optimiseur</b> : configure d'abord le terminal de test (🤖 Optimiseur → 🔑 Terminal de test).")
            return
        if self.ctx.deploy.running:
            self.note("déploiement de l'EA en cours : optimisation reportée à demain")
            return
        # Bot 3.5.0 : le preset ⭐ de travail (ex. Martin) FAIT FOI : l'optimiseur part de ses réglages (plus d'aucun compte),
        # le modifie et l'applique. Au 1er lancement, le preset de base (copie de Martin) est créé, puis jamais retouché.
        work = self.work_preset()
        params = dict(self.r.dash.store.presets.get(work, {})) if work else {}
        if not params:
            self.note("aucun preset ⭐ : rien à optimiser")
            await self._send("⚠️ <b>Optimiseur</b> : aucun preset ⭐ (ex. Martin). Crée ou choisis-en un dans 💾 Presets, nuit annulée.")
            return
        self.base_preset()
        ref = work
        running = [i for i in self.ctx.db.list() if self.ctx.mgr.is_running(i)]   # comptes où appliquer Martin
        self.note(f"réglages de départ : preset ⭐ {work} ({len(params)} réglages)")
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
        got = await self._backtest(ref, vs, f"🤖 optimiseur {gtitle}")
        if isinstance(got, str):
            if got.startswith("backtest impossible"):
                return
            self.ctx.db.opt_add(gkey, "erreur", got, "", None)
            await self._send(f"❌ <b>Optimiseur · {esc(gtitle)}{self.series_tag()}</b> : backtest en échec ({esc(got)}). Aucun réglage changé.")
            return
        run, rid, bparams, currency = got
        d_to = run.d_to
        rows = labo.rows_of(self.ctx.db.labo_since(0, f"bt_{rid}"), "SERIE")
        self.note(f"backtest #{rid} terminé en {int((time.time() - self.started) / 60)} min : {len(rows)} objectifs virtuels")
        if optim.ea_too_old(rows, vs):
            need = "7.7.0"
            self.note(f"EA du testeur trop ancien (< {need}) : options des variantes ignorées, nuit annulée")
            self.ctx.db.opt_add(gkey, "erreur", f"EA du testeur < {need}", "", rid)
            await self._send(f"❌ <b>Optimiseur · {esc(gtitle)}</b> : l'EA du testeur est trop ancien ({need} ou plus requis). "
                             "Installe la mise à jour de l'EA ; aucun réglage changé.")
            return
        dec = optim.decide(rows, vs, d_to, params)
        # après une nuit normale où les réglages actuels sont dangereux : la nuit suivante est une nuit 🛟 Sécurité
        # (après une nuit de sécurité, on revient à la rotation, qui revérifiera)
        lost = bool(dec.table) and dec.table[0].lost60 > 0
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
        wait = self.schedule_safety(lost, safety)
        if lost and not safety and wait > 0:
            self.note(f"compte perdu sur 60 j, mais nuit de sécurité déjà faite récemment : prochaine possible dans {wait} nuit(s)")
        for r in dec.table:
            log.info("  %-34s 30 j : ✅ %d/%d %+.2f € | 60 j : 💀 %d DD %.0f %% %+.2f € %s", r.name, r.won30, r.n30, r.net30,
                     r.lost60, r.dd60, r.net60, "sûr" if r.safe else "NON SÛR")
        applied: dict = {}
        lines = [f"🤖 <b>Optimiseur · {esc(gtitle)}</b> · " + (f"série {self.series_i}/{self.series_n}" if self.series_active
                                                              else f"nuit du {self._now().strftime('%d/%m')}"),
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
            lines.append(f"   appliqué à {min(oks)}/{len(running)} compte(s)" + f" + preset ⭐ {esc(self.work_preset())}")
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

    async def _backtest(self, ref: str, vs: list, title: str, override: dict[str, str] | None = None):
        """Backtest de 60 j sur le terminal de test : réglages du preset `ref` (+ `override`), `vs` en séries virtuelles. → (run, rid, bparams, devise) ou message d'erreur (str)."""
        d_from, d_to = bt.date_range(optim.SAFETY_DAYS)
        inputs = await self.r._bt_inputs(f"preset:{ref}")      # Bot 3.5.0 : réglages du preset ⭐, pas d'un compte
        if isinstance(inputs, str):
            self.note(f"backtest impossible : {inputs}")
            return f"backtest impossible : {inputs}"
        bparams, label, deposit, currency, leverage, comm, slip = inputs
        if override:
            bparams = {**bparams, **override}
        # Bot 3.2.3 (EA 7.8.3+) : le robot du testeur ne trade pas, seules les séries virtuelles (dont « miroir » = réglages
        # actuels) servent à décider : un gap qui vidait son compte faisait arrêter le test par MT5 avant la fin.
        bparams = {**bparams, "bt_labo_seul": "1"}
        rid = self.ctx.db.bt_create("opt", d_from, d_to, "reel", f"{title} · {label}")
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
            return run.error or "erreur inconnue"
        return run, rid, bparams, currency

    async def _apply(self, insts, key: str, raw: str) -> int:
        """Réglage appliqué à tous les comptes lancés + preset de travail ⭐ (ex. « Martin », jamais le preset de base) + .set
        de BASE (nouveaux comptes)."""
        res = await asyncio.gather(*[act.send_cmd(self.ctx, i, "SET", [key, raw]) for i in insts], return_exceptions=True)
        ok = 0
        for i, r in zip(insts, res):
            good = not isinstance(r, Exception) and getattr(r, "ok", False)
            ok += 1 if good else 0
            if not good:
                log.warning("SET %s=%s → %s : %s", key, raw, i.label, r if isinstance(r, Exception) else getattr(r, "message", "?"))
        store = self.r.dash.store
        work = self.work_preset()
        if work:
            store.set_value(work, key, raw)
            try:
                self.r.sync_base_set()
            except Exception:
                log.exception("preset ⭐ → .set de BASE")
        return ok

    # ---- Bot 3.3.0 : ♻️ réinitialisation (cycle + preset de base d'avant la 1re optimisation)
    BASE_PRESET = "🧩 Base avant optimisation"
    NEVER_RESET = ("sl_cont",)                               # choix de l'utilisateur, jamais touché par l'optimiseur

    def initial_values(self) -> dict[str, str]:
        """Valeur de chaque réglage AVANT le premier changement de l'optimiseur (historique des nuits)."""
        out: dict[str, str] = {}
        for r in self.ctx.db.opt_history():
            try:
                ap = json.loads(r["applied"] or "{}")
            except ValueError:
                continue
            if r["grp"] == "reset":                           # une réinitialisation n'est pas une optimisation
                continue
            for k, ov in ap.items():
                if k not in out and isinstance(ov, (list, tuple)) and len(ov) == 2 and not k.startswith("ct_"):
                    out[k] = str(ov[0])
        return {k: v for k, v in out.items() if k not in self.NEVER_RESET}

    def work_preset(self) -> str:
        """Bot 3.4.0 : preset que l'optimiseur modifie et applique (ex. « Martin »). Jamais le preset de base : si c'est lui
        qui est ⭐, on reprend le preset de travail mémorisé (ou le 1er autre preset) et on le remet ⭐ (→ nouveaux comptes)."""
        store = self.r.dash.store
        skip = (self.BASE_PRESET, ps.RECOMMENDED_NAME)
        if store.active and store.active != self.BASE_PRESET:
            name = store.active
        else:
            name = self.ctx.db.meta_get("opt_work", "") or ""
            if name not in store.presets or name == self.BASE_PRESET:
                name = next((n for n in store.names() if n not in skip), "")
            if not name:
                if not store.presets:
                    return ""
                name = "Martin"
                store.put(name, dict(store.presets.get(self.BASE_PRESET, {})))
            store.set_active(name)
            log.info("preset de travail de l'optimiseur : %s (remis ⭐)", name)
        if self.ctx.db.meta_get("opt_work", "") != name:
            self.ctx.db.meta_set("opt_work", name)
        return name

    def base_preset(self) -> dict[str, str]:
        """Preset « 🧩 Base avant optimisation » : créé la 1re fois (preset ⭐ actif + valeurs d'avant l'optimiseur), puis
        réutilisé tel quel à chaque réinitialisation."""
        store = self.r.dash.store
        if self.BASE_PRESET not in store.presets:
            work = self.work_preset()
            base = dict(store.presets.get(work, {})) if work else {}
            base.update(self.initial_values())
            store.put(self.BASE_PRESET, base)
            log.info("preset de base créé : %d réglage(s)", len(base))
        return dict(store.presets[self.BASE_PRESET])

    def reset_cycle(self) -> None:
        for k, v in (("opt_grp", "0"), ("opt_scale", "{}"), ("opt_idle", "{}"), ("opt_safety", "0"), ("opt_safety_wait", "0")):
            self.ctx.db.meta_set(k, v)

    async def reset(self) -> str:
        if self.running:
            return "⏳ Optimisation en cours : réessaie quand elle est finie."
        base = {k: v for k, v in self.base_preset().items()
                if k not in self.NEVER_RESET and not k.startswith("ct_") and k in ps.BY_KEY}
        running = [i for i in self.ctx.db.list() if self.ctx.mgr.is_running(i)]
        store = self.r.dash.store
        work = self.work_preset()
        cur = dict(store.presets.get(work, {})) if work else {}   # Bot 3.5.0 : Martin fait foi
        diff = {k: (cur.get(k, ""), v) for k, v in base.items() if cur.get(k) != v}
        self.reset_cycle()
        if work:                                              # le preset de travail (⭐) reprend TOUTES les valeurs de base
            store.put(work, {**store.presets.get(work, {}), **base})
            store.set_active(work)
            try:
                self.r.sync_base_set()
            except Exception:
                log.exception("preset ⭐ → .set de BASE")
        done = []
        for k, (o, n) in diff.items():
            ok = await self._apply(running, k, n)
            name = optim.label_of(k, n)
            name = name[:-len(optim.value_text(k, n)) - 3] if name.endswith(" : " + optim.value_text(k, n)) else name
            done.append(f"{name} : {optim.value_text(k, o) if o else '?'} → {optim.value_text(k, n)}"
                        f" ({ok}/{len(running)})")
        summary = "♻️ Réinitialisation : cycle remis à zéro" + (" ; " + " ; ".join(done) if done else " ; réglages déjà à la base")
        self.ctx.db.opt_add("reset", "change" if diff else "inchange", summary,
                            json.dumps({k: [o, n] for k, (o, n) in diff.items() if o}), None)
        self.note(summary)
        lines = ["♻️ <b>Optimiseur réinitialisé</b>",
                 f"🔄 Cycle remis à zéro : prochaine nuit {esc(optim.GROUPS[0][1])}, recherche normale, pas de nuit 🛟 en attente.",
                 f"🧩 Preset de base (jamais modifié) : <b>{esc(self.BASE_PRESET)}</b>",
                 f"⭐ Preset de travail remis à la base et appliqué : <b>{esc(work or '—')}</b> (nouveaux comptes compris)"]
        if done:
            lines += [f"Réglages remis ({len(done)}) :"] + [f"• {esc(d_)}" for d_ in done]
        else:
            lines.append("Réglages : déjà à la base, rien à changer.")
        lines.append("<i>Coupures et mode continu non modifiés. ↩️ Annuler possible depuis 🤖 Optimiseur.</i>")
        return "\n".join(lines)

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
        if self.series_active:
            lines.append(f"🔁 <b>Série en cours : optimisation {self.series_i}/{self.series_n}</b>"
                         + (" · ⏹ arrêt demandé (après celle-ci)" if self.series_stop else ""))
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

<b>Ce qu'il fait</b> : chaque nuit à <b>21 h</b> (réglable), il rejoue les <b>60 derniers jours</b> dans le testeur avec tes réglages actuels et jusqu'à 38 variantes (des robots fictifs joués en parallèle sur les mêmes prix). Pas de limite de durée : le test va jusqu'au bout, même après 2 h. Une <b>catégorie</b> par nuit, 4 nuits à la suite :
🧱 <b>Grille et lots</b> (ATR de la grille, minimum, maximum · ordres en attente, multiplicateur de lot, trades avant multiplication)
📐 <b>Signal et TP</b> (EMA rapide, EMA lente, période ATR · multiplicateur ATR du TP, TP minimum, TP maximum)
🛡️ <b>Filtres</b> (chacun activé ou désactivé · ATR min/max, volume min/max, extension max, corps de bougie, bougies du signal récent)
🎯 <b>Objectif et créneaux</b> (% de l'objectif · créneaux)
Le <b>mode continu</b> n'est jamais modifié par l'optimiseur : c'est toi qui le choisis.

<b>Comment il choisit</b>
• Il ne garde qu'une variante qui <b>ne perd jamais le compte</b> sur 60 jours et 30 jours, avec un drawdown sous 90 %.
• Si tes réglages actuels sont sûrs, la variante doit <b>gagner nettement plus sur les 30 derniers jours</b> sans faire moins bien sur 60 jours.
• À résultat presque égal, il préfère la valeur <b>entourée de bons résultats</b> (ses voisines gagnent aussi) : plus robuste quand le marché change.
• Si tes réglages actuels <b>perdent vraiment le compte</b> (💀) sur 60 jours, la nuit suivante est une nuit 🛟 <b>Sécurité</b> (au plus une par tour) : lot moins multiplié, moins d'ordres, grille plus large ; il prend la variante sûre la plus rentable si elle garde au moins <b>80 % de ton gain</b>.

<b>Les crans</b> : un cran est le pas d'un réglage (ex. 0,1 pour l'ATR de la grille). Chaque variante bouge un réglage d'<b>un cran</b>, seul ou combiné à un autre réglage de la même famille. La meilleure variante sûre est appliquée ; les nuits suivantes, il reteste autour de la nouvelle valeur. Après <b>3 passages sans changement</b> d'un réglage, il cherche <b>deux fois plus loin</b> (2 puis 4 crans), puis revient à 1 cran.

<b>Les créneaux</b> (nuit 🎯) : un créneau est <b>retiré</b> s'il dépasse <b>80 % de drawdown</b> sur 60 jours ; chaque <b>heure pile libre</b> est testée seule et <b>ajoutée</b> si elle reste sous 80 %, ne perd jamais le compte et gagne de l'argent. Il ne retire jamais le dernier créneau. Les coupures ne sont jamais touchées. En mode continu, les créneaux ne sont ni testés ni modifiés.

<b>Ce que tu reçois</b> : un message par nuit avec l'ancienne valeur, la nouvelle, la raison, les meilleures variantes et un bouton <b>↩️ Annuler ce changement</b>. Le réglage est appliqué à tous les comptes lancés et à ton preset ⭐ de travail (ex. Martin, utilisé aussi pour les nouveaux comptes). Le preset « 🧩 Base avant optimisation » n'est jamais modifié : il sert au ♻️ reset.

<b>Les boutons</b>
• ✅/⏸ : activer ou suspendre l'optimiseur.
• 🕐 : changer l'heure de lancement.
• ▶️ Lancer maintenant : une nuit tout de suite.
• 🔁 Série d'optimisations : 2 à 50 optimisations enchaînées dans l'ordre du cycle (la nuit est sautée pendant la série) ; ⏹ pour l'arrêter après celle en cours.
• 🔑 Terminal de test : le compte du broker utilisé par le testeur (démo ou réel, aucun ordre passé).
• 📜 Journal : tout ce qu'il a fait, ligne par ligne ; 📎 le fichier complet à me transmettre en cas de problème.

<b>Limites</b> : le passé ne garantit pas l'avenir ; un backtest ne connaît pas ton slippage exact (il reprend celui mesuré sur tes vrais ordres). Le SL d'urgence et les coupures ne sont jamais touchés."""
