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
from . import presets_ui as pui
from .ui import ALL_ID, Confirmations, Keyboard, Pending, Sender, resolve, short

log = logging.getLogger(__name__)


class Router:
    awaiting: tuple[str, int] | None = None

    def __init__(self, ctx: act.Ctx, sender: Sender, chat_id: int, dash: Dashboard, conf: Confirmations,
                 notifier: Notifier) -> None:
        self.ctx, self.sender, self.chat_id, self.dash, self.conf, self.notifier = ctx, sender, chat_id, dash, conf, notifier
        dash.params.on_active_change = self.sync_base_set
        self._tasks: set[asyncio.Task] = set()
        self._dep_msg: int | None = None
        self.building = False
        self.awaiting_preset: tuple[str, str] | None = None   # (compte modèle, preset remplacé) : nom attendu (texte)
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
            if head == "z":
                return await self._pause_cb(parts)
            if head == "s":
                return await self._preset_cb(parts)
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
        elif sub == "fa":                                   # tous les comptes (filtre remis à « Tous »)
            self.dash.goto("lst")
            self.dash.set_filter("all")
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
        elif sub == "jra":                                  # journal des actions demandées depuis Telegram
            from .system import actions_text
            await self.sender.send(self.chat_id, actions_text(self.ctx), [[("✖ Fermer", "d:close")]], True)
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
        elif sub == "pall":
            if not any(self.ctx.mgr.is_running(v.inst) for v in self.ctx.views()):
                self.dash.flash("Aucun terminal lancé.")
            else:
                self.dash.goto("pz", ALL_ID)
        elif sub == "rall":
            cmd = "REPRISE"
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
        """Code court d'un compte, « all » (tous les comptes lancés), ou « P… » (un preset : ⚙️ Paramètres du preset)."""
        if code.startswith("P"):
            name = pui.find(self.dash.store, code)
            return pui.PresetTarget(name) if name else None
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
        elif sub == "c":                                                # 🕐 créneaux
            self.dash.goto("prc", inst.id)
            if not ui.fresh(inst.id):
                self._bg(self._prm_fetch(inst))
        elif sub == "ca":                                               # ➕ choix de l'heure
            if len(pu.cat.slots_of(ui.masks(inst.id) or [0, 0, 0, 0])) >= pu.cat.MAX_SLOTS:
                self.dash.flash(f"⚠️ {pu.cat.MAX_SLOTS} créneaux maximum : retires-en un d'abord.")
            else:
                self.dash.goto("prh", inst.id)
        elif sub.startswith("ch") and sub[2:].isdigit() and int(sub[2:]) < 24:
            self.dash.goto("prq", inst.id, int(sub[2:]))
        elif sub.startswith("cm") and sub[2:].isdigit() and len(p) > 3 and p[3].isdigit():
            self._slot_change(inst, int(sub[2:]) * 60 + int(p[3]), add=True)
        elif sub.startswith("cx") and sub[2:].isdigit():
            self._slot_change(inst, int(sub[2:]), add=False)
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

    def _slot_change(self, inst, slot: int, add: bool) -> None:
        """Ajoute ou retire un créneau : un seul masque (celui de ses minutes) est envoyé, sur le robot, tous les robots ou le preset."""
        cat = pu.cat
        masks = self.dash.params.masks(inst.id)
        h, m = divmod(slot, 60)
        if masks is None:
            self.dash.flash("⚠️ Créneaux inconnus : touche 🔃 Relire.")
            return
        if not (0 <= h < 24 and m in cat.SLOT_MINUTES):
            self.dash.flash("❌ Créneau invalide.")
            return
        slots = cat.slots_of(masks)
        t = cat.slot_text(slot)
        self.dash.goto("prc", inst.id)
        if add and slot in slots:
            self.dash.flash(f"ℹ️ Le créneau {t} existe déjà.")
            return
        if not add and slot not in slots:
            self.dash.flash(f"ℹ️ Le créneau {t} n'existe plus.")
            return
        if add and len(slots) >= cat.MAX_SLOTS:
            self.dash.flash(f"⚠️ {cat.MAX_SLOTS} créneaux maximum : retires-en un d'abord.")
            return
        mi = cat.SLOT_MINUTES.index(m)
        new = masks[mi] | (1 << h) if add else masks[mi] & ~(1 << h)
        cached = self.dash.params.vals.get(inst.id)
        if cached:                                     # cache mis à jour tout de suite : deux touches rapides ne s'écrasent pas
            cached[1][cat.SLOT_KEYS[mi]] = str(new)
        self._bg(self._prm_apply(inst, pu.find(cat.SLOT_KEYS[mi]), str(new),
                                 f"Créneau {t} {'ajouté' if add else 'retiré'}"))

    async def _prm_fetch(self, inst) -> None:
        ok = await self.dash.params.fetch(inst)
        self.dash.flash("" if ok else f"❌ Lecture impossible : {self.dash.params.last_error}.", 25.0 if not ok else 0.1)
        await self.dash.refresh(force=True)

    async def _prm_apply(self, inst, idx: int, value: str, shown: str = "") -> None:
        self.dash.flash(f"⏳ {shown or pu.cat.PARAMS[idx][0] + ' = ' + value}…")
        await self.dash.refresh(force=True)
        self.dash.flash(await self.dash.params.set_value(inst, idx, value, shown))
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
            iid, _ = self.awaiting_preset
            self.awaiting_preset = None
            inst = self.ctx.db.get(iid)
            name = text.strip()
            if inst is None or not re.fullmatch(r"[A-Za-z0-9_-]{1,30}", name):
                self.dash.flash("❌ Nom invalide : lettres, chiffres, - et _ (30 max).")
                await self.dash.refresh(force=True)
                return
            self._bg(self._preset_capture(inst, name))
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
            dash.goto("pz", inst.id)
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

    # ------------------------------------------------------------------ 💾 presets centraux
    def _running(self) -> list:
        return [i for i in self.ctx.db.list() if self.ctx.mgr.is_running(i)]

    async def _preset_cb(self, p: list[str]) -> str:
        """s:l liste · s:o:<P> preset · s:new[:<P>] modèle · s:src:<compte>[:<P>] · s:ap:<P> appliquer · s:del:<P> · s:chk."""
        store, dash = self.dash.store, self.dash
        sub = p[1] if len(p) > 1 else "l"
        name = pui.find(store, p[2]) if len(p) > 2 and sub in ("o", "ap", "del", "new", "cmp") else ""
        self.awaiting_preset = None
        if sub == "o" and name:
            dash.goto("pso", name)
        elif sub == "new":
            insts = self._running()
            if not insts:
                dash.flash("Aucun robot lancé : impossible de lire des réglages.")
            elif len(insts) == 1:
                return await self._preset_source(insts[0], name)
            else:
                dash.goto("psn", name) if name else dash.goto("psn")
        elif sub == "src" and len(p) > 2:
            inst = resolve(self.ctx.db, p[2])
            over = pui.find(store, p[3]) if len(p) > 3 else ""
            if inst is None:
                return "Compte introuvable"
            return await self._preset_source(inst, over)
        elif sub == "ap" and name:
            insts = self._running()
            if not insts:
                dash.flash("Aucun robot lancé.")
            else:
                return await self._ask(f"📤 <b>Appliquer le preset « {esc(name)} » à {len(insts)} robot(s) ?</b>\n"
                                       + esc(", ".join(i.label for i in insts[:15])) +
                                       "\nLes réglages sont changés <b>tout de suite</b>, même pendant une série en cours. "
                                       "Il devient le preset ⭐ actif.",
                                       lambda: self._preset_apply(name, insts), ("pso", name), "📤 Appliquer")
        elif sub == "del" and name:
            return await self._ask(f"🗑 <b>Supprimer le preset « {esc(name)} » ?</b>\nLes robots gardent leurs réglages actuels.",
                                   lambda: self._preset_delete(name), ("pso", name), "🗑 Supprimer")
        elif sub == "cmp" and name:
            if not self._running():
                dash.flash("Aucun robot lancé : rien à comparer.")
            else:
                dash.flash("⏳ Lecture des réglages des robots…")
                self._bg(self._preset_compare(name))
        elif sub == "chk":
            dash.goto("psl")
            dash.flash("⏳ Contrôle des robots…")
            self._bg(self.check_presets())
        else:
            dash.goto("psl")
        await dash.refresh(force=True)
        return ""

    async def _preset_source(self, inst, overwrite: str) -> str:
        """Robot modèle choisi : remplacement direct, sinon le nom du nouveau preset est attendu (message texte)."""
        if overwrite:
            return await self._ask(f"📸 <b>Remplacer le preset « {esc(overwrite)} » par les réglages actuels de {esc(inst.label)} ?</b>",
                                   lambda: self._preset_capture(inst, overwrite), ("pso", overwrite), "📸 Remplacer")
        self.awaiting_preset = (inst.id, "")
        self.dash.goto("psl")
        self.dash.flash(f"✏️ Envoie le <b>nom du nouveau preset</b> (lettres, chiffres, - et _) : il prendra les réglages de {esc(inst.label)}.", 120)
        await self.dash.refresh(force=True)
        return ""

    async def _preset_capture(self, inst, name: str) -> None:
        self.dash.flash(f"⏳ Lecture des réglages de {esc(inst.label)}…")
        await self.dash.refresh(force=True)
        raw = await pui.read_raw(self.ctx, inst)
        if not raw:
            self.dash.flash(f"❌ Réglages illisibles sur {esc(inst.label)} (terminal arrêté ?).")
        else:
            existed = name in self.dash.store.presets
            self.dash.store.put(name, raw)
            self.dash.params.vals.pop(pui.PREFIX + name, None)
            self.dash.goto("pso", name)
            self.dash.flash(f"✅ Preset « {esc(name)} » {'remplacé' if existed else 'créé'} : {len(raw)} réglages de {esc(inst.label)}."
                            + (" Preset actif : 📤 Appliquer pour l'envoyer aux robots." if name == self.dash.store.active else ""))
        await self.dash.refresh(force=True)

    async def _preset_apply(self, name: str, insts) -> None:
        values = self.dash.store.presets.get(name)
        if values is None:
            return
        mid = await self.sender.send(self.chat_id, f"⏳ <b>Preset « {esc(name)} »</b> → {len(insts)} robot(s)…", None, True)
        res = await asyncio.gather(*[pui.apply_to(self.ctx, i, values) for i in insts])
        self.dash.store.set_active(name)
        note = self.sync_base_set()
        lines = []
        for i, (ok, n, bad, err) in zip(insts, res):
            if err:
                lines.append(f"❌ <b>{esc(i.label)}</b> : {esc(err)}")
            elif ok:
                lines.append(f"✅ <b>{esc(i.label)}</b> : " + (f"{n} réglage(s) changé(s)" if n else "déjà conforme"))
            else:
                lines.append(f"⚠️ <b>{esc(i.label)}</b> : refusé par l'EA : {esc(pui._keys_text(bad))}")
        good = sum(1 for ok, _, _, err in res if ok and not err)
        await self.sender.edit(self.chat_id, mid, f"📤 <b>Preset « {esc(name)} »</b> ⭐ actif : {good}/{len(insts)} robot(s) conforme(s)\n"
                               + "\n".join(lines) + (f"\n{note}" if note else ""), [[("✖ Fermer", "d:close")]])
        await self.check_presets()

    async def _preset_compare(self, name: str) -> None:
        values = self.dash.store.presets.get(name)
        insts = self._running()
        if values is None:
            return
        raws = await asyncio.gather(*[pui.read_raw(self.ctx, i) for i in insts])
        await self.sender.send(self.chat_id, pui.compare_text(name, values, [(i.label, r) for i, r in zip(insts, raws)]),
                               [[("✖ Fermer", "d:close")]], True)
        self.dash.flash("🔍 Comparaison envoyée ci-dessous.", 10)
        await self.dash.refresh(force=True)

    async def _preset_delete(self, name: str) -> None:
        self.dash.store.delete(name)
        self.dash.goto("psl")
        self.dash.flash(f"🗑 Preset « {esc(name)} » supprimé.")
        if self.dash.preset_check and self.dash.preset_check.preset == name:
            self.dash.preset_check = None
        await self.dash.refresh(force=True)

    def sync_base_set(self) -> str:
        """Preset ⭐ actif → .set de BASE : les nouveaux comptes partent de ces réglages. Renvoie une note (vide si rien)."""
        from core import presets as cps
        store = self.dash.store
        if not store.active:
            return ""
        path = self.ctx.cfg.paths.base / "MQL5" / "Presets" / self.ctx.cfg.mt5.set_name
        try:
            ok = cps.write_base_set(path, store.presets[store.active])
        except (OSError, UnicodeDecodeError) as exc:
            log.warning(".set de BASE non mis à jour : %s", exc)
            return "⚠️ .set de BASE non mis à jour"
        return "📦 .set de BASE mis à jour (nouveaux comptes)" if ok else ""

    async def preset_for_new(self, iid: str) -> str:
        """Nouveau compte : applique le preset ⭐ actif et vérifie. Renvoie la ligne à ajouter au message de création."""
        store = self.dash.store
        inst = self.ctx.db.get(iid)
        if not store.active or inst is None:
            return "💾 Aucun preset actif : réglages du .set de BASE."
        ok, n, bad, err = await pui.apply_to(self.ctx, inst, store.presets[store.active])
        if err:
            return f"⚠️ Preset « {esc(store.active)} » non appliqué : {esc(err)} (💾 Presets → 📤 Appliquer)."
        if not ok:
            return f"⚠️ Preset « {esc(store.active)} » : refusé par l'EA pour {esc(pui._keys_text(bad))}."
        return f"💾 Preset « {esc(store.active)} » appliqué" + (f" ({n} réglage(s) ajusté(s))." if n else " (déjà conforme).")

    async def check_presets(self) -> None:
        """Compare les réglages de chaque robot lancé au preset actif (toutes les 5 min et après une application)."""
        store = self.dash.store
        if not store.active:
            self.dash.preset_check = None
            return
        self.dash.preset_check = await pui.check_all(self.ctx, store, self._running())
        await self.dash.refresh(force=True)

    async def _pause_cb(self, p: list[str]) -> str:
        """z:<compte|all>:<choix> : pause immédiate ou programmée (reprise automatique par l'EA)."""
        from .dashboard import PAUSE_CHOICES
        choice = next((c for c in PAUSE_CHOICES if len(p) > 2 and c[0] == p[2]), None)
        if choice is None or len(p) < 2:
            return "Choix invalide"
        _, label, arg = choice
        args = [arg] if arg else []
        if p[1] == "all":
            insts = [v.inst for v in self.ctx.views() if self.ctx.mgr.is_running(v.inst)]
            if not insts:
                self.dash.flash("Aucun terminal lancé.")
                await self.dash.refresh(force=True)
                return ""
            return await self._ask(f"⏸ <b>Pause {esc(label.lower())} sur {len(insts)} compte(s) ?</b>\n"
                                   + esc(", ".join(i.label for i in insts[:15])),
                                   lambda: self._group(insts, "PAUSE", args), ("dash",), "⏸ Confirmer")
        inst = resolve(self.ctx.db, p[1])
        if inst is None:
            return "Compte introuvable"
        self.dash.goto("acc", inst.id)
        self._bg(self._run_cmd_flash(inst, "PAUSE", args))
        return ""

    async def _group(self, insts, cmd: str, args: list[str] | None = None) -> None:
        """Même commande sur plusieurs comptes, en parallèle ; un seul message de résultat."""
        title = cmd + (" " + " ".join(args) if args else "")
        mid = await self.sender.send(self.chat_id, f"⏳ <b>{esc(title)}</b> sur {len(insts)} compte(s)…", None, True)
        res = await asyncio.gather(*[act.send_cmd(self.ctx, i, cmd, args) for i in insts])
        lines = [f"{'✅' if r.ok else '❌'} <b>{esc(i.label)}</b> : {esc((r.message or '').splitlines()[0][:120] if r.message else '')}"
                 for i, r in zip(insts, res)]
        await self.sender.edit(self.chat_id, mid, f"<b>{esc(title)}</b>\n" + "\n".join(lines), [[("✖ Fermer", "d:close")]])
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
        elif sub == "rs":
            return await self._ask(f"♻️ <b>Remettre les valeurs par défaut de l'EA sur {lab} ?</b>\nTous les réglages reviennent aux valeurs "
                                   "de l'EA ; valide-les ensuite.", lambda: self._ea_run(inst, "RESET_PARAMS", []), back_c, "♻️ Rétablir")
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

    async def _ea_run(self, inst, cmd: str, args: list[str]) -> None:
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
        if plan.inputs_note:
            lines.append("\n" + esc(plan.inputs_note))
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
        from core import update as upd
        left = int(upd.commit_info(plan.source).get("remaining", 0) or 0)
        lines = ["⬆ <b>Mise à jour ScalpX disponible</b>" + (f" (encore {left} ensuite, proposée(s) une par une)" if left else "")]
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
            inst_c = (self.ctx.db.meta_get("installed_commit") or "").split(" ")[0]
            res = await loop.run_in_executor(None, lambda: gh.fetch(cfg, force=True, installed=inst_c))   # sans délai
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
        if plan.inputs_note:
            lines.append("\n" + esc(plan.inputs_note))
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
            extra = ("\n" + esc(plan.inputs_note)) if plan.inputs_note else ""
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
        extra = ("\n" + esc(plan.inputs_note)) if plan.inputs_note else ""
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
