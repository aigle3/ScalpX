"""Routeur : traduit callbacks et commandes texte en actions. Aucune dépendance à python-telegram-bot
(tout passe par l'interface `Sender`), donc testable sans Telegram."""
from __future__ import annotations

import asyncio
import logging
import re
import time

from core.service import Notice
from mt5 import base as basemod
from core.summary import FILTERS, SORTS
from mt5.deploy import plan_deploy
from . import actions as act
from . import render
from .charts import balance_chart, total_chart
from .dashboard import Dashboard
from .notify import Notifier
from .render import esc
from .reports import build_report
from . import params_ui as pu
from .ui import ALL_ID, Confirmations, Keyboard, Pending, Sender, resolve, short

log = logging.getLogger(__name__)


class Router:
    awaiting: tuple[str, int] | None = None

    def __init__(self, ctx: act.Ctx, sender: Sender, chat_id: int, dash: Dashboard, conf: Confirmations,
                 notifier: Notifier) -> None:
        self.ctx, self.sender, self.chat_id, self.dash, self.conf, self.notifier = ctx, sender, chat_id, dash, conf, notifier
        self._tasks: set[asyncio.Task] = set()
        self._dep_msg: int | None = None
        self.building = False
        self.awaiting_preset: str | None = None             # compte dont on attend le nom de preset (message texte)
        self.awaiting_goal: str | None = None               # compte (ou ALL_ID) dont on attend l'objectif (message texte)
        self._dep_sel: set[str] = set()

    def _bg(self, coro) -> None:
        """Lance une action longue sans bloquer la réception des mises à jour Telegram."""
        t = asyncio.get_event_loop().create_task(self._guard(coro))
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _guard(self, coro) -> None:
        try:
            await coro
        except Exception:
            log.exception("action en arrière-plan en erreur")

    # ------------------------------------------------------------------ callbacks
    async def on_callback(self, data: str, message_id: int = 0) -> str:
        """Traite un callback_data. Renvoie un court texte pour query.answer()."""
        parts = data.split(":")
        head = parts[0]
        try:
            if head == "d":
                return await self._dash_cb(parts, message_id)
            if head == "p":
                return await self._prm_cb(parts)
            if head == "x" and len(parts) > 1:
                await self.cmd_export(parts[1])
                return ""
            if head == "a":
                return await self._acc_cb(parts)
            if head == "c":
                return await self._cmd_cb(parts)
            if head in ("ok", "no"):
                return await self._confirm_cb(head, parts[1] if len(parts) > 1 else "")
            if head == "dep":
                return await self._dep_cb(parts)
            if head == "upd":
                return await self._upd_cb(parts)
            if head == "e":
                return await self._ea_cb(parts)
            if head == "g":
                return await self._goal_cb(parts)
        except Exception:
            log.exception("callback en erreur : %s", head)
            return "Erreur"
        return ""

    async def _dash_cb(self, p: list[str], message_id: int | None = None) -> str:
        sub = p[1] if len(p) > 1 else "r"
        if sub == "f" and len(p) > 2:
            self.dash.set_filter(p[2])
        elif sub == "s" and len(p) > 2:
            self.dash.set_sort(p[2])
        elif sub == "p" and len(p) > 2 and p[2].lstrip("-").isdigit():
            self.dash.set_page(int(p[2]))
        elif sub == "home":
            self.dash.goto("dash")
        elif sub == "gcmd":
            if act.target(self.ctx, ALL_ID) is None:
                self.dash.flash("Aucun terminal lancé.")
            else:
                self.dash.goto("gcmd")
        elif sub == "tall" and len(p) > 2 and p[2] in ("rst", "start", "stop"):
            verb = p[2]
            insts = self.ctx.db.list()
            on = [i for i in insts if self.ctx.mgr.is_running(i)]
            targets = [i for i in insts if i not in on] if verb == "start" else on
            if not targets:
                self.dash.flash("Tous les terminaux sont déjà lancés." if verb == "start" else "Aucun terminal lancé.")
            else:
                what = {"rst": "🔄 Redémarrer", "start": "🚀 Lancer", "stop": "🛑 Arrêter"}[verb]
                extra = "\nFermeture propre ; les positions restent ouvertes chez le broker." if verb != "start" else ""
                return await self._ask(f"<b>{what} {len(targets)} terminal(aux) ?</b>\n{esc(', '.join(i.label for i in targets[:15]))}{extra}",
                                       lambda: self._terminals(targets, verb), ("gterm",), what)
        elif sub == "flt":
            self.dash.goto("flt")
            self._bg(self._filters_fetch())
        elif sub == "pos":
            self._bg(self._positions_detail())
            return ""
        elif sub in ("lst", "act", "rpt", "logs", "gterm"):
            self.dash.goto(sub)
            self.dash.silent = self.notifier.silent_mode
        elif sub == "fc":                                   # filtre suivant
            i = FILTERS.index(self.dash.flt) if self.dash.flt in FILTERS else 0
            self.dash.set_filter(FILTERS[(i + 1) % len(FILTERS)])
        elif sub == "sc":                                   # tri suivant
            i = SORTS.index(self.dash.sort) if self.dash.sort in SORTS else 0
            self.dash.set_sort(SORTS[(i + 1) % len(SORTS)])
        elif sub == "chart":
            await self.cmd_courbe()
            return ""
        elif sub == "sys":
            self.dash.goto("sys")
        elif sub == "crash":
            from .system import crash_report
            text, has = crash_report(self.ctx)
            kb = [[("🗑 Effacer l'historique", "d:crashclr"), ("✖ Fermer", "d:close")]] if has else [[("✖ Fermer", "d:close")]]
            await self.sender.send(self.chat_id, text, kb, True)
            return ""
        elif sub == "crashclr":
            from .system import log_path
            try:
                log_path(self.ctx, "crash.log").unlink()
            except OSError:
                pass
            if message_id:
                await self.sender.edit(self.chat_id, message_id, "💥 Historique des plantages effacé.", [[("✖ Fermer", "d:close")]])
            return ""
        elif sub in ("log", "clog"):
            from .system import log_path, tail
            name = {"log": "bot.log", "clog": "compile.log"}[sub]
            txt = tail(log_path(self.ctx, name), 40 if sub == "clog" else 25)
            await self.sender.send(self.chat_id, f"📜 <b>{name}</b>\n" + (f"<pre>{esc(txt)}</pre>" if txt else "<i>vide ou absent</i>"),
                                   [[("✖ Fermer", "d:close")]], True)
            return ""
        elif sub == "close" and message_id:
            await self.sender.delete(self.chat_id, message_id)
            return ""
        elif sub == "upd":
            self.dash.flash("⏳ Vérification sur GitHub…")
            await self.dash.refresh(force=True)
            self._bg(self.check_updates_now())
            return "Vérification…"
        elif sub == "win":
            procs = self.ctx.mgr.procs
            insts = [i for i in self.ctx.db.list() if self.ctx.mgr.is_running(i)]
            if hasattr(procs, "rearrange"):
                for i in insts:
                    procs.rearrange(self.ctx.mgr.exe(i), settle=False)
            self.dash.flash(f"🪟 {len(insts)} terminal(aux) : panneaux masqués, graphique agrandi, fenêtre réduite.")
        elif sub == "rst":
            return await self._ask("🔄 <b>Redémarrer le bot ?</b>\nEnviron 30 s sans bot. Les terminaux MT5 et l'EA continuent de trader.",
                                   self.restart_bot, ("sys",), "🔄 Redémarrer")
        elif sub in ("pall", "rall"):
            cmd = "PAUSE" if sub == "pall" else "REPRISE"
            insts = [v.inst for v in self.ctx.views() if self.ctx.mgr.is_running(v.inst)]
            if not insts:
                self.dash.flash("Aucun terminal lancé.")
            else:
                what = "mettre en PAUSE" if cmd == "PAUSE" else "faire REPRENDRE"
                return await self._ask(f"{'⏸' if cmd == 'PAUSE' else '▶'} <b>{what} {len(insts)} compte(s) ?</b>\n"
                                       + esc(", ".join(i.label for i in insts[:15])),
                                       lambda: self._group(insts, cmd), ("dash",), "✅ Confirmer")
        elif sub == "rbk":
            from core import update as upd
            from .system import BOT_ROOT
            prev = upd.backup_version(BOT_ROOT)
            if not prev:
                self.dash.flash("Aucune version précédente enregistrée.")
            else:
                from core.version import bot_version
                return await self._ask(f"⏪ <b>Revenir au bot {esc(prev)} ?</b>\nVersion actuelle : {esc(bot_version())}. "
                                       "Le bot redémarre ; les terminaux continuent. La mise à jour restera disponible (🔔).",
                                       self.rollback_bot, ("sys",), "⏪ Revenir")
        elif sub == "bld":
            if self.building or self.ctx.deploy.running:
                return "Compilation ou déploiement déjà en cours"
            self._bg(self.run_manual_build())
            return "Compilation lancée"
        elif sub == "dep":
            await self.cmd_deployer()
            return ""
        elif sub == "rep":
            await self.cmd_rapport()
            return ""
        elif sub == "png":
            await self.cmd_ping()
            return ""
        elif sub == "sil":
            await self.cmd_silencieux("off" if self.notifier.silent_mode else "on")
            self.dash.silent = self.notifier.silent_mode
        elif sub == "exp":
            tables = ("balances", "events", "closures500", "withdrawals", "cycles")
            await self.sender.send(self.chat_id, "📤 <b>Export CSV</b> — quelle table ?",
                                   [[(t, f"x:{t}") for t in tables[:3]], [(t, f"x:{t}") for t in tables[3:]]], True)
            return ""
        await self.dash.refresh(force=True)
        return ""

    # ------------------------------------------------------------------ paramètres de l'EA (boutons)
    def _target(self, code: str):
        """Code court d'un compte, ou « all » : tous les comptes dont le terminal est lancé."""
        return act.target(self.ctx, ALL_ID) if code == "all" else resolve(self.ctx.db, code)

    async def _prm_cb(self, p: list[str]) -> str:
        inst = self._target(p[1]) if len(p) > 1 else None
        if inst is None:
            self.dash.flash("Aucun terminal lancé." if len(p) > 1 and p[1] == "all" else "Compte introuvable.")
            self.dash.goto("dash")
            await self.dash.refresh(force=True)
            return "Compte introuvable"
        ui = self.dash.params
        sub = p[2] if len(p) > 2 else ""
        self.awaiting = None
        if sub == "" or sub == "r":
            self.dash.goto("prm", inst.id)
            if sub == "r" or not ui.fresh(inst.id):
                self.dash.flash("⏳ Lecture des paramètres…")
                self._bg(self._prm_fetch(inst))
        elif sub[0] == "g" and sub[1:].isdigit():
            self.dash.goto("prg", inst.id, int(sub[1:]))
        elif sub[0] == "k" and sub[1:].isdigit():
            self.dash.goto("prp", inst.id, int(sub[1:]))
        elif sub[0] == "t" and sub[1:].isdigit():                      # bascule oui/non
            idx = int(sub[1:])
            cur = pu.bool_of(ui.cached(inst.id).get(pu.cat.PARAMS[idx][0], ""))
            if cur is None:
                self.dash.flash("⚠️ Valeur inconnue : touche 🔃 Relire.")
            else:
                self._bg(self._prm_apply(inst, idx, "0" if cur else "1"))
        elif sub[0] == "s" and sub[1:].isdigit() and len(p) > 3:       # − / + n pas
            idx = int(sub[1:])
            prm = pu.cat.PARAMS[idx]
            cur = pu.num_of(ui.cached(inst.id).get(prm[0], ""))
            try:
                n = int(p[3])
            except ValueError:
                n = 0
            if cur is None or not n:
                self.dash.flash("⚠️ Valeur inconnue : touche 🔃 Relire.")
            else:
                self._bg(self._prm_apply(inst, idx, pu.fmt_num(prm, cur + n * prm[6])))
        elif sub[0] == "e" and sub[1:].isdigit() and len(p) > 3 and p[3].isdigit():
            self._bg(self._prm_apply(inst, int(sub[1:]), p[3]))
        elif sub[0] == "w" and sub[1:].isdigit():                      # saisie libre : le prochain message texte est la valeur
            idx = int(sub[1:])
            self.awaiting = (inst.id, idx)
            self.dash.goto("prp", inst.id, idx)
            self.dash.flash("✏️ Envoie maintenant la nouvelle valeur (un nombre).")
        await self.dash.refresh(force=True)
        return ""

    async def _prm_fetch(self, inst) -> None:
        ok = await self.dash.params.fetch(inst)
        self.dash.flash("" if ok else f"❌ Lecture impossible : {self.dash.params.last_error}.", 25.0 if not ok else 0.1)
        await self.dash.refresh(force=True)

    async def _prm_apply(self, inst, idx: int, value: str) -> None:
        self.dash.flash(f"⏳ {pu.cat.PARAMS[idx][0]} = {value}…")
        await self.dash.refresh(force=True)
        self.dash.flash(await self.dash.params.set_value(inst, idx, value))
        await self.dash.refresh(force=True)

    async def on_text(self, text: str) -> None:
        """Message texte libre : valeur attendue d'un paramètre, sinon le tableau de bord revient en bas."""
        if self.awaiting_goal:
            iid, self.awaiting_goal = self.awaiting_goal, None
            inst = act.target(self.ctx, iid)
            v = text.strip().replace(" ", "")
            if inst is None or not self.GOAL_RE.match(v):
                self.dash.flash("❌ Objectif non reconnu : 10, +3% ou =1250.")
                await self.dash.refresh(force=True)
                return
            await self._goal_confirm(inst, v.replace(",", "."))
            return
        if self.awaiting_preset:
            iid, self.awaiting_preset = self.awaiting_preset, None
            inst = act.target(self.ctx, iid)
            name = text.strip()
            if inst is None or not re.fullmatch(r"[A-Za-z0-9_-]{1,30}", name):
                self.dash.flash("❌ Nom invalide : lettres, chiffres, - et _ (30 max).")
                await self.dash.refresh(force=True)
                return
            self._bg(self._ea_run(inst, "PRESET_SAVE", [name], then_presets=True))
            return
        if self.awaiting:
            iid, idx = self.awaiting
            self.awaiting = None
            inst = act.target(self.ctx, iid)
            prm = pu.cat.PARAMS[idx]
            num = pu.num_of(text)
            if inst is None or num is None or prm[3] not in ("int", "dbl"):
                self.dash.flash("❌ Valeur non reconnue : envoie un nombre.")
                await self.dash.refresh(force=True)
                return
            self._bg(self._prm_apply(inst, idx, pu.fmt_num(prm, num)))
            return
        await self.cmd_start()

    async def _acc_cb(self, p: list[str]) -> str:
        inst = resolve(self.ctx.db, p[1]) if len(p) > 1 else None
        if inst is None:
            self.dash.goto("dash")
            await self.dash.refresh(force=True)
            return "Compte introuvable"
        verb = p[2] if len(p) > 2 else "open"
        ctx, dash = self.ctx, self.dash
        if verb in ("open", "refresh"):
            dash.goto("acc", inst.id)
        elif verb == "cmds":
            dash.goto("cmds", inst.id)
        elif verb == "term":
            dash.goto("term", inst.id)
        elif verb == "pause":
            self._bg(self._run_cmd_flash(inst, "PAUSE"))
        elif verb == "resume":
            self._bg(self._run_cmd_flash(inst, "REPRISE"))
        elif verb == "start":
            dash.flash(await act.action_start(ctx, inst))
        elif verb == "restart":
            dash.flash("⏳ Redémarrage…")
            self._bg(self._long(inst, act.action_restart(ctx, inst)))
        elif verb == "chart":
            self._bg(self._send_chart(inst))
            dash.flash("⏳ Graphique en préparation…")
        elif verb == "stop":
            return await self._ask(f"🛑 <b>Arrêter {esc(inst.label)} ?</b>\nFermeture propre du terminal ; les positions restent ouvertes chez le broker.\n"
                                   "Si une clôture à 500 est en cours, l'arrêt attend sa fin.",
                                   lambda: self._long(inst, act.action_stop(ctx, inst)), ("acc", inst.id), "🛑 Arrêter")
        elif verb == "delete":
            return await self._ask(f"🗑 <b>Supprimer {esc(inst.label)} ?</b>\nArrêt du terminal puis archivage du dossier (pas de suppression brute).",
                                   lambda: self._long(inst, act.action_delete(ctx, inst), home=True), ("acc", inst.id), "🗑 Supprimer")
        elif verb == "update":
            return await self._deploy_single(inst)
        elif verb == "recon":                                     # normalement capté par l'assistant (ConversationHandler)
            dash.flash("🔑 Utilise le bouton Reconnecter pour lancer l'assistant.")
        await dash.refresh(force=True)
        return ""

    async def _ask(self, text: str, run, back: tuple, yes: str = "✅ Confirmer") -> str:
        tok = self.conf.ask(Pending(text, run, back[0]))
        self.dash.goto("confirm", tok)
        self.dash.confirm_back = back
        await self.dash.refresh(force=True)
        return ""

    async def _confirm_cb(self, kind: str, tok: str) -> str:
        back = self.dash.confirm_back
        p = self.conf.take(tok)
        if kind == "no" or p is None:
            self.dash.goto(*back)
            await self.dash.refresh(force=True)
            return "Annulé" if kind == "no" else "Expiré"
        self.dash.goto(*back)
        self._bg(p.run())
        await self.dash.refresh(force=True)
        return "OK"

    async def _long(self, inst, coro, home: bool = False) -> None:
        self.dash.flash("⏳ En cours…")
        await self.dash.refresh(force=True)
        msg = await coro
        self.dash.flash(f"{esc(inst.label)} : {msg}")
        if home:
            self.dash.goto("dash")
        await self.dash.refresh(force=True)

    async def _run_cmd_flash(self, inst, cmd: str, args: list[str] | None = None) -> None:
        """⏳ envoyé → ✅ exécuté / ❌ échec selon l'ack (avec timeout)."""
        self.dash.flash(f"⏳ {cmd} envoyé à {esc(inst.label)}…")
        await self.dash.refresh(force=True)
        r = await act.send_cmd(self.ctx, inst, cmd, args)
        icon = "✅" if r.ok else "❌"
        self.dash.flash(f"{icon} {cmd} · {esc(inst.label)} : {esc(r.message[:160])}")
        await self.dash.refresh(force=True)

    async def _cmd_cb(self, p: list[str]) -> str:
        inst = resolve(self.ctx.db, p[1]) if len(p) > 1 else None
        if inst is None or len(p) < 3 or not p[2].isdigit() or int(p[2]) >= len(act.CARD_CMDS):
            return "Commande invalide"
        _, cmd = act.CARD_CMDS[int(p[2])]
        self._bg(self._cmd_with_output(inst, cmd))
        return ""

    async def _filters_fetch(self) -> None:
        """FILTRES lu sur un seul robot (tous ont les mêmes filtres et le même symbole)."""
        g = act.target(self.ctx, ALL_ID)
        if g is None:
            self.dash.flash("Aucun terminal lancé : filtres indisponibles.")
            await self.dash.refresh(force=True)
            return
        r = await act.send_cmd(self.ctx, g.ref, "FILTRES")
        if r.timeout or not r.message:
            self.dash.flash("❌ Filtres : EA injoignable.")
        else:
            self.dash.filters = (time.time(), g.ref.label, r.message)
        await self.dash.refresh(force=True)

    async def _positions_detail(self) -> None:
        """Détail des positions (commande POSITIONS) des comptes qui en ont, en un seul message."""
        views = [v for v in self.ctx.views() if self.ctx.mgr.is_running(v.inst) and v.st and (v.st.positions or v.st.ordres)]
        if not views:
            await self.sender.send(self.chat_id, "📋 Aucune position ni ordre en cours.", [[("✖ Fermer", "d:close")]], True)
            return
        res = await asyncio.gather(*[act.send_cmd(self.ctx, v.inst, "POSITIONS") for v in views])
        parts = [f"<b>{esc(v.inst.label)}</b>\n{esc((r.message or '—').strip())[:1500]}" for v, r in zip(views, res)]
        await self.sender.send(self.chat_id, "📋 <b>Positions</b>\n\n" + "\n\n".join(parts)[:3900], [[("✖ Fermer", "d:close")]], True)

    async def _terminals(self, insts, verb: str) -> None:
        """Redémarrer / lancer / arrêter plusieurs terminaux en parallèle ; un seul message de résultat."""
        label = {"rst": "Redémarrage", "start": "Lancement", "stop": "Arrêt"}[verb]
        mid = await self.sender.send(self.chat_id, f"⏳ <b>{label}</b> de {len(insts)} terminal(aux)…", None, True)
        fn = {"rst": act.action_restart, "start": act.action_start, "stop": act.action_stop}[verb]

        async def one(i):
            try:
                return i, await fn(self.ctx, i)
            except Exception as exc:
                return i, f"❌ {exc}"

        res = await asyncio.gather(*[one(i) for i in insts])
        lines = [f"<b>{esc(i.label)}</b> : {esc(str(r))[:150]}" for i, r in res]
        await self.sender.edit(self.chat_id, mid, f"🖥 <b>{label}</b>\n" + "\n".join(lines), [[("✖ Fermer", "d:close")]])
        await self.dash.refresh(force=True)

    async def _group(self, insts, cmd: str) -> None:
        """Même commande sur plusieurs comptes, en parallèle ; un seul message de résultat."""
        mid = await self.sender.send(self.chat_id, f"⏳ <b>{cmd}</b> sur {len(insts)} compte(s)…", None, True)
        res = await asyncio.gather(*[act.send_cmd(self.ctx, i, cmd) for i in insts])
        lines = [f"{'✅' if r.ok else '❌'} <b>{esc(i.label)}</b> : {esc((r.message or '').splitlines()[0][:120] if r.message else '')}"
                 for i, r in zip(insts, res)]
        await self.sender.edit(self.chat_id, mid, f"<b>{cmd}</b>\n" + "\n".join(lines), [[("✖ Fermer", "d:close")]])
        await self.dash.refresh(force=True)

    async def _ea_cb(self, p: list[str]) -> str:
        """Actions EA de la fiche (🧰) : vacances, presets, valider, annuler l'objectif, tout fermer."""
        inst = self._target(p[1]) if len(p) > 1 else None
        if inst is None or len(p) < 3:
            self.dash.flash("Aucun terminal lancé." if len(p) > 1 and p[1] == "all" else "Compte introuvable.")
            await self.dash.refresh(force=True)
            return "Compte introuvable"
        sub, lab = p[2], esc(inst.label)
        back_c = ("gcmd",) if inst.id == ALL_ID else ("cmds", inst.id)
        back_p = ("pst", inst.id)
        names = self.dash.presets.get(inst.id) or []
        idx = int(p[3]) if len(p) > 3 and p[3].isdigit() else -1
        name = names[idx] if 0 <= idx < len(names) else ""
        if sub == "von":
            return await self._ask(f"🏖 <b>Mode vacances sur {lab} ?</b>\nAucune nouvelle série ; les positions ouvertes restent gérées jusqu'au TP.",
                                   lambda: self._ea_run(inst, "VACANCES", ["on"]), back_c, "🏖 Activer")
        if sub == "voff":
            self._bg(self._ea_run(inst, "VACANCES", ["off"]))
        elif sub == "val":
            self._bg(self._ea_run(inst, "VALIDER", []))
        elif sub == "gx":
            return await self._ask(f"🎯 <b>Annuler l'objectif en cours sur {lab} ?</b>",
                                   lambda: self._ea_run(inst, "ANNULER_OBJECTIF", []), back_c, "🎯 Annuler l'objectif")
        elif sub == "fx":
            return await self._ask(f"🛑 <b>TOUT FERMER sur {lab} ?</b>\nToutes les positions et ordres sont fermés maintenant, "
                                   "puis le robot passe en PAUSE.", lambda: self._ea_run(inst, "FERMER_TOUT", []), back_c, "🛑 Tout fermer")
        elif sub == "ps":
            self.dash.goto("pst", inst.id)
            self._bg(self._presets_fetch(inst))
        elif sub == "pl" and name:
            return await self._ask(f"📥 <b>Charger le preset « {esc(name)} » sur {lab} ?</b>\nLes réglages actuels sont remplacés ; "
                                   "valide-les ensuite (✅ Valider les réglages).",
                                   lambda: self._ea_run(inst, "PRESET_LOAD", [name], then_presets=True), back_p, "📥 Charger")
        elif sub == "pd" and name:
            return await self._ask(f"🗑 <b>Supprimer le preset « {esc(name)} » ?</b>",
                                   lambda: self._ea_run(inst, "PRESET_DELETE", [name], then_presets=True), back_p, "🗑 Supprimer")
        elif sub == "pn":
            self.awaiting_preset = inst.id
            self.dash.flash("✏️ Envoie maintenant le nom du preset (lettres, chiffres, - et _).")
        elif sub == "rs":
            return await self._ask(f"♻️ <b>Remettre les valeurs par défaut sur {lab} ?</b>\nTous les réglages reviennent aux valeurs de l'EA ; "
                                   "valide-les ensuite.", lambda: self._ea_run(inst, "RESET_PARAMS", []), back_p, "♻️ Rétablir")
        else:
            return "Action inconnue"
        await self.dash.refresh(force=True)
        return ""

    GOAL_RE = re.compile(r"^(\+?\d+(?:[.,]\d+)?\s*%|=\s*\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)$")

    async def _goal_cb(self, p: list[str]) -> str:
        """🎯 Objectif immédiat (hors créneaux) : OBJECTIF <valeur> puis DEMARRER, sur un compte ou sur tous."""
        inst = self._target(p[1]) if len(p) > 1 else None
        if inst is None:
            self.dash.flash("Aucun terminal lancé." if len(p) > 1 and p[1] == "all" else "Compte introuvable.")
            await self.dash.refresh(force=True)
            return ""
        sub = p[2] if len(p) > 2 else ""
        if sub == "p" and len(p) > 3 and re.fullmatch(r"\d+(\.\d+)?", p[3]):
            return await self._goal_confirm(inst, f"+{p[3]}%")
        if sub == "w":
            self.awaiting_goal = inst.id
            self.dash.flash("✏️ Envoie l'objectif : 10 (= +10 €), +3% ou =1250 (solde cible).")
        self.dash.goto("obj", inst.id)
        await self.dash.refresh(force=True)
        return ""

    async def _goal_confirm(self, inst, value: str) -> str:
        what = (f"solde cible {value[1:]}" if value.startswith("=") else value if "%" in value else f"+{value} €")
        return await self._ask(f"🎯 <b>Lancer maintenant un objectif {esc(what)} sur {esc(inst.label)} ?</b>\n"
                               "Hors créneau horaire : le robot démarre tout de suite et s'arrête à l'objectif.",
                               lambda: self._goal_run(inst, value), ("obj", inst.id), "🎯 Lancer")

    async def _goal_run(self, inst, value: str) -> None:
        members = getattr(inst, "members", None) or [inst]
        self.dash.flash(f"⏳ Objectif {value} envoyé à {len(members)} compte(s)…")
        await self.dash.refresh(force=True)

        async def one(m):
            r = await act.send_cmd(self.ctx, m, "OBJECTIF", [value])
            if not r.ok:
                return m, r
            return m, await act.send_cmd(self.ctx, m, "DEMARRER")

        res = await asyncio.gather(*[one(m) for m in members])
        lines = [f"{'✅' if r.ok else '❌'} <b>{esc(m.label)}</b> : {esc((r.message or '').strip().splitlines()[0][:150] if r.message else '')}"
                 for m, r in res]
        ok = sum(1 for _, r in res if r.ok)
        await self.sender.send(self.chat_id, f"🎯 <b>Objectif {esc(value)}</b> : {ok}/{len(members)} lancé(s)\n" + "\n".join(lines),
                               [[("✖ Fermer", "d:close")]], True)
        self.dash.flash(f"🎯 Objectif lancé sur {ok}/{len(members)} compte(s)")
        await self.dash.refresh(force=True)

    async def _presets_fetch(self, inst) -> None:
        self.dash.presets.pop(inst.id, None)
        await self.dash.refresh(force=True)
        r = await act.send_cmd(self.ctx, getattr(inst, "ref", inst), "PRESETS")         # groupe : presets du 1er compte
        if r.timeout:
            self.dash.flash("❌ EA injoignable (terminal arrêté ?).")
            self.dash.presets[inst.id] = []
        else:
            self.dash.presets[inst.id] = [m.group(1) for m in re.finditer(r"(?m)^\s*•\s*([A-Za-z0-9_-]{1,30})\s*$", r.message or "")]
        await self.dash.refresh(force=True)

    async def _ea_run(self, inst, cmd: str, args: list[str], then_presets: bool = False) -> None:
        self.dash.flash(f"⏳ {cmd} envoyé…")
        await self.dash.refresh(force=True)
        members = getattr(inst, "members", None)
        if members is not None:                                         # tous les comptes : un résumé
            res = await asyncio.gather(*[act.send_cmd(self.ctx, m, cmd, args) for m in members])
            bad = [m.label for m, r in zip(members, res) if not r.ok]
            self.dash.flash(f"✅ {cmd} : {len(members)} compte(s)" if not bad else
                            f"⚠️ {cmd} : {len(members) - len(bad)}/{len(members)} ; échec : {esc(', '.join(bad[:5]))}")
        else:
            r = await act.send_cmd(self.ctx, inst, cmd, args)
            first = (r.message or "").strip().splitlines()[0][:200] if r.message else ""
            self.dash.flash(("✅ " if r.ok else "❌ ") + esc(first or cmd))
        if then_presets:
            await self._presets_fetch(inst)
        else:
            await self.dash.refresh(force=True)

    async def _cmd_with_output(self, inst, cmd: str) -> None:
        """Commande EA depuis la fiche : le texte renvoyé par l'EA (ex-réponse Telegram) est envoyé tel quel."""
        self.dash.flash(f"⏳ {cmd} envoyé…")
        await self.dash.refresh(force=True)
        r = await act.send_cmd(self.ctx, inst, cmd)
        self.dash.flash(f"{'✅' if r.ok else '❌'} {cmd} · {esc(inst.label)}")
        await self.dash.refresh(force=True)
        if r.message:
            await self.sender.send(self.chat_id, f"<b>{esc(inst.label)} · {cmd}</b>\n{esc(r.message)[:3800]}", None, True)

    async def _send_chart(self, inst) -> None:
        rows = self.ctx.db.balances(inst.id, int(time.time()) - 7 * 86400)
        v = self.ctx.view_of(inst.id)
        s_a = v.st.seuil_arret if v and v.st else 500.0
        s_r = v.st.seuil_reprise if v and v.st else 125.0
        png = balance_chart(rows, f"{inst.label} — solde / equity (7 j)", s_a, s_r)
        if png is None:
            self.dash.flash("📈 Pas encore assez de points d'historique.")
        else:
            await self.sender.photo(self.chat_id, png, f"{inst.label} — 7 derniers jours", True)
            self.dash.flash("📈 Graphique envoyé.")
        await self.dash.refresh(force=True)

    # ------------------------------------------------------------------ actions du tableau de bord
    async def cmd_start(self) -> None:
        """Tout message reçu (bouton « 📊 Tableau de bord », texte libre) : le tableau de bord revient en bas de la conversation."""
        await self.dash.bring_down()

    async def cmd_ping(self) -> None:
        views = self.ctx.views()
        alive = sum(1 for v in views if v.health.icon == "🟢")
        await self.sender.send(self.chat_id, f"🏓 pong · bot actif depuis {render.ago(time.time() - self.ctx.started_at)} · "
                                             f"{alive}/{len(views)} comptes 🟢", None, True)

    async def cmd_rapport(self) -> None:
        text = build_report(self.ctx, time.time() - 86400)
        await self.sender.send(self.chat_id, text, [[("✖ Fermer", "d:close")]], True)

    async def cmd_courbe(self) -> None:
        series = {v.inst.label: self.ctx.db.balances(v.inst.id, int(time.time()) - 7 * 86400) for v in self.ctx.views()}
        png = total_chart(series, "Soldes — 7 derniers jours")
        if png:
            await self.sender.photo(self.chat_id, png, "Historique des soldes", True)
        else:
            self.dash.flash("📈 Pas encore assez d'historique pour la courbe.")
            await self.dash.refresh(force=True)

    async def cmd_export(self, table: str) -> None:
        try:
            csv_text = self.ctx.db.export_csv(table, int(time.time()) - self.ctx.cfg.reports.history_days * 86400)
        except ValueError:
            await self.sender.send(self.chat_id, "Tables : balances, events, closures500, withdrawals, cycles", None, True)
            return
        await self.sender.document(self.chat_id, csv_text.encode("utf-8"), f"scalpx_{table}.csv", f"Export {table}")

    async def cmd_silencieux(self, arg: str) -> None:
        if arg.lower() in ("on", "oui", "1"):
            self.notifier.silent_mode = True
        elif arg.lower() in ("off", "non", "0"):
            self.notifier.silent_mode = False
        await self.sender.send(self.chat_id, "🔕 Mode silencieux : " + ("activé (alertes critiques seulement avec son)" if self.notifier.silent_mode else "désactivé"), None, True)

    # ------------------------------------------------------------------ déploiement
    async def cmd_deployer(self) -> None:
        plan = plan_deploy(self.ctx.cfg, self.ctx.db)
        interrupted = self.ctx.deploy.interrupted()
        if interrupted and not self.ctx.deploy.running:
            await self.sender.send(self.chat_id, "⚠️ <b>Un déploiement a été interrompu.</b>", [[("▶ Reprendre", "dep:resume"), ("✖ Annuler le déploiement", "dep:cancel")]], False)
            return
        lines = [f"<b>🔄 Déploiement de l'EA</b>", f"Version de BASE : <b>{esc(plan.base_version or '?')}</b>", ""]
        for v in self.ctx.views():
            cur = v.st.version if v.st and v.st.version else v.inst.ea_version
            lines.append(f"{'⬆' if v.inst.id in plan.targets else '✅'} {esc(v.inst.label)} : v{esc(cur or '?')}")
        if plan.inputs_changed:
            lines.append(f"\n⚠️ Les inputs du .set ont changé ({esc(plan.inputs_diff)}). Les paramètres des comptes ne seront PAS modifiés.")
        if not plan.targets:
            lines.append("\nTous les comptes sont à jour.")
            await self.sender.send(self.chat_id, "\n".join(lines), None, True)
            return
        kb: Keyboard = [[("1️⃣ Un compte (test)", "dep:one"), ("☑ Sélection", "dep:sel")],
                        [(f"📦 Tous ({len(plan.targets)})", "dep:all")], [("✖ Fermer", "dep:close")]]
        self._dep_msg = await self.sender.send(self.chat_id, "\n".join(lines), kb, True)

    # ------------------------------------------------------------------ mises à jour (GitHub)
    async def on_update_available(self, plan) -> None:
        self._upd_plan = plan
        from core.version import bot_version, read as vread
        from mt5 import base as basemod
        lines = ["⬆ <b>Mise à jour ScalpX disponible</b>"]
        if plan.note:
            lines.append("📝 " + esc(plan.note.splitlines()[0][:200]))
        if plan.bot_files or plan.bot_remove:
            new_b = vread(plan.source / "bot" / "version.txt") or "?"
            lines.append(f"🤖 Bot {esc(bot_version())} → <b>{esc(new_b)}</b>")
        if any(f.lower().endswith((".mq5", ".mqh")) for f in plan.ea_files):
            lines.append(f"📈 EA {esc(basemod.read_version(self.ctx.cfg.paths.base) or '?')} → <b>{esc(plan.version or '?')}</b>")
        if plan.bot_files or plan.bot_remove:
            extra = f", {len(plan.bot_remove)} supprimé(s)" if plan.bot_remove else ""
            lines.append(f"🤖 Bot : {len(plan.bot_files)} fichier(s){extra} — redémarrage du bot (≈ 30 s, les terminaux continuent)")
        if plan.ea_files:
            lines.append(f"📈 EA : {len(plan.ea_files)} fichier(s) — copie dans BASE, compilation, puis proposition de déploiement")
        names = plan.bot_files[:6] + plan.ea_files[:6]
        lines.append("<i>" + esc(", ".join(names)) + ("…" if len(plan.bot_files) + len(plan.ea_files) > len(names) else "") + "</i>")
        self._upd_msg = await self.sender.send(self.chat_id, "\n".join(lines),
                                               [[("⬆ Installer", f"upd:go:{plan.stamp}"), ("⏰ Plus tard", "upd:later")]], True)

    async def _upd_cb(self, p: list[str]) -> str:
        from core import update as upd
        from pathlib import Path as _P
        sub = p[1] if len(p) > 1 else ""
        mid = getattr(self, "_upd_msg", None)
        if sub == "later":
            if mid:
                await self.sender.edit(self.chat_id, mid, "⏰ Mise à jour reportée. Pour l'installer plus tard : "
                                       "tableau de bord → <b>🔔 ⬆ Mises à jour</b>.", None)
            await self.dash.refresh(force=True)
            return ""
        if sub != "go":
            return ""
        if self.ctx.deploy.running or self.building:
            return "Déploiement ou compilation en cours : réessaie après."
        bot_root = _P(__file__).resolve().parent.parent
        plan = upd.plan_update(self.ctx.cfg, bot_root)
        if plan is None or plan.empty:
            if mid:
                await self.sender.edit(self.chat_id, mid, "✅ Déjà à jour.", None)
            return ""
        out = []
        try:
            if plan.ea_files:
                ver = upd.install_ea(self.ctx.cfg, plan)
                out.append(f"📈 EA copié dans BASE" + (f" — compilation v{esc(ver)} dans quelques secondes" if ver else " (.set seulement)"))
            if plan.bot_files or plan.bot_remove:
                upd.install_bot(plan, bot_root)
                if plan.requirements_changed:
                    ok, err = await asyncio.get_running_loop().run_in_executor(None, upd.pip_install, bot_root)
                    out.append("📦 Bibliothèques " + ("installées" if ok else "NON installées : " + esc(err)))
                out.append("🤖 Bot mis à jour : redémarrage…")
        except Exception as exc:
            log.exception("mise à jour")
            out.append(f"❌ Erreur : {esc(str(exc))}")
            if mid:
                await self.sender.edit(self.chat_id, mid, "\n".join(out), None)
            return ""
        self.ctx.db.meta_set("update_offered", plan.stamp)
        self.ctx.db.meta_set("update_pending", "")
        info = upd.commit_info(plan.source)
        if info.get("sha"):
            msg = (info.get("message") or "").splitlines()[0][:60] if info.get("message") else ""
            self.ctx.db.meta_set("installed_commit", f"{info['sha'][:7]} {msg}".strip())
        if mid:
            await self.sender.edit(self.chat_id, mid, "✅ <b>Mise à jour installée</b>\n" + "\n".join(out), None)
        if plan.bot_files or plan.bot_remove:
            how = upd.restart_bot(bot_root)
            log.warning("mise à jour du bot installée : redémarrage (%s)", how)
            upd.STOP.set()
        return ""

    async def check_updates_now(self) -> None:
        """Bouton ⬆ Mises à jour : vérifie GitHub tout de suite et propose l'installation, même si elle a été reportée."""
        from core import github as gh, update as upd
        from pathlib import Path as _P
        cfg = self.ctx.cfg
        loop = asyncio.get_running_loop()
        if cfg.update.github:
            res = await loop.run_in_executor(None, lambda: gh.fetch(cfg, force=True))      # demandé : sans délai
            if not res.ok:
                await self.sender.send(self.chat_id, f"⚠️ Mises à jour : {esc(res.error)}", [[("✖ Fermer", "d:close")]], True)
                return
        plan = await loop.run_in_executor(None, upd.plan_update, cfg, _P(__file__).resolve().parent.parent)
        if plan is None:
            await self.sender.send(self.chat_id, "⚠️ Aucun paquet de mise à jour trouvé (GitHub non configuré ?).", [[("✖ Fermer", "d:close")]], True)
            return
        self.dash.flash("", 0.1)
        if plan.empty:
            self.ctx.db.meta_set("update_pending", "")
            info = upd.commit_info(plan.source)
            note = (info.get("message") or "").splitlines()[0][:80] if info.get("message") else ""
            await self.sender.send(self.chat_id, f"✅ <b>Tout est à jour</b>" + (f"\nDernière publication : {esc(note)}" if note else ""),
                                   [[("✖ Fermer", "d:close")]], True)
            await self.dash.refresh(force=True)
            return
        self.ctx.db.meta_set("update_pending", plan.stamp)
        await self.on_update_available(plan)

    async def rollback_bot(self) -> None:
        from core import update as upd
        from .system import BOT_ROOT
        try:
            ver = await asyncio.get_running_loop().run_in_executor(None, upd.restore_bot, BOT_ROOT)
        except Exception as exc:
            log.exception("retour arrière")
            await self.sender.send(self.chat_id, f"❌ Retour impossible : {esc(str(exc))}", [[("✖ Fermer", "d:close")]], True)
            return
        await self.sender.send(self.chat_id, f"⏪ Bot {esc(ver)} restauré : redémarrage…", None, True)
        upd.restart_bot(BOT_ROOT)
        upd.STOP.set()

    async def restart_bot(self) -> None:
        """Relance le bot (même mécanisme qu'après une mise à jour) : terminaux et EA non touchés."""
        from core import update as upd
        from pathlib import Path as _P
        if self.ctx.deploy.running or self.building:
            await self.sender.send(self.chat_id, "⏳ Déploiement ou compilation en cours : redémarre après.", [[("✖ Fermer", "d:close")]], True)
            return
        await self.sender.send(self.chat_id, "🔄 Redémarrage du bot… il revient dans environ 30 s.", None, True)
        how = upd.restart_bot(_P(__file__).resolve().parent.parent)
        log.warning("redémarrage demandé depuis Telegram (%s)", how)
        upd.STOP.set()

    async def run_manual_build(self) -> None:
        """Bouton 🔨 Compiler l'EA : compile BASE tel quel, puis propose le déploiement si le .ex5 a changé."""
        from mt5 import build as buildmod
        from mt5.deploy import detect_new_build
        self.building = True
        try:
            mid = await self.sender.send(self.chat_id, "🔨 Compilation de l'EA dans BASE…", None, True)
            res = await buildmod.compile_ea(self.ctx.cfg)
            text = (f"✅ <b>EA v{esc(res.version)} compilé</b> ({res.warnings} avertissement(s))." if res.ok else
                    f"❌ <b>Compilation échouée</b>" + (f" ({res.errors} erreur(s))" if res.errors > 0 else "") + f"\n{esc(res.detail)}")
            if mid:
                await self.sender.edit(self.chat_id, mid, text, [[("✖ Fermer", "d:close")]])
            if res.ok:
                nb = detect_new_build(self.ctx.cfg, self.ctx.db, settle_s=0)
                if nb is not None:
                    await self.on_new_build(nb)
        finally:
            self.building = False

    async def run_auto_build(self, req) -> None:
        """version.txt modifié : version écrite dans les sources, compilation dans BASE, puis proposition de déploiement."""
        from mt5 import build as buildmod
        from mt5.deploy import detect_new_build
        self.building = True
        try:
            mid = await self.sender.send(self.chat_id, f"🛠 <b>version.txt = {esc(req.version)}</b> : mise à jour des sources et compilation dans BASE…", None, True)
            res = await buildmod.auto_build(self.ctx.cfg, self.ctx.db, req)
            if res.ok:
                text = f"✅ <b>EA v{esc(res.version)} compilé</b> (0 erreur, {res.warnings} avertissement(s))."
            else:
                text = (f"❌ <b>Compilation v{esc(res.version)} échouée</b>"
                        + (f" ({res.errors} erreur(s))" if res.errors > 0 else "") + f"\n{esc(res.detail)}\n\n"
                        "Aucun compte n'est touché. Corrige dans MetaEditor (F7) : la nouvelle compilation sera proposée au déploiement.")
            if mid:
                await self.sender.edit(self.chat_id, mid, text, None)
            if res.ok:
                nb = detect_new_build(self.ctx.cfg, self.ctx.db, settle_s=0)
                if nb is not None:
                    await self.on_new_build(nb)
        finally:
            self.building = False

    async def on_new_build(self, nb) -> None:
        """Nouvelle compilation détectée dans BASE : proposer de la déployer sur les comptes existants."""
        if self.ctx.deploy.running:
            return
        plan = nb.plan
        names = [v.inst.label for v in self.ctx.views() if v.inst.id in plan.targets]
        lines = [f"🆕 <b>Nouvelle compilation de l'EA détectée dans BASE</b>",
                 f"Version <b>{esc(plan.base_version or '?')}</b> · compilée à {time.strftime('%H:%M', time.localtime(nb.compiled_at))}",
                 "", f"{len(plan.targets)} compte(s) tournent avec l'ancien .ex5 :", esc(", ".join(names[:15]))]
        if plan.inputs_changed:
            lines.append(f"\n⚠️ Les inputs du .set ont changé ({esc(plan.inputs_diff)}). Les paramètres des comptes ne seront PAS modifiés.")
        lines.append("\nMise à jour un compte à la fois, après fermeture des positions, avec retour à l'ancien .ex5 en cas d'échec.")
        kb: Keyboard = [[(f"🚀 Déployer sur tous ({len(plan.targets)})", "dep:all")],
                        [("1️⃣ Un compte d'abord", "dep:one"), ("☑ Sélection", "dep:sel")],
                        [("⏰ Plus tard", "dep:close")]]
        self._dep_msg = await self.sender.send(self.chat_id, "\n".join(lines), kb, True)

    async def _deploy_single(self, inst) -> str:
        plan = plan_deploy(self.ctx.cfg, self.ctx.db, [inst.id])
        if inst.id not in plan.targets:
            self.dash.flash("✅ Ce compte est déjà à jour.")
        else:
            extra = f"\n⚠️ inputs modifiés ({esc(plan.inputs_diff)}), paramètres inchangés." if plan.inputs_changed else ""
            return await self._ask(f"⬆ <b>Mettre à jour {esc(inst.label)} vers v{esc(plan.base_version)} ?</b>\nMaintenance, arrêt propre, copie du .ex5, relance, vérification.{extra}",
                                   lambda: self._run_deploy([inst.id]), ("acc", inst.id), "⬆ Déployer")
        await self.dash.refresh(force=True)
        return ""

    async def _dep_cb(self, p: list[str]) -> str:
        sub = p[1] if len(p) > 1 else ""
        plan = plan_deploy(self.ctx.cfg, self.ctx.db)
        mid = self._dep_msg
        if sub == "close" and mid:
            await self.sender.delete(self.chat_id, mid)
        elif sub == "one":
            kb = [[(f"{v.inst.label[:20]}", f"dep:go1:{short(v.inst.id)}")] for v in self.ctx.views() if v.inst.id in plan.targets]
            await self._dep_edit("Choisis le compte de test :", kb + [[("⬅ Retour", "dep:back")]])
        elif sub == "sel":
            self._dep_sel = set()
            await self._dep_edit_sel(plan)
        elif sub == "tog" and len(p) > 2:
            inst = resolve(self.ctx.db, p[2])
            if inst:
                self._dep_sel ^= {inst.id}
            await self._dep_edit_sel(plan)
        elif sub == "go1" and len(p) > 2:
            inst = resolve(self.ctx.db, p[2])
            if inst:
                await self._dep_confirm([inst.id], plan)
        elif sub == "gosel":
            await self._dep_confirm([i for i in plan.targets if i in self._dep_sel], plan)
        elif sub == "all":
            await self._dep_confirm(list(plan.targets), plan)
        elif sub == "back":
            if mid:
                await self.sender.delete(self.chat_id, mid)
            await self.cmd_deployer()
        elif sub == "run" and len(p) > 2:
            tok = p[2]
            pend = self.conf.take(tok)
            if pend is None:
                return "Expiré"
            self._bg(pend.run())
        elif sub == "abort":
            if mid:
                await self.sender.edit(self.chat_id, mid, "✖ Déploiement annulé.", None)
        elif sub == "resume":
            self._bg(self._run_resume())
        elif sub == "cancel":
            self.ctx.deploy.cancel_interrupted()
            await self.sender.send(self.chat_id, "✖ Déploiement interrompu annulé : comptes remis en DEMARRE.", None, True)
        elif sub == "stop":
            self.ctx.deploy.cancel()
            return "Arrêt après le compte en cours"
        return ""

    async def _dep_edit(self, text: str, kb: Keyboard) -> None:
        if self._dep_msg:
            await self.sender.edit(self.chat_id, self._dep_msg, text, kb)

    async def _dep_edit_sel(self, plan) -> None:
        kb: Keyboard = [[(("☑ " if v.inst.id in self._dep_sel else "☐ ") + v.inst.label[:20], f"dep:tog:{short(v.inst.id)}")]
                        for v in self.ctx.views() if v.inst.id in plan.targets]
        kb.append([(f"▶ Déployer ({len(self._dep_sel)})", "dep:gosel"), ("⬅ Retour", "dep:back")])
        await self._dep_edit("Sélectionne les comptes à mettre à jour :", kb)

    async def _dep_confirm(self, targets: list[str], plan) -> None:
        if not targets:
            return
        labels = ", ".join((self.ctx.db.get(t).label if self.ctx.db.get(t) else t) for t in targets[:10])
        extra = f"\n⚠️ Inputs modifiés ({esc(plan.inputs_diff)}) : paramètres inchangés, à vérifier après coup." if plan.inputs_changed else ""
        tok = self.conf.ask(Pending("deploy", lambda: self._run_deploy(targets)))
        await self._dep_edit(f"⚠️ <b>Confirmer le déploiement v{esc(plan.base_version)} sur {len(targets)} compte(s) ?</b>\n{esc(labels)}{extra}\n\n"
                             "Un compte à la fois ; le déploiement s'arrête au premier échec.",
                             [[("✅ Déployer", f"dep:run:{tok}"), ("✖ Annuler", "dep:abort")]])

    async def _run_deploy(self, targets: list[str]) -> None:
        await self._deploy_with_progress(lambda cb: self.ctx.deploy.run(targets, cb))

    async def _run_resume(self) -> None:
        await self._deploy_with_progress(lambda cb: self.ctx.deploy.resume(cb))

    async def _deploy_with_progress(self, runner) -> None:
        """Progression en direct dans UN seul message édité (✅ mis à jour, ⏳ en cours, ⏸ en attente, ❌ échec et restauré)."""
        labels = {i.id: i.label for i in self.ctx.db.list()}
        stop_kb: Keyboard = [[("⏹ Arrêter après le compte en cours", "dep:stop")]]
        mid = await self.sender.send(self.chat_id, render.deploy_view({}, labels) + "\nPréparation…", stop_kb, False)
        last = [0.0]

        async def cb(view):
            if time.time() - last[0] < 1.5:                      # limite le rythme d'édition (le rapport final corrige tout)
                return
            last[0] = time.time()
            await self.sender.edit(self.chat_id, mid, render.deploy_view(view, labels), stop_kb)

        outcomes = await runner(cb)
        ok = sum(1 for o in outcomes if o.status == "ok")
        wait = [o for o in outcomes if o.status == "waiting"]
        bad = [o for o in outcomes if o.status == "failed"]
        view = {o.iid: ({"ok": "✅", "waiting": "⏸", "failed": "❌"}[o.status], o.message or "mis à jour") for o in outcomes}
        text = render.deploy_view(view, labels, "🔄 Déploiement terminé") + f"\n\n✅ {ok} · ⏸ {len(wait)} · ❌ {len(bad)}"
        if wait:
            text += "\n⏸ Instances en attente (positions ouvertes) : relance « 🚀 Déployer l'EA » plus tard."
        if bad:
            text += "\n❌ Échec : ancien .ex5 restauré, déploiement stoppé."
        await self.sender.edit(self.chat_id, mid, text, None)
        await self.notifier.emit(Notice("-", "Déploiement", "DEPLOIEMENT", f"🔄 Déploiement terminé : ✅ {ok} · ⏸ {len(wait)} · ❌ {len(bad)}", 2 if bad else 1))

    # ------------------------------------------------------------------ alertes internes (watchdog, etc.)
    async def alert(self, iid: str, kind: str, text: str, prio: int) -> None:
        inst = self.ctx.db.get(iid)
        label = inst.label if inst else iid
        self.ctx.db.add_incident(iid, kind, text)
        await self.notifier.emit(Notice(iid, label, kind, text, prio))
