"""Routeur : traduit callbacks et commandes texte en actions. Aucune dépendance à python-telegram-bot
(tout passe par l'interface `Sender`), donc testable sans Telegram."""
from __future__ import annotations

import asyncio
import contextvars
import os
import logging
import re
import time

from core.service import Notice
from mt5 import base as basemod
from core.summary import FILTERS, SORTS
from mt5.deploy import plan_deploy
from storage.db import DEMARRE
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


# Bot 3.18.0 : clés de .env saisies depuis Telegram : nom → (attribut de Config, description, format accepté)
SECRETS = {
    "BACKUP_TOKEN": ("backup_token", "jeton GitHub de sauvegarde des presets, github_pat_…", r"(github_pat_[A-Za-z0-9_]{20,255}|ghp_[A-Za-z0-9]{30,255})"),
    "HEALTHCHECK_URL": ("healthcheck_url", "adresse https://hc-ping.com/…", r"https://[A-Za-z0-9.-]+(/[A-Za-z0-9._~/-]*)?"),
}


def _clock() -> float:
    """Horloge de la confirmation à 10 s (Bot 3.18.0), remplaçable dans les tests sans toucher à asyncio."""
    return time.monotonic()


_CB_KEY: contextvars.ContextVar[str] = contextvars.ContextVar("scalpx_cb", default="")   # Bot 4.8.4 : bouton en cours

class Router:
    awaiting: tuple[str, int] | None = None

    def __init__(self, ctx: act.Ctx, sender: Sender, chat_id: int, dash: Dashboard, conf: Confirmations,
                 notifier: Notifier) -> None:
        self.ctx, self.sender, self.chat_id, self.dash, self.conf, self.notifier = ctx, sender, chat_id, dash, conf, notifier
        dash.params.on_active_change = self.sync_base_set
        self._tasks: set[asyncio.Task] = set()
        self._dep_msg: int | None = None
        self.building = False
        self.installing = False                 # installation d'une mise à jour en cours (copie des fichiers hors de la boucle)
        self.build_pct = 0                      # avancement estimé de la compilation en cours
        self._upd_after = ""                    # « Installer » touché pendant une compilation / un déploiement : installé ensuite
        self.awaiting_preset: tuple[str, str] | None = None   # (compte modèle, preset remplacé) : nom attendu (texte)
        self.awaiting_goal: str | None = None               # compte (ou ALL_ID) dont on attend l'objectif (message texte)
        self.awaiting_import = False                        # Bot 3.16.0 : fichier / texte de presets attendu (📥 Importer)
        self.awaiting_broker = ""                           # Bot 4.7.0 : "" | "name" | "url:<serveur>"
        self.awaiting_cmd = False                           # Bot 4.6.0 : 💻 commande attendue (message texte)
        self._shell_msg = 0                                 # Bot 4.8.8 : message de l'invite 💻 (devient confirmation puis résultat)
        self._shell_pending: dict[str, str] = {}            # jeton de confirmation → commande
        self._pending_import = self._pending_restore = b""   # Bot 4.2.0 : fichier chiffré en attente de son mot de passe
        self.awaiting_token = ""                            # Bot 3.18.0 : clé de .env attendue (message texte), ex. BACKUP_TOKEN
        self.awaiting_bulk = False                          # Bot 3.20.0 : fichier de comptes attendu (📄)
        self._bulk = None                                   # lot de comptes en attente de confirmation (mots de passe en mémoire)
        self.remember_server = None                         # assistant : serveur mémorisé après une connexion réussie
        self._dep_sel: set[str] = set()
        from mt5.backtester import BacktestManager
        self.bt = BacktestManager(ctx.cfg, ctx.db, ctx.mgr.procs)      # 🧪 terminal de backtest à part
        dash.bt = self.bt
        self.awaiting_bt: str | None = None                 # saisie attendue : login, server_text, password
        self._bt_msg: int | None = None                     # message de suivi du backtest en cours
        self._bt_last = (0.0, "")                           # dernière édition du suivi (heure, texte)
        from .optim import Optimizer
        self.optim = Optimizer(self)                        # 🤖 optimiseur automatique (nuit)
        dash.optim = self.optim

    def _bg(self, coro) -> None:
        """Lance une action longue sans bloquer la réception des mises à jour Telegram."""
        t = asyncio.get_event_loop().create_task(self._guard(coro))
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        key = _CB_KEY.get()
        if key and hasattr(self, "_cb_running"):                                             # Bot 4.8.4 : action lancée par un bouton → en cours tant qu'elle tourne
            self._cb_running.setdefault(key, set()).add(t)
            t.add_done_callback(lambda tt, k=key: self._cb_running.get(k, set()).discard(tt))

    @property
    def awaiting_any(self) -> bool:
        """Bot 4.8.7 : une saisie au clavier est attendue (assistant, jeton, mot de passe, objectif, preset, commande…)."""
        return bool(self.awaiting_bt or self.awaiting_token or self.awaiting_goal or self.awaiting_preset
                    or self.awaiting_import or self.awaiting_broker or self.awaiting_cmd or self.awaiting_bulk)

    def cancel_inputs(self) -> None:
        """Bot 4.8.7 : 📊 Tableau de bord (ou Accueil) annule toute saisie en cours : le bouton n'est jamais pris pour une réponse."""
        self.awaiting_bt = self.awaiting_goal = self.awaiting_preset = None
        self.awaiting_token = self.awaiting_broker = ""
        self.awaiting_import = self.awaiting_cmd = self.awaiting_bulk = False
        self._pending_import = self._pending_restore = b""
        if getattr(self.dash, "bt_wiz", None):
            self.dash.bt_wiz = ("login", {})

    CB_REPEAT_S = 1.5

    def _cb_duplicate(self, data: str) -> bool:
        """Bot 4.8.4 : même bouton retouché pendant que son action tourne, ou dans les 1,5 s : ignoré (sinon messages en double,
        ex. « Tout est à jour » deux fois, deux demandes de confirmation)."""
        if data.startswith("d:noop@"):
            return False
        if any(not t.done() for t in self._cb_running.get(data, ())):
            return True
        return time.monotonic() - self._cb_last.get(data, -1e9) < self.CB_REPEAT_S

    async def _guard(self, coro) -> None:
        try:
            await coro
        except Exception:
            log.exception("action en arrière-plan en erreur")

    # ------------------------------------------------------------------ callbacks
    async def on_callback(self, data: str, message_id: int = 0) -> str:
        """Traite un callback_data (un appui en double est ignoré). Renvoie un court texte pour query.answer()."""
        if not hasattr(self, "_cb_running"):
            self._cb_running, self._cb_last = {}, {}
        key = f"{data}@{message_id}"                         # même bouton SUR LE MÊME message (✖ Fermer sur 2 messages : permis)
        if self._cb_duplicate(key):
            log.info("bouton %s retouché pendant son action : ignoré", data)
            return "⏳ Déjà en cours…"
        self._cb_last[key] = time.monotonic()
        tok = _CB_KEY.set(key)
        try:
            return await self._on_callback(data, message_id)
        finally:
            _CB_KEY.reset(tok)
            self._cb_last[key] = time.monotonic()            # compté depuis la FIN (appuis traités l'un après l'autre)

    async def _on_callback(self, data: str, message_id: int = 0) -> str:
        parts = data.split(":")
        head = parts[0]
        if self.awaiting_broker and data not in ("d:brkadd",):
            self.awaiting_broker = ""                           # Bot 4.7.0 : autre bouton → ajout de courtier abandonné
        if self.awaiting_cmd and not data.startswith(("d:shc", "d:shn")):
            self.awaiting_cmd = False                           # Bot 4.6.0 : autre bouton → plus de commande attendue
        if data in ("d:home", "d:close") and self.awaiting_bt:
            self.cancel_inputs()                                # Bot 4.8.7 : quitter l'assistant l'annule
        if (self.awaiting_bt or "").startswith("bm_") and not data.startswith("d:bm"):
            self.awaiting_bt = None                             # Bot 4.13.0 : 🧪 saisie du backtest abandonnée (autre bouton)
        if self.awaiting_token and data not in ("d:bkp", "d:key", "d:noop"):
            # Bot 4.2.0 : tout autre bouton annule la saisie attendue (jeton / mot de passe) : un texte tapé plus tard
            # (nom de preset…) ne doit jamais devenir un jeton ou une clé de chiffrement.
            self.awaiting_token = ""
            self._pending_import = self._pending_restore = b""
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
                extra += "\nFermeture de tous en même temps, puis lancement 2 par 2 (le groupe suivant attend le vert)." if verb == "rst" else (
                         "\nLancement 2 par 2 : le groupe suivant attend le vert." if verb == "start" else "\nTous en même temps.")
                return await self._ask(f"<b>{what} {len(targets)} terminal(aux) ?</b>\n{esc(', '.join(i.label for i in targets[:15]))}{extra}",
                                       lambda: self._terminals(targets, verb), ("gterm",), what)
        elif sub == "flt":
            self.dash.goto("flt")
            self._bg(self._filters_fetch())
        elif sub == "pos":
            self._bg(self._positions_detail())
            return ""
        elif sub == "sst":                                  # 🕐 statistiques par créneau (7 / 30 / 90 jours)
            if len(p) > 2 and p[2].isdigit() and int(p[2]) in self.dash.SST_DAYS:
                self.dash.sst_days = int(p[2])
            self.dash.goto("sst")
        elif sub in ("opt", "opton", "opth", "optgo", "optj", "optf", "optg", "opu", "optr", "ops", "opsx"):   # 🤖 optimiseur
            r = await self._opt_cb(sub, p)
            if r is not None:
                return r
        elif sub in ("btc", "btsv", "btq", "btx"):         # 🔑 terminal de test de l'optimiseur
            r = await self._bt_cb(sub, p)
            if r is not None:
                return r
        elif sub in ("bm", "bmp", "bmd", "bmdt", "bmlo", "bmhi", "bmrs", "bmgo", "bml", "bmr", "bmg"):   # 🧪 backtest MT5
            r = await self._bm_cb(sub, p)
            if r is not None:
                return r
        elif sub in ("lst", "act", "rpt", "logs", "gterm", "wdr"):
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
        elif sub == "bulk":                                     # Bot 3.20.0 : 📄 plusieurs comptes depuis un fichier
            self.awaiting_bulk = True
            self.dash.goto("home")
            self.dash.flash("📄 Envoie un <b>fichier texte</b> (ou colle le texte), un compte par ligne :\n"
                            "<code>numéro:mot de passe:serveur</code>\nIl est effacé de la conversation dès réception.", 300)
        elif sub in ("bkgo", "bkno"):
            return await self._bulk_cb(sub, p)
        elif sub in ("unigo", "unino"):                         # Bot 3.18.3 : dernière confirmation (compte à rebours)
            return await self._uninstall_cb(sub, p)
        elif sub in ("srv", "sva", "rbt", "uni", "rup", "wvw"):  # Bot 3.18.0 : 🖥 Serveur (Linux)
            return await self._server_cb(sub, p)
        elif sub == "bkp":                                      # Bot 4.2.0 : ☁️ écran Sauvegarde
            self.dash.goto("bkp")
        elif sub == "bknever":
            self.ctx.db.meta_set("backup_never", "1")
            self.dash.flash("☁️ Je ne te le proposerai plus : 🛠 Système → ☁️ Sauvegarde pour l'activer plus tard.")
        elif sub == "bkpw":                                     # mot de passe de chiffrement (nouveau ou changé)
            self.awaiting_token = "BACKUP_PASSWORD"
            self.dash.goto("bkp")                                   # Bot 4.8.3 : « ✍️ J'attends ton mot de passe » dans l'écran
        elif sub == "bkplain":
            self.awaiting_token = ""
            self.ctx.db.meta_set("backup_plain", "1")
            self.dash.goto("bkp")
            self.dash.flash("☁️ Sauvegarde sans chiffrement (dépôt privé) : 🔒 Chiffrer reste possible ici.")
            self._bg(self._after_plain())
            return ""
        elif sub == "brk":                                      # Bot 4.7.0 : 🏦 courtiers connus
            await self.brokers_screen()
        elif sub == "brkadd":
            self.awaiting_broker = "name"
            await self.sender.send(self.chat_id, "🏦 <b>Ajouter un courtier</b> — 1/2 : envoie le <b>nom d'un serveur</b> de ce "
                                   "courtier, tel qu'il s'écrit dans MT5 (ex. <code>Exness-MT5Real8</code>).", None, False)
        elif sub == "shc":                                      # Bot 4.6.0 : 💻 exécuter une commande sur le serveur
            # Bot 4.8.8 : tout se passe dans UN message à part (invite → confirmation → résultat), jamais dans le tableau
            # de bord : il ne peut plus le recouvrir ni l'effacer.
            self.awaiting_cmd = True
            self._shell_msg = await self.sender.send(
                self.chat_id, "💻 <b>Envoie la commande</b> à exécuter sur le serveur (utilisateur <code>scalpx</code>, sans "
                "droits administrateur ; « sudo » en tête est retiré). Tu confirmes avant l'exécution ; arrêt au bout de 2 min ; "
                "le résultat s'affiche dans CE message.", [[("✖ Annuler", "d:shn")]], True)
        elif sub == "shn":                                      # Bot 4.8.8 : commande annulée
            self.awaiting_cmd = False
            tok = p[2] if len(p) > 2 else ""
            self._shell_pending.pop(tok, None)
            await self.sender.delete(self.chat_id, message_id)
        elif sub == "shx":                                      # Bot 4.8.8 : ▶️ exécuter (une seule fois par confirmation)
            tok = p[2] if len(p) > 2 else ""
            got = self._shell_pending.pop(tok, None)
            if got is None:
                return "Déjà lancée ou expirée"
            self._bg(self._shell_run(got, message_id))
        elif sub == "bkown":                                    # Bot 4.4.0 : remplacer la sauvegarde d'un autre serveur
            async def own() -> None:
                self.ctx.db.meta_set("bkp_owner", "1")
                self.ctx.db.meta_set("ghb_hash", "")
                self.ctx.db.add_action("", "SAUVEGARDE REMPLACÉE", True, "")
                err = await self.full_backup()
                await self.backup_presets()
                self.dash.goto("bkp")
                self.dash.flash("✅ Sauvegarde remplacée par ce serveur." if not err else f"❌ {esc(err)}")
                await self.dash.refresh(force=True)
            return await self._ask("🔁 <b>Remplacer la sauvegarde existante par ce serveur ?</b>\nPresets, historique et optimiseur "
                                   "de CE serveur remplacent ceux du dépôt (l'ancienne version reste dans l'historique GitHub).",
                                   own, ("bkp",), "🔁 Remplacer")
        elif sub == "bkoff":                                    # désactiver : jeton et clé retirés du .env (dépôt gardé)
            return await self._ask("☁️ <b>Désactiver la sauvegarde ?</b>\nLe bot oublie le jeton, le dépôt et la clé ; le dépôt "
                                   "GitHub et ses sauvegardes restent sur ton compte.", self._backup_off, ("bkp",), "🚫 Désactiver")
        elif sub == "fbk":                                      # Bot 3.21.0 : ☁️ sauvegarder maintenant
            self.dash.flash("⏳ Sauvegarde complète en cours…")
            await self.dash.refresh(force=True)
            err = await self.full_backup()
            self.dash.flash("✅ Sauvegarde complète envoyée sur GitHub." if not err else f"❌ {esc(err)}")
        elif sub == "frs":                                      # Bot 3.21.0 : ☁️ restaurer la sauvegarde complète
            self._bg(self._full_restore_ask())
            return ""
        elif sub == "key":                                      # Bot 3.18.0 : 🔑 clés et jetons (saisis ici, jamais sur le VPS)
            self.dash.goto("key")
        elif sub in ("kset", "bkt") and (sub == "bkt" or (len(p) > 2 and p[2] in SECRETS)):
            key = "BACKUP_TOKEN" if sub == "bkt" else p[2]
            self.awaiting_token = key
            self.dash.goto("bkp" if key == "BACKUP_TOKEN" else "key")   # Bot 4.8.3 : la consigne est dans l'écran
        elif sub == "rz":                                       # Bot 3.17.0 : ♻️ réinitialiser (double confirmation)
            return await self._ask("♻️ <b>Réinitialiser le serveur ?</b>\n"
                                   "Supprime <b>tous les comptes de ce serveur</b> (terminaux arrêtés, dossiers effacés), le terminal "
                                   "de backtest, tout l'historique (soldes, cycles, retraits, journaux, optimiseur) et les fichiers "
                                   "d'échange avec l'EA.\nGardés : le bot, BASE et les presets. "
                                   "Les positions restent chez le broker (TP en place).",
                                   self._reset_confirm2, ("srv",), "♻️ Continuer")
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
            path = log_path(self.ctx, name)
            txt = tail(path, 40 if sub == "clog" else 25)
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
        elif sub == "menu":                                     # Bot 3.6.3 : bouton permanent « 📊 Tableau de bord »
            await self.sender.reply_menu(self.chat_id, "📊 Bouton « Tableau de bord » remis en bas de l'écran.")
            self.dash.flash("📊 Bouton du bas remis.")
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
        elif sub[:1] in ("c", "u") and (len(sub) == 1 or sub[1] in "pahmx"):   # 🕐 créneaux (c…) / ⛔ coupures (u…, EA 7.0.0)
            f, rest = sub[0], sub[1:]
            scr = "prc" if f == "c" else "puc"
            if rest == "" or (rest.startswith("p") and rest[1:].isdigit()):     # liste (p<n> : page n)
                self.dash.goto(scr, inst.id, int(rest[1:]) if rest else 0)
                if not ui.fresh(inst.id):
                    self._bg(self._prm_fetch(inst))
            elif rest == "a":                                           # ➕ choix de l'heure
                self.dash.goto("prh" if f == "c" else "puh", inst.id)
            elif rest.startswith("h") and rest[1:].isdigit() and int(rest[1:]) < 24:
                self.dash.goto("prq" if f == "c" else "puq", inst.id, int(rest[1:]))
            elif rest.startswith("m") and rest[1:].isdigit() and len(p) > 3 and p[3].isdigit():
                self._slot_change(inst, int(rest[1:]) * 60 + int(p[3]), add=True, cut=f == "u")
            elif rest.startswith("x") and rest[1:].isdigit():
                self._slot_change(inst, int(rest[1:]), add=False, cut=f == "u")
        elif sub[0] == "g" and sub[1:].isdigit():
            self.dash.goto("prg", inst.id, int(sub[1:]))
        elif sub[0] == "k" and sub[1:].isdigit():
            self.dash.goto("prp", inst.id, int(sub[1:]))
        elif sub[0] == "t" and sub[1:].isdigit():                      # bascule oui/non
            idx = int(sub[1:])
            cur = pu.bool_of(ui.cached(inst.id).get(pu.cat.PARAMS[idx][0], ""))
            if cur is None:                                             # pas encore de valeur (preset plus ancien) : choix explicite
                self.dash.goto("prp", inst.id, idx)
                self.dash.flash("Choisis ✅ Activer ou ❌ Désactiver.")
            else:
                self._bg(self._prm_apply(inst, idx, "0" if cur else "1"))
        elif sub[0] == "s" and sub[1:].isdigit() and len(p) > 3:       # − / + n pas
            idx = int(sub[1:])
            prm = pu.cat.PARAMS[idx]
            cur = pu.num_of(ui.cached(inst.id).get(prm[0], "") or pu.cat.DEFAULTS.get(prm[0], ""))   # sans valeur : défaut de l'EA
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

    def _slot_change(self, inst, slot: int, add: bool, cut: bool = False) -> None:
        """Ajoute ou retire un créneau (ou une heure de coupure si cut) : un seul masque (celui de ses minutes) est envoyé,
        sur le robot, tous les robots ou le preset."""
        cat = pu.cat
        keys = cat.CUT_KEYS if cut else cat.SLOT_KEYS
        noun = "L'heure de coupure" if cut else "Le créneau"
        masks = self.dash.params.masks(inst.id, keys)
        h, m = divmod(slot, 60)
        if masks is None:
            self.dash.flash("⚠️ Heures inconnues : touche 🔃 Relire.")
            return
        if not (0 <= h < 24 and m in cat.SLOT_MINUTES):
            self.dash.flash("❌ Heure invalide.")
            return
        slots = cat.slots_of(masks)
        t = cat.slot_text(slot)
        self.dash.goto("puc" if cut else "prc", inst.id)
        if add and slot in slots:
            self.dash.flash(f"ℹ️ {noun} {t} existe déjà.")
            return
        if not add and slot not in slots:
            self.dash.flash(f"ℹ️ {noun} {t} n'existe plus.")
            return
        mi = cat.SLOT_MINUTES.index(m)
        new = masks[mi] | (1 << h) if add else masks[mi] & ~(1 << h)
        cached = self.dash.params.vals.get(inst.id)
        if cached:                                     # cache mis à jour tout de suite : deux touches rapides ne s'écrasent pas
            cached[1][keys[mi]] = str(new)
        what = f"Coupure {t} {'ajoutée' if add else 'retirée'}" if cut else f"Créneau {t} {'ajouté' if add else 'retiré'}"
        self._bg(self._prm_apply(inst, pu.find(keys[mi]), str(new), what))

    async def _prm_fetch(self, inst) -> None:
        ok = await self.dash.params.fetch(inst)
        self.dash.flash("" if ok else f"❌ Lecture impossible : {self.dash.params.last_error}.", 25.0 if not ok else 0.1)
        await self.dash.refresh(force=True)

    async def _prm_apply(self, inst, idx: int, value: str, shown: str = "") -> None:
        self.dash.flash(f"⏳ {shown or pu.cat.PARAMS[idx][0] + ' = ' + value}…")
        await self.dash.refresh(force=True)
        self.dash.flash(await self.dash.params.set_value(inst, idx, value, shown))
        await self.dash.refresh(force=True)

    MENU_TEXT = "📊 Tableau de bord"

    async def on_text(self, text: str) -> None:
        """Message texte libre : valeur attendue d'un paramètre, sinon le tableau de bord revient en bas."""
        if text.strip() == self.MENU_TEXT:                     # Bot 4.8.7 : le bouton du bas n'est jamais une réponse
            self.cancel_inputs()
            await self.cmd_start()
            return
        # Bot 4.8.9 : même texte reçu deux fois (double envoi, réseau mobile qui réexpédie) juste après avoir servi de saisie :
        # le doublon est ignoré en silence (avant : « rien n'attendait de saisie » + tableau de bord ramené).
        last_t, last_at = getattr(self, "_last_input", ("", 0.0))
        if not self.awaiting_any and text.strip() and text.strip() == last_t and time.monotonic() - last_at < 15:
            log.info("texte reçu en double juste après une saisie : ignoré")
            return
        if self.awaiting_any:
            self._last_input = (text.strip(), time.monotonic())
        if self.awaiting_bt:
            await self._bt_text(text)
            return
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
        if self.awaiting_broker:
            await self._broker_text(text)
            return
        if self.awaiting_cmd:
            self.awaiting_cmd = False
            await self._shell_ask(text)
            return
        if self.awaiting_bulk:
            self.awaiting_bulk = False
            await self.bulk_received(text)
            return
        if self.awaiting_token:
            key, self.awaiting_token = self.awaiting_token, ""
            if key in SECRETS:
                await self.set_secret(key, text.strip())
            else:                                               # Bot 4.2.0 : mots de passe (chiffrement, import, restauration)
                await self._password_received(key, text.strip())
            return
        if self.awaiting_import:
            self.awaiting_import = False
            await self.preset_import(text)
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
            # preset : sa cible « preset:… » n'est pas un compte (avant : valeur refusée « envoie un nombre » pour un preset)
            inst = pui.PresetTarget(iid[len(pui.PREFIX):]) if iid.startswith(pui.PREFIX) else act.target(self.ctx, iid)
            prm = pu.cat.PARAMS[idx]
            num = pu.num_of(text)
            if inst is None or num is None or prm[3] not in ("int", "dbl"):
                self.dash.flash("❌ Valeur non reconnue : envoie un nombre.")
                await self.dash.refresh(force=True)
                return
            self._bg(self._prm_apply(inst, idx, pu.fmt_num(prm, num)))
            return
        if text.strip():                                        # Bot 4.8.8 : rien n'était attendu → dit clairement
            self.dash.flash("💬 Message reçu, mais rien n'attendait de saisie (étape annulée par un autre bouton ou par un "
                            "redémarrage du bot). Pour une commande : 🛠 Système → 🖥 Serveur → 💻, puis colle-la.", 120)
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
        elif verb == "maintoff":                                  # 🔧 retirer la maintenance (annule la mise à jour en cours)
            dep = ctx.deploy
            if dep.running and dep.current_iid == inst.id:
                if dep.abort(inst.id):
                    dash.flash("⏹ Mise à jour annulée : maintenance retirée, le robot repart. Les comptes suivants ne sont pas mis à jour.")
                else:
                    dash.flash("⚠️ Trop tard : le nouvel EA est en cours d'installation sur ce compte (1 à 3 min). Attends la fin.")
            else:
                self._bg(self._maint_off(inst))
                dash.flash("⏳ Retrait de la maintenance…")
        elif verb == "recon":                                     # normalement capté par l'assistant (ConversationHandler)
            dash.flash("🔑 Utilise le bouton Reconnecter pour lancer l'assistant.")
        await dash.refresh(force=True)
        return ""

    async def _maint_off(self, inst) -> None:
        """Hors déploiement (déploiement interrompu, maintenance restée active) : l'EA reprend, le watchdog le surveille."""
        self.ctx.db.set_desired(inst.id, DEMARRE)
        if self.ctx.mgr.is_running(inst):
            r = await act.send_cmd(self.ctx, inst, "MAINTENANCE_OFF")
            msg = ("✅ Maintenance retirée : le robot peut de nouveau ouvrir des positions." if r.ok
                   else f"⚠️ Le bot ne surveille plus ce compte comme en maintenance, mais l'EA n'a pas répondu : {esc(r.message)}")
        else:
            ok = self.ctx.mgr.start(inst, set_desired=False)
            msg = "✅ Maintenance retirée, terminal relancé." if ok else "❌ Dossier du terminal introuvable."
        log.info("maintenance retirée à la main sur %s", inst.label)
        self.dash.flash(msg)
        await self.dash.refresh(force=True)

    # ------------------------------------------------------------------ 🔑 terminal de test (optimiseur)
    def _bt_servers(self) -> list[str]:
        seen: list[str] = []
        for s in list(self.ctx.cfg.mt5.servers) + [i.server for i in self.ctx.db.list()]:
            if s and s not in seen:
                seen.append(s)
        return seen

    async def _bt_cb(self, sub: str, p: list[str]) -> str | None:
        arg = p[2] if len(p) > 2 else ""
        d = self.dash
        if sub == "btx":
            d.flash("⏹ Arrêt du test demandé…" if self.bt.cancel() else "Aucun test en cours.")
        elif sub == "btc":                                  # configuration du compte du testeur
            if self.bt.busy() or self.optim.running:
                d.flash("⏳ Attends la fin du test en cours.")
            else:
                self.awaiting_bt = "login"
                d.bt_wiz = ("login", {})
                d.goto("btw")
        elif sub == "btsv":
            step, data = d.bt_wiz
            if arg == "new":
                self.awaiting_bt = "server_text"
                d.bt_wiz = ("server_text", data)
            elif arg.isdigit() and int(arg) < len(data.get("servers", [])):
                data["server"] = data["servers"][int(arg)]
                self.awaiting_bt = "password"
                d.bt_wiz = ("password", data)
            d.goto("btw")
        elif sub == "btq":
            self.awaiting_bt = None
            d.bt_wiz = ("login", {})
            d.goto("opt")
        return None

    async def _bt_text(self, text: str) -> None:
        from core.ids import valid_login, valid_server
        step, self.awaiting_bt = self.awaiting_bt, None
        d = self.dash
        if step in ("bm_lo", "bm_hi", "bm_date"):            # Bot 4.13.0 : 🧪 backtest MT5 (minimum, maximum, date de début)
            await self._bm_text(step, text)
            return
        if step == "login":
            v = text.strip()
            if not valid_login(v):
                self.awaiting_bt = "login"
                d.flash("❌ Numéro de compte invalide (chiffres uniquement).")
            else:
                d.bt_wiz = ("server", {"login": int(v), "servers": self._bt_servers()})
                self.awaiting_bt = "server_text" if not d.bt_wiz[1]["servers"] else None
                if self.awaiting_bt:
                    d.bt_wiz = ("server_text", d.bt_wiz[1])
            d.goto("btw")
        elif step == "server_text":
            v = text.strip()
            data = d.bt_wiz[1]
            if not valid_server(v):
                self.awaiting_bt = "server_text"
                d.flash("❌ Nom de serveur invalide.")
            else:
                data["server"] = v
                self.awaiting_bt = "password"
                d.bt_wiz = ("password", data)
            d.goto("btw")
        elif step == "password":
            data = d.bt_wiz[1]
            d.bt_wiz = ("login", {})
            d.goto("opt")
            if text and "login" in data and "server" in data:
                self._bg(self._bt_setup(int(data["login"]), data["server"], text))
        await d.refresh(force=True)

    async def _bt_notify(self, run) -> None:
        """Message de suivi du backtest : édité en place (au plus toutes les 5 s, et à chaque étape importante)."""
        views = self.ctx.views()
        cur = next((v.st.devise for v in views if v.st and v.st.devise), "")
        text = render.bt_progress_text(run, cur)
        last_t, last_text = self._bt_last
        if text == last_text or (not run.done and time.time() - last_t < 5 and run.progress is not None):
            return
        kb = [[("⏹ Arrêter le backtest", "d:btx")]] if not run.done else [[("✖ Fermer", "d:close")]]
        self._bt_last = (time.time(), text)
        if self._bt_msg is None:
            self._bt_msg = await self.sender.send(self.chat_id, text, kb, True)
            return
        from .ui import EditResult
        r = await self.sender.edit(self.chat_id, self._bt_msg, text, kb)
        if r == EditResult.GONE:
            self._bt_msg = await self.sender.send(self.chat_id, text, kb, True)

    async def _bt_setup(self, login: int, server: str, password: str) -> None:
        self._bt_msg, self._bt_last = None, (0.0, "")
        self.ctx.db.add_action("", f"Terminal de test : configuration du compte {login} ({server})", True)
        run = await self.bt.setup(login, server, password, currency=self.ctx.cfg.alerts.expected_currency or "EUR",
                                  notify=self._bt_notify, poll_s=self.ctx.cfg.backtest.poll_s)
        del password
        if run.ok:
            await self.sender.send(self.chat_id, f"✅ <b>Terminal de test prêt</b> : compte {login} · {esc(server)}.\n"
                                                 "Le mot de passe est mémorisé par MT5. L'optimiseur et 🧪 Backtest MT5 l'utilisent.",
                                   [[("🧪 Backtest MT5", "d:bm"), ("🤖 Optimiseur", "d:opt")], [("✖ Fermer", "d:close")]], False)
        else:
            await self.sender.send(self.chat_id, f"❌ <b>Configuration du terminal de test impossible</b>\n{esc(run.error)}",
                                   [[("🔑 Réessayer", "d:btc"), ("✖ Fermer", "d:close")]], False)
        await self.dash.refresh(force=True)

    async def _bt_inputs(self, src: str) -> tuple[dict[str, str], str, float, str, int, float, float] | str:
        """Réglages et paramètres du testeur depuis la source choisie. Texte d'erreur si impossible."""
        import json as _json
        insts = self.ctx.db.list()
        params: dict[str, str] = {}
        ref = None
        if src.startswith("preset:"):
            params = dict(self.dash.store.presets.get(src[7:], {}))
            if not params:
                return "Preset introuvable."
        elif src != "base":
            ref = self.ctx.db.get(src) if src else (insts[0] if insts else None)
            if ref is not None:
                raw = await pui.read_raw(self.ctx, ref)
                if not raw:
                    return f"Impossible de lire les réglages de {ref.label} (terminal arrêté ?). Choisis un preset ou BASE."
                params = raw
        st_ref = self.ctx.status.read(ref.id) if ref is not None else None
        if st_ref is None:
            st_ref = next((v.st for v in self.ctx.views() if v.st), None)
        currency = (st_ref.devise if st_ref and st_ref.devise else "") or self.ctx.cfg.alerts.expected_currency or "EUR"
        deposit = float(st_ref.seuil_reprise) if st_ref and st_ref.seuil_reprise else 125.0
        leverage = int(st_ref.levier) if st_ref and st_ref.levier else self.ctx.cfg.backtest.leverage
        comm = slip = 0.0
        for i in ([ref] if ref is not None else []) + insts:
            try:
                e = _json.loads(self.ctx.db.meta_get(f"labo_etat_{i.id}", "") or "{}")
            except ValueError:
                e = {}
            if e:
                comm, slip = float(e.get("comm", 0) or 0), float(e.get("slip", 0) or 0)
                break
        label = (f"preset {src[7:]}" if src.startswith("preset:") else "réglages de BASE (.set)" if ref is None
                 else f"compte {ref.label} (réglages actuels)")
        return params, label, deposit, currency, leverage, comm, slip

    # ------------------------------------------------------------------ 🧪 backtest MT5 lancé depuis Telegram (Bot 4.13.0)
    async def _bm_cb(self, sub: str, p: list[str]) -> str | None:
        from core import backtest as bt
        from . import backtest_ui as bui
        d, s = self.dash, self.dash.bm
        arg = p[2] if len(p) > 2 else ""
        if self.awaiting_bt in ("bm_lo", "bm_hi", "bm_date") and sub not in ("bmlo", "bmhi", "bmdt"):
            self.awaiting_bt = None                          # autre bouton : saisie abandonnée
        if sub == "bm":
            d.goto("bm")
        elif sub == "bmp":
            s.next_preset()
            d.goto("bm")
        elif sub == "bmd" and arg.isdigit() and 1 <= int(arg) <= bt.MAX_DAYS:
            s.days = int(arg)
            s.save()
            d.goto("bm")
        elif sub == "bmdt":
            self.awaiting_bt = "bm_date"
            d.flash("📅 Envoie la <b>date de début</b> (JJ/MM/AAAA), entre hier et il y a un an.", 180)
            d.goto("bm")
        elif sub in ("bmlo", "bmhi"):
            lo, hi = s.lo_hi()
            self.awaiting_bt = "bm_lo" if sub == "bmlo" else "bm_hi"
            d.flash("⬇️ Envoie le <b>minimum</b> (solde de départ, et recharge après un cycle perdu), ex. 200. Actuel : "
                    f"{bui._num(lo)}." if sub == "bmlo" else
                    "⬆️ Envoie le <b>maximum</b> (atteint : retrait jusqu'au minimum, cycle gagné), ex. 500. Actuel : "
                    f"{bui._num(hi)}.", 180)
            d.goto("bm")
        elif sub == "bmrs":
            s.reset_limits()
            d.flash("↺ Minimum et maximum du preset.")
            d.goto("bm")
        elif sub == "bmgo":
            if self.bt.busy() or self.optim.running:
                d.flash("⏳ Un test ou une optimisation est déjà en cours : attends sa fin.")
            elif not self.bt.configured():
                d.flash("🔑 Configure d'abord le terminal de test.")
            elif not s.preset_name():
                d.flash("❌ Aucun preset.")
            else:
                self._bg(self.start_manual_backtest())
                return "Backtest lancé"
            d.goto("bm")
        elif sub == "bml":
            d.goto("bml")
        elif sub == "bmr" and arg.isdigit():
            await self._bm_send_result(int(arg), with_files=False)
            return ""
        elif sub == "bmg" and arg.isdigit():
            await self._bm_chart(int(arg))
            return ""
        return None

    async def _bm_text(self, step: str, text: str) -> None:
        from core import backtest as bt
        d, s = self.dash, self.dash.bm
        if step == "bm_date":
            days = bt.parse_date(text)
            if days is None:
                self.awaiting_bt = "bm_date"
                d.flash("❌ Date non reconnue : JJ/MM/AAAA, entre hier et il y a un an.", 180)
            else:
                s.days = days
                s.save()
                d.flash(f"📅 Début du backtest : {bt.human_date(bt.date_range(days)[0])}.")
        else:
            err = s.set_limit("lo" if step == "bm_lo" else "hi", text)
            if err:
                self.awaiting_bt = step
                d.flash(err, 180)
            else:
                lo, hi = s.lo_hi()
                d.flash(f"✅ Minimum {lo:g} · maximum {hi:g} pour les prochains backtests.")
        d.goto("bm")
        await d.refresh(force=True)

    async def start_manual_backtest(self) -> None:
        from core import backtest as bt
        from mt5.backtester import Run
        from . import backtest_ui as bui
        s = self.dash.bm
        name = s.preset_name()
        inputs = await self._bt_inputs("preset:" + name)
        if isinstance(inputs, str):
            self.dash.flash("❌ " + inputs)
            await self.dash.refresh(force=True)
            return
        _, _, _, currency, leverage, comm, slip = inputs
        params = s.params()
        lo, hi = s.lo_hi()
        d_from, d_to = bt.date_range(s.days)
        label = ("preset ⭐ " if name == self.dash.store.active else "preset ") + name
        rid = self.ctx.db.bt_create(bui.MANUAL_KIND, d_from, d_to, "reel", label)
        run = Run(rid, bui.MANUAL_KIND, d_from, d_to, "reel", f"{label} · min {bui._num(lo)} · max {bui._num(hi)}")
        run.note(f"📋 {label} · départ {lo:.2f} {currency} · maximum {hi:.2f} · levier 1:{leverage}")
        self.ctx.db.add_action("", f"Backtest #{rid} lancé ({label}, {bt.human_date(d_from)} → {bt.human_date(d_to)}, "
                                   f"min {bui._num(lo)}, max {bui._num(hi)})", True)
        self._bt_msg, self._bt_last = None, (0.0, "")
        self.dash.flash(f"🧪 Backtest #{rid} lancé : suivi dans le message ci-dessous.")
        self.dash.goto("bm")
        await self.dash.refresh(force=True)
        run = await self.bt.execute(run, params=params, deposit=lo, currency=currency, leverage=leverage, comm=comm, slip=slip,
                                    notify=self._bt_notify, poll_s=self.ctx.cfg.backtest.poll_s)
        status = "ok" if run.ok else ("arrete" if run.cancelled else "erreur")
        if run.result is not None:
            run.result.params = dict(params)
            run.result.lo, run.result.hi = lo, hi
            run.result.deposit = run.result.deposit or lo
        self.ctx.db.bt_update(rid, status, run.result.to_json() if run.result else "", run.error)
        if run.ok:
            await self._bm_send_result(rid, with_files=True, duration_s=time.time() - run.started)
        else:
            await self.sender.send(self.chat_id, f"❌ <b>Backtest #{rid}</b> : {esc(run.error or 'échec')}",
                                   [[("🧪 Backtest MT5", "d:bm"), ("✖ Fermer", "d:close")]], False)
        await self.dash.refresh(force=True)

    def _bm_result(self, rid: int):
        from core import backtest as bt
        row = self.ctx.db.bt_get(rid)
        if row is None or not row["result"]:
            return None, None
        return row, bt.Result.from_json(row["result"])

    def _bm_cur(self) -> str:
        return next((v.st.devise for v in self.ctx.views() if v.st and v.st.devise), "") or self.ctx.cfg.alerts.expected_currency or ""

    async def _bm_send_result(self, rid: int, *, with_files: bool, duration_s: float = 0) -> None:
        row, res = self._bm_result(rid)
        if res is None:
            self.dash.flash(f"Résultat du backtest #{rid} introuvable.")
            await self.dash.refresh(force=True)
            return
        text = render.bt_manual_result_text(rid, row["source"], res, self._bm_cur(), duration_s)
        kb = [[("📈 Graphique", f"d:bmg:{rid}"), ("🧪 Nouveau backtest", "d:bm")], [("✖ Fermer", "d:close")]]
        await self.sender.send(self.chat_id, text, kb, False)
        await self._bm_chart(rid)
        if with_files:
            await self._bm_mt5_report(rid)

    async def _bm_chart(self, rid: int) -> None:
        from .charts import backtest_chart
        row, res = self._bm_result(rid)
        png = None
        if res is not None:
            title = (f"Backtest #{rid} · {res.cycles_won} cycle(s) gagné(s), {res.cycles_lost} perdu(s) · "
                     f"gain net {res.net:+.0f} {self._bm_cur()}".strip())
            png = await asyncio.to_thread(backtest_chart, res, title, self._bm_cur())
        if png:
            await self.sender.photo(self.chat_id, png, f"🧪 Backtest #{rid} : solde, cycles (▲ gagné · ▼ perdu) et gain net cumulé", True)
        else:
            self.dash.flash("📈 Pas assez de jours simulés pour un graphique.")
            await self.dash.refresh(force=True)

    async def _bm_mt5_report(self, rid: int) -> None:
        """Rapport du testeur MT5 (Report=reports\\bt_<id>) : page HTML en pièce jointe et graphique du testeur, puis effacés."""
        folder = self.bt.folder / "reports"
        try:
            files = sorted(folder.glob(f"bt_{rid}.*")) + sorted(folder.glob(f"bt_{rid}-*.png"))
        except OSError:
            files = []
        for f in files:
            try:
                data = f.read_bytes()
            except OSError:
                continue
            if f.suffix.lower() in (".htm", ".html") and data:
                await self.sender.document(self.chat_id, data, f"backtest_{rid}_mt5{f.suffix.lower()}",
                                           f"📎 Rapport complet du testeur MT5 (backtest #{rid}) : à ouvrir dans un navigateur")
            elif f.suffix.lower() == ".png" and data and f.stem == f"bt_{rid}":
                await self.sender.photo(self.chat_id, data, f"🖥 Graphique du testeur MT5 (backtest #{rid})", True)
        for f in files:
            try:
                f.unlink()
            except OSError:
                pass

    async def _opt_cb(self, sub: str, p: list[str]) -> str | None:
        from .optim import GUIDE
        from .system import tail
        o, d = self.optim, self.dash
        arg = p[2] if len(p) > 2 else ""
        if sub == "opt":
            d.goto("opt")
        elif sub == "opton":
            o.set_on(not o.on)
            d.flash("✅ Optimiseur activé." if o.on else "⏸ Optimiseur suspendu : plus aucun réglage changé automatiquement.")
        elif sub == "opth":
            d.flash(f"🕐 L'optimiseur se lancera chaque nuit à {o.next_hour():02d}:00.")
        elif sub == "optgo":
            if o.running or self.bt.busy():
                d.flash("⏳ Un test ou une optimisation est déjà en cours.")
            else:
                self.ctx.db.add_action("", "Optimiseur lancé à la main", True)
                self._bg(o.run(manual=True))
                d.flash("🤖 Optimisation lancée : résultat dans 1 à 3 h (📜 Journal pour suivre).")
        elif sub == "optj":
            txt = tail(o.path, 60, 3800)
            await self.sender.send(self.chat_id, "📜 <b>Journal de l'optimiseur</b>\n" + (f"<pre>{esc(txt)}</pre>" if txt else "<i>vide</i>"),
                                   [[("📎 Fichier complet", "d:optf"), ("✖ Fermer", "d:close")]], True)
            return ""
        elif sub == "optf":
            try:
                data = o.path.read_bytes()
            except OSError:
                data = b""
            if data:
                await self.sender.document(self.chat_id, data, "optim.log", "Journal complet de l'optimiseur")
            else:
                d.flash("Journal vide.")
            return ""
        elif sub == "optg":
            await self.sender.send(self.chat_id, GUIDE, [[("✖ Fermer", "d:close")]], True)
            return ""
        elif sub == "ops":                                      # Bot 3.6.0 : 🔁 série d'optimisations
            if o.series_active:
                d.flash(f"🔁 Une série est déjà en cours ({o.series_i}/{o.series_n}).")
            elif not arg:
                d.goto("ops")
            elif arg.isdigit() and int(arg) in o.SERIES_CHOICES:
                if o.running or self.bt.busy():
                    d.flash("⏳ Un test ou une optimisation est en cours : réessaie après.")
                    return None
                n = int(arg)
                import functools
                return await self._ask(f"🔁 <b>Lancer une série de {n} optimisations ?</b>\n"
                                       "Elles s'enchaînent dans l'ordre du cycle (🛟 Sécurité comprise si besoin), chacune applique "
                                       "ses changements à Martin ⭐ et aux comptes. Compte environ 15 à 30 min par optimisation.\n"
                                       "L'optimisation de la nuit est sautée pendant la série. Arrêt possible à tout moment.",
                                       functools.partial(self._opt_series, n), ("opt",), f"🔁 Lancer {n}")
        elif sub == "opsx":
            if o.series_active:
                o.series_stop = True
                self.ctx.db.add_action("", "Série d'optimisations : arrêt demandé", True)
                d.flash("⏹ La série s'arrêtera après l'optimisation en cours.")
        elif sub == "optr":                                     # Bot 3.3.0 : ♻️ réinitialisation
            if o.running or self.bt.busy():
                d.flash("⏳ Un test ou une optimisation est en cours : réessaie après.")
                return None
            return await self._ask("♻️ <b>Réinitialiser l'optimiseur ?</b>\n"
                                   "• Cycle remis à zéro (prochaine nuit : 🧱 Grille et lots, compteurs effacés).\n"
                                   f"• Réglages remis au preset <b>{esc(o.BASE_PRESET)}</b> (valeurs d'avant la 1re optimisation, "
                                   "créé la 1re fois) sur tous les comptes lancés et dans le preset ⭐.\n"
                                   "Coupures et mode continu non modifiés.", self._opt_reset, ("opt",), "♻️ Réinitialiser")
        elif sub == "opu" and arg.isdigit():
            rid = int(arg)
            return await self._ask("↩️ <b>Annuler ce changement de l'optimiseur ?</b>\nL'ancienne valeur est remise sur tous les comptes lancés "
                                   "et dans le preset ⭐.", lambda: self._opt_revert(rid), ("opt",), "↩️ Annuler le changement")
        return None

    async def _opt_series(self, n: int) -> None:
        self.ctx.db.add_action("", f"Série de {n} optimisations lancée", True)
        self._bg(self.optim.run_series(n))
        self.dash.goto("opt")
        self.dash.flash(f"🔁 Série de {n} optimisations lancée : suivi ici et dans 📜 Journal.")
        await self.dash.refresh(force=True)

    async def _opt_reset(self) -> None:
        msg = await self.optim.reset()
        self.ctx.db.add_action("", "Optimiseur réinitialisé (cycle + preset de base)", True)
        await self.sender.send(self.chat_id, msg, [[("🤖 Optimiseur", "d:opt"), ("✖ Fermer", "d:close")]], False)
        self.dash.goto("opt")
        await self.dash.refresh(force=True)

    async def _opt_revert(self, rid: int) -> None:
        msg = await self.optim.revert(rid)
        self.ctx.db.add_action("", f"Optimiseur : changement #{rid} annulé", True)
        self.dash.flash(msg)
        await self.dash.refresh(force=True)

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

    async def _terminals(self, insts, verb: str, title: str = "") -> list[str]:
        """Plusieurs terminaux : 🛑 arrêter / 🚀 lancer / 🔄 redémarrer tous (aussi pour les mises à jour du bot).
        Bot 3.12.0 : fermeture de TOUS en même temps (propre, puis arrêt forcé au bout de kill_after_s), lancement PAR GROUPES
        de launch_group (2) : le groupe suivant n'est lancé qu'une fois ceux du groupe en ligne et connectés (vert).
        Renvoie les identifiants réussis (arrêt : terminal vérifié fermé ; lancement : en ligne)."""
        from storage.db import MAINTENANCE
        label = title or {"rst": "Redémarrage", "start": "Lancement", "stop": "Arrêt"}[verb]
        n = len(insts)
        line: dict[str, str] = {i.id: "en attente" for i in insts}
        good: list[str] = []
        mid = await self.sender.send(self.chat_id, f"⏳ <b>{label}</b> de {n} terminal(aux)…", None, True)
        last_show = [0.0]

        async def show(force: bool = False) -> None:
            if not force and time.monotonic() - last_show[0] < 3:              # au plus une édition toutes les 3 s
                return
            last_show[0] = time.monotonic()
            txt = f"⏳ <b>{label}</b> · {len(good)}/{n}\n" + "\n".join(f"<b>{esc(i.label)}</b> : {esc(line[i.id])[:150]}" for i in insts)
            try:
                await self.sender.edit(self.chat_id, mid, txt[-3900:], None)
            except Exception:
                log.debug("progression des terminaux", exc_info=True)

        # 1) arrêt : tous en même temps (fermer libère du CPU, inutile d'attendre)
        to_start = list(insts)
        if verb in ("stop", "rst"):
            async def stop_one(i) -> None:
                line[i.id] = "🔌 fermeture…"
                try:
                    if verb == "stop":
                        r = await act.action_stop(self.ctx, i)
                    else:                                    # redémarrage : MAINTENANCE pendant l'arrêt (la surveillance n'y touche pas)
                        r = await self.ctx.mgr.stop(i, force=False, set_desired=MAINTENANCE)
                        if r == "attente_cloture":
                            self.ctx.db.set_desired(i.id, DEMARRE)
                            r = "⏳ Clôture à 500 en cours : redémarrage reporté."
                        act.journal(self.ctx, i, "TERMINAL redémarrer", not r.startswith("⏳"), r)
                    if not await asyncio.to_thread(self.ctx.mgr.is_running, i):   # vérifié : bien fermé
                        if verb == "stop":
                            good.append(i.id)
                        line[i.id] = "🔌 fermé" if verb == "rst" else str(r)
                    else:
                        line[i.id] = f"⚠️ toujours ouvert ({r})"
                        if verb == "rst" and i in to_start:
                            to_start.remove(i)
                except Exception as exc:
                    log.exception("terminal %s", i.label)
                    line[i.id] = f"❌ {exc}"
                    if i in to_start:
                        to_start.remove(i)
                await show()
            await show(True)
            await asyncio.gather(*(stop_one(i) for i in insts))
            await show(True)
        # 2) lancement par groupes : chaque groupe attendu (vert) avant le suivant
        if verb in ("start", "rst"):
            timeout = float(self.ctx.cfg.timing.wait_online_limit_s)      # Bot 3.11.0 : 0 = le temps qu'il faut
            per = max(1, int(self.ctx.cfg.timing.launch_group))

            from mt5.manager import Slots
            slots = Slots(per)

            async def start_one(i) -> None:
                slot = await slots.take()                  # Bot 3.14.1 : file glissante (place libérée dès l'EA chargé)
                line[i.id] = "🚀 lancement…"
                await show()
                t0 = time.time()
                try:
                    r = await act.action_start(self.ctx, i)
                    if r.startswith("🚀"):
                        async def tick(sec: int, last: str, i=i) -> None:
                            line[i.id] = f"attente de l'EA {sec} s ({last})…"
                            await show()
                        ok, r = await act.wait_online(self.ctx, i, t0, timeout, tick, slot)
                        if ok:
                            good.append(i.id)
                    elif r.startswith("ℹ️"):                    # déjà lancé
                        good.append(i.id)
                except Exception as exc:
                    log.exception("terminal %s", i.label)
                    r = f"❌ {exc}"
                finally:
                    slot.free()
                line[i.id] = str(r)
                await show()
            await asyncio.gather(*(start_one(i) for i in to_start))
            for i in to_start:                                     # redémarrage interrompu : jamais laissé en MAINTENANCE
                cur = self.ctx.db.get(i.id)
                if verb == "rst" and cur is not None and cur.desired_state == MAINTENANCE:
                    self.ctx.db.set_desired(i.id, DEMARRE)
        await self.sender.edit(self.chat_id, mid, f"🖥 <b>{label}</b> terminé · {len(good)}/{n}\n"
                               + "\n".join(f"<b>{esc(i.label)}</b> : {esc(line[i.id])[:150]}" for i in insts),
                               [[("✖ Fermer", "d:close")]])
        await self.dash.refresh(force=True)
        return good

    # ------------------------------------------------------------------ 💾 presets centraux
    def _running(self) -> list:
        return [i for i in self.ctx.db.list() if self.ctx.mgr.is_running(i)]

    async def filters_alerts_off(self) -> None:
        """2.15.0, une seule fois par compte : coupe les alertes « filtres OK / séries suspendues » (n_filters), redondantes avec
        le tableau de bord. Fait aussi dans le preset ⭐ actif. Réactivées ensuite à la main : respecté (jamais recoupées)."""
        store = self.dash.store
        if store.active and not self.ctx.db.meta_get("nfilters_off_preset"):
            if store.presets[store.active].get("n_filters") not in (None, "0"):
                store.set_value(store.active, "n_filters", "0")
                self.sync_base_set()
            self.ctx.db.meta_set("nfilters_off_preset", "1")
        running = await asyncio.to_thread(self._running)               # parcours des processus : hors de la boucle du bot
        todo = [i for i in running if not self.ctx.db.meta_get(f"nfilters_off_{i.id}")]
        if not todo:
            return
        res = await asyncio.gather(*[act.send_cmd(self.ctx, i, "SET", ["n_filters", "0"]) for i in todo])
        for i, r in zip(todo, res):
            if "Appliqué" in (r.message or ""):
                self.ctx.db.meta_set(f"nfilters_off_{i.id}", "1")
                log.info("alertes des filtres coupées sur %s", i.label)

    async def _preset_cb(self, p: list[str]) -> str:
        """s:l liste · s:o:<P> preset · s:new[:<P>] modèle · s:src:<compte>[:<P>] · s:ap:<P> appliquer · s:del:<P> · s:chk."""
        store, dash = self.dash.store, self.dash
        sub = p[1] if len(p) > 1 else "l"
        name = pui.find(store, p[2]) if len(p) > 2 and sub in ("o", "ap", "del", "new", "cmp", "exp") else ""
        self.awaiting_preset = None
        self.awaiting_import = False
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
        elif sub in ("exp", "expa"):                                     # Bot 3.16.0 : 📤 fichier texte envoyé dans la conversation
            names = [name] if sub == "exp" and name else ([] if sub == "exp" else store.names())
            if not names:
                dash.flash("Aucun preset à exporter.")
            else:
                await self._preset_export(names)
                return ""
        elif sub == "ghr":                                              # Bot 3.17.0 : ☁️ restaurer la sauvegarde GitHub
            self._bg(self.preset_restore_github())
            return ""
        elif sub == "imp":
            self.awaiting_import = True
            dash.goto("psl")
            dash.flash("📥 Envoie le <b>fichier de presets</b> exporté par l'autre bot (transfère-le), ou colle son texte. "
                       "Les presets du même nom seront remplacés après confirmation.", 300)
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

    # ---- Bot 3.18.0 : ☁️ jeton de sauvegarde, 🖥 gestion du serveur (Linux)
    # ---- Bot 3.20.0 : 📄 création de plusieurs comptes (fichier texte)
    async def bulk_received(self, text: str) -> None:
        from . import bulk
        rows, errors = bulk.parse(text)
        del text
        if self._bulk is not None:
            self._bulk.forget()
        b = bulk.new_batch(rows, errors, self.ctx.db.exists_active)
        self._bulk = b if b.rows else None
        kb = ([[(f"✅ Créer {len(b.rows)} compte(s)", f"d:bkgo:{b.token}:{bulk.PARALLEL}")], [("✖ Annuler", "d:bkno")]]
              if b.rows else [[("✖ Fermer", "d:close")]])
        b.msg = await self.sender.send(self.chat_id, bulk.summary(b) if (b.rows or b.errors or b.skipped)
                                       else "📄 Aucun compte trouvé dans ce fichier (format : numéro:mot de passe:serveur).",
                                       kb, False)
        self.ctx.db.add_action("", "COMPTES (FICHIER) REÇUS", True, f"{len(b.rows)} à créer, {len(b.skipped)} ignoré(s)")

    async def _bulk_cb(self, sub: str, p: list[str]) -> str:
        from . import bulk
        b = self._bulk
        if sub == "bkno" or b is None or len(p) < 4 or p[2] != b.token or time.monotonic() - b.created_at > bulk.TTL_S:
            if b is not None:
                b.forget()
                await self.sender.edit(self.chat_id, getattr(b, "msg", None), "✖ Création des comptes annulée "
                                       "(mots de passe oubliés).", None) if getattr(b, "msg", None) else None
            self._bulk = None
            return "Annulé" if sub == "bkno" else "Expiré : renvoie le fichier"
        self._bulk = None
        n = int(p[3]) if p[3].isdigit() else 2
        mid = b.msg
        self.ctx.db.add_action("", "CRÉATION DE COMPTES EN LOT", True, f"{len(b.rows)} compte(s), {n} à la fois")

        async def create(login, pw, server, progress):
            return await self.ctx.creator.create(login, pw, server, "", progress, exclusive=False)

        async def edit(txt: str) -> None:
            await self.sender.edit(self.chat_id, mid, txt, None)

        async def go() -> None:
            await bulk.run(b, n, create, self.preset_for_new, edit, self.remember_server)
            await self.dash.refresh(force=True)
        self._bg(go())
        return "C'est parti"

    async def set_secret(self, key: str, value: str) -> None:
        """Clé de .env reçue par message (déjà effacé de la conversation) : vérifiée, écrite, prise en compte tout de suite."""
        from core import envfile
        from .system import BOT_ROOT
        self.dash.goto("bkp" if key == "BACKUP_TOKEN" else "key")
        attr, hint, pattern = SECRETS[key]
        clear = value.lower() in ("effacer", "supprimer", "-")
        if not clear and not re.fullmatch(pattern, value):
            self.dash.flash(f"❌ Ce n'est pas un {key} valide ({hint}) : touche à nouveau 🔑 pour réessayer.")
            await self.dash.show()
            return
        value = "" if clear else value
        try:
            await asyncio.to_thread(envfile.set_key, BOT_ROOT / ".env", key, value)
        except (OSError, ValueError) as exc:
            self.dash.flash(f"❌ .env non modifié : {esc(str(exc))}")
            await self.dash.refresh(force=True)
            return
        setattr(self.ctx.cfg, attr, value)
        self.ctx.db.add_action("", f"{key} {'RETIRÉ' if clear else 'ENREGISTRÉ'}", True, "")
        msg = f"✅ {key} {'retiré' if clear else 'enregistré'}."
        if key == "BACKUP_TOKEN" and value:
            from core import envfile as _ef
            self.ctx.cfg.backup_repo = ""                       # nouveau jeton : dépôt retrouvé ou créé sur SON compte
            try:
                await asyncio.to_thread(_ef.set_key, BOT_ROOT / ".env", "BACKUP_REPO", "")
            except (OSError, ValueError):
                pass
            self.ctx.db.meta_set("ghb_hash", "")
            self.ctx.db.meta_set("ghb_err", "")
            self.dash.goto("bkp")
            self.dash.flash("⏳ Jeton reçu : je cherche ou crée ton dépôt privé…")
            await self.dash.show()
            if not await self.backup_ready():
                msg = f"❌ Dépôt de sauvegarde : {esc(self.ctx.db.meta_get('ghb_err') or 'indisponible')}"
            elif not self.ctx.cfg.backup_key and self.ctx.db.meta_get("backup_plain") != "1":
                self.awaiting_token = "BACKUP_PASSWORD"
                msg = f"✅ Jeton OK, dépôt privé <b>{esc(self.ctx.cfg.backup_repo)}</b> prêt. Étape suivante : le mot de passe."
            else:
                await self.backup_presets()
                await self.full_backup()
                err = self.ctx.db.meta_get("ghb_err") or self.ctx.db.meta_get("fbk_err")
                msg = f"❌ {esc(err)}" if err else f"✅ Sauvegarde active dans <b>{esc(self.ctx.cfg.backup_repo)}</b>."
            self.dash.flash(msg, 300)
            await self.dash.show()
            return
        self.dash.flash(msg)
        await self.dash.show()

    async def _server_cb(self, sub: str, p: list[str]) -> str:
        from core import server as sv
        dash = self.dash
        if sub == "srv":
            dash.goto("srv")
            dash.srv = (None, getattr(dash, "srv", (None, {}, True))[1], getattr(dash, "srv", (None, {}, True))[2])
            await dash.refresh(force=True)
            st, auto, sudo = await asyncio.gather(asyncio.to_thread(sv.stats), asyncio.to_thread(sv.autostart),
                                                  asyncio.to_thread(sv.sudo_ready))
            dash.srv = (st, auto, sudo)
        elif sub == "sva":
            ok, msg = await asyncio.to_thread(sv.enable_own)
            dash.flash("✅ Bot et écran virtuel relancés au démarrage du VPS." if ok else "❌ " + esc(msg))
            return await self._server_cb("srv", ["d", "srv"])
        elif sub == "wvw":                                       # Bot 3.21.0 : WebView2 des terminaux
            w = getattr(self.ctx.mgr.procs, "wine", None)
            if w is not None:
                w.webview = not w.webview
                self.ctx.db.meta_set("webview", "on" if w.webview else "off")
                self.ctx.db.add_action("", f"WEBVIEW2 {'ACTIVÉ' if w.webview else 'COUPÉ'}", True, "")
                dash.flash(("🧩 WebView2 réactivé" if w.webview else "🧩 WebView2 coupé")
                           + " : pris en compte au prochain redémarrage de chaque terminal (🔄 sur un compte, ou ⚡ Actions → redémarrer tous).")
            dash.goto("srv")
        elif sub == "rbt":
            return await self._ask("🔁 <b>Redémarrer le VPS ?</b>\nTerminaux et bot coupés ≈ 1 à 3 min ; au retour, le bot et les "
                                   "terminaux repartent seuls. Positions et ordres restent chez le broker.",
                                   self._reboot, ("srv",), "🔁 Redémarrer")
        elif sub == "rup":
            return await self._ask("🔧 <b>Mettre à jour l'installation système ?</b>\nLe serveur télécharge la dernière version validée et "
                                   "relance l'installeur (droits administrateur, services, autorisations), sans question. Comptes, "
                                   "réglages et BASE gardés ; le bot redémarre (≈ 1 à 5 min).", self._root_update, ("sys",), "🔧 Lancer")
        elif sub == "uni":
            return await self._ask("🧨 <b>Tout désinstaller ?</b>\nSupprime de ce VPS : le bot, BASE, tous les comptes MT5, le "
                                   "préfixe Wine, l'historique, les services, les autorisations, le paquet téléchargé, Wine et Xvfb. Rien ne restera de ScalpX.",
                                   self._uninstall_2, ("srv",), "🧨 Continuer")
        await dash.refresh(force=True)
        return ""

    async def _root_update(self) -> None:
        from core import server as sv
        ok, err = await asyncio.to_thread(sv.root_update)
        self.ctx.db.add_action("", "MISE À JOUR DE L'INSTALLATION", ok, err)
        self.dash.goto("sys")
        self.dash.flash("🔧 Mise à jour de l'installation lancée : le bot redémarre dans quelques minutes "
                        "(journal : /var/log/scalpx-root-update.log)." if ok else
                        f"❌ Refusé : {esc(err or 'droits administrateur absents (relance install.sh une fois)')}")
        await self.dash.refresh(force=True)

    async def check_install(self) -> None:
        """Bot 3.19.0 (Linux) : le paquet téléchargé apporte une nouvelle installation système → 🔔 et un message (une fois)."""
        from core import server as sv, update as upd
        need = await asyncio.to_thread(sv.install_needed, upd.find_source(self.ctx.cfg))
        db = self.ctx.db
        if db.meta_get("update_pending"):
            # Bot 4.5.1 : 🔧 installe toujours la DERNIÈRE version (stable) alors que le bot rattrape ses mises à jour une à
            # une : tant qu'il est en retard, les empreintes diffèrent sans raison (cloche 🔧 qui revenait sans cesse).
            need = ""
        if need != db.meta_get("install_pending"):
            db.meta_set("install_pending", need)
            await self.dash.refresh(force=True)
        if need and db.meta_get("install_offered") != need:
            db.meta_set("install_offered", need)
            await self.sender.send(self.chat_id, "🔔 <b>Mise à jour de l'installation système disponible</b>\n"
                                   "Elle touche ce qui demande les droits administrateur (services, autorisations, scripts). "
                                   "Comptes, réglages et BASE sont gardés ; le bot redémarre (≈ 1 à 5 min).",
                                   [[("🔧 Mettre à jour maintenant", "d:rup")], [("⏰ Plus tard (🛠 Système)", "d:close")]],
                                   False)                      # Bot 4.8.9 : avec notification

    async def _reboot(self) -> None:
        from core import server as sv
        self.ctx.db.add_action("", "REDÉMARRAGE DU VPS", True, "")
        await self.sender.send(self.chat_id, "🔁 <b>Redémarrage du VPS…</b> Le bot revient dans quelques minutes.", None, True)
        ok, err = await asyncio.to_thread(sv.reboot)
        if not ok:
            self.dash.goto("srv")
            self.dash.flash(f"❌ Redémarrage refusé : {esc(err or 'droits administrateur absents (relance install.sh)')}")
            await self.dash.refresh(force=True)

    async def _uninstall_2(self) -> None:
        await self._ask("⚠️ <b>Vraiment sûr ?</b>\nTous les comptes devront être recréés sur un autre serveur, et le bot de ce "
                        "VPS ne répondra plus jamais. Les positions restent chez le broker.", self._uninstall_3, ("srv",),
                        "🧨 Oui, je suis sûr")

    UNI_STEP_S = 1.0                                     # compte à rebours de la dernière confirmation (remplacé dans les tests)

    async def _uninstall_3(self) -> None:
        """Dernière confirmation : compte à rebours de 10 s affiché, puis le bouton apparaît (valable 60 s)."""
        import secrets
        tok = secrets.token_hex(4)
        self._uni = (tok, 0.0)
        txt = "🛑 <b>Dernière confirmation</b> : désinstallation complète de ScalpX sur ce VPS, définitive."
        mid = await self.sender.send(self.chat_id, f"{txt}\n\n⏳ Bouton disponible dans <b>10</b> s…",
                                     [[("✖ Annuler", "d:unino")]], False)
        for n in range(9, 0, -1):
            await asyncio.sleep(self.UNI_STEP_S)
            if getattr(self, "_uni", ("", 0))[0] != tok:              # annulé pendant le compte à rebours
                return
            await self.sender.edit(self.chat_id, mid, f"{txt}\n\n⏳ Bouton disponible dans <b>{n}</b> s…", [[("✖ Annuler", "d:unino")]])
        await asyncio.sleep(self.UNI_STEP_S)
        if getattr(self, "_uni", ("", 0))[0] != tok:
            return
        self._uni = (tok, _clock())
        self._uni_msg = mid
        await self.sender.edit(self.chat_id, mid, f"{txt}\n\n👇 Tu peux confirmer (bouton valable 1 min).",
                               [[("🧨 DÉSINSTALLER MAINTENANT", f"d:unigo:{tok}")], [("✖ Annuler", "d:unino")]])

    async def _uninstall_cb(self, sub: str, p: list[str]) -> str:
        tok, ready = getattr(self, "_uni", ("", 0.0))
        mid = getattr(self, "_uni_msg", None)
        if sub == "unino" or not tok:
            self._uni = ("", 0.0)
            if mid:
                await self.sender.edit(self.chat_id, mid, "✖ Désinstallation annulée.", [[("✖ Fermer", "d:close")]])
            return "Annulé"
        if len(p) < 3 or p[2] != tok or not ready or _clock() - ready > 60:
            return "Expiré : recommence depuis 🖥 Serveur"
        self._uni = ("", 0.0)
        if mid:
            await self.sender.edit(self.chat_id, mid, "🧨 <b>Désinstallation confirmée.</b>", None)
        self._bg(self._uninstall_go())
        return "OK"

    async def _uninstall_go(self) -> None:
        from core import server as sv
        self.ctx.db.add_action("", "DÉSINSTALLATION", True, "")
        await self.sender.send(self.chat_id, "🧨 <b>Désinstallation lancée.</b> Le serveur t'envoie chaque étape ici, puis un "
                               "dernier message quand tout est effacé.", None, True)
        ok, err = await asyncio.to_thread(sv.uninstall)
        if not ok:
            self.dash.goto("srv")
            self.dash.flash(f"❌ Désinstallation refusée : {esc(err or 'droits administrateur absents (relance install.sh)')}")
            await self.dash.refresh(force=True)

    async def _reset_confirm2(self) -> None:
        n = len(self.ctx.db.list())
        await self._ask(f"⚠️ <b>Vraiment sûr ?</b>\n{n} compte(s) seront retirés de ce serveur. Impossible à annuler : il faudra "
                        "recréer chaque compte avec ➕ (mot de passe redemandé).", self.reset_server, ("srv",),
                        "♻️ OUI, tout effacer")

    async def reset_server(self) -> None:
        """Bot 3.17.0 : ♻️ voir core.reset. Le bot redémarre à la fin."""
        from core import reset as rs, update as upd
        mid = await self.sender.send(self.chat_id, "♻️ <b>Réinitialisation…</b>\n⏳ arrêt des terminaux", None, True)
        insts = self.ctx.db.list(include_archived=True)
        for i in insts:
            self.ctx.db.set_desired(i.id, "ARRETE")                       # le watchdog ne relance plus rien
        for i in insts:
            try:
                await asyncio.to_thread(self.ctx.mgr.procs.kill, self.ctx.mgr.exe(i))
            except Exception:
                log.exception("réinitialisation : arrêt de %s", i.id)
        try:
            await self.bt._kill()
        except Exception:
            log.exception("réinitialisation : arrêt du testeur")
        await asyncio.sleep(3)
        cfg = self.ctx.cfg
        rep = await asyncio.to_thread(rs.wipe_files, cfg.paths.instances, cfg.paths.archives, self.bt.folder,
                                      cfg.paths.common(), cfg.paths.base)
        rs.wipe_db(self.ctx.db, rep)
        self.ctx.db.add_action("", "SERVEUR RÉINITIALISÉ", not rep.errors, f"{len(insts)} compte(s)")
        log.warning("serveur réinitialisé : %d compte(s), %d dossier(s), %d fichier(s), %d ligne(s), erreurs : %s",
                    len(insts), len(rep.folders), rep.files, rep.rows, rep.errors)
        txt = (f"♻️ <b>Serveur réinitialisé</b> : {len(insts)} compte(s) retiré(s), {rep.files} fichier(s) d'échange supprimé(s).\n"
               "Bot, BASE et presets gardés. Le bot redémarre ; recrée tes comptes avec ➕.")
        if rep.errors:
            txt += "\n⚠️ Non supprimé : " + esc("; ".join(rep.errors)[:600])
        await self.sender.edit(self.chat_id, mid, txt, [[("✖ Fermer", "d:close")]])
        from .system import BOT_ROOT
        await asyncio.to_thread(upd.restart_bot, BOT_ROOT)
        upd.STOP.set()

    # ---- Bot 3.21.0 : ☁️ sauvegarde complète (base + presets) et restauration
    async def prepare_base(self) -> None:
        """Bot 4.5.0 : BASE pas encore « prêt » (MT5 neuf ou mis à jour) → MT5 ouvert une fois dans BASE jusqu'à la fin de
        sa compilation de départ ; création de comptes refusée en attendant. Jamais deux préparations à la fois."""
        from mt5 import base as basemod, prepare
        cfg = self.ctx.cfg
        if getattr(self, "_base_preparing", False) or prepare.is_ready(cfg.paths.base, cfg.mt5.terminal_exe):
            return
        if basemod.check_base(cfg):
            return                                              # BASE incomplet : signalé ailleurs (🛠 Système, création)
        self._base_preparing = True
        try:
            await self.sender.send(self.chat_id, "🔧 <b>Préparation de MT5</b> (dossier BASE) : compilation de départ des "
                                   "programmes MetaQuotes, 2 à 10 min. La création de comptes attend la fin.", None, True)
            ok, msg = await asyncio.to_thread(prepare.prepare, cfg)
            self.ctx.db.add_action("", "PRÉPARATION DE BASE", ok, msg)
            await self.sender.send(self.chat_id, ("✅ <b>MT5 prêt</b> : tu peux créer des comptes (➕ ou 📄)." if ok else
                                                  f"⚠️ Préparation de MT5 : {esc(msg)}. Nouvel essai dans quelques minutes."),
                                   None, not ok)
        except Exception as exc:
            log.exception("préparation de BASE")
            await self.sender.send(self.chat_id, f"⚠️ Préparation de MT5 : {esc(str(exc))}", None, True)
        finally:
            self._base_preparing = False

    async def brokers_screen(self) -> None:
        """Bot 4.7.0 : courtiers dont le bot a la liste de serveurs (création de compte possible)."""
        from mt5 import brokers
        base = self.ctx.cfg.paths.base
        brokers.migrate(base)
        names = brokers.known(base)
        lst = ", ".join(f"<b>{esc(n)}</b>" for n in names) or "aucun"
        await self.sender.send(
            self.chat_id, f"🏦 <b>Courtiers connus</b> : {lst}\nUn compte ne peut être créé que si le début de son nom de serveur "
            "correspond à un courtier connu (<code>RoboForex-Pro</code> → roboforex). Autre courtier : ➕ avec le lien de son "
            "installeur MT5 pour PC (le bot l'installe à part, garde sa liste de serveurs, puis le supprime).",
            [[("➕ Ajouter un courtier", "d:brkadd"), ("✖️ Fermer", "d:close")]], False)

    async def _broker_text(self, text: str) -> None:
        from mt5 import brokers
        t = text.strip()
        if self.awaiting_broker == "name":
            if not brokers.key_of(t) or len(t) > 60 or " " in t:
                await self.sender.send(self.chat_id, "❌ Nom de serveur invalide (ex. <code>Exness-MT5Real8</code>), renvoie-le.",
                                       None, False)
                return
            self.awaiting_broker = "url:" + t
            await self.sender.send(self.chat_id, "🏦 2/2 : envoie le <b>lien de l'installeur MT5 pour PC</b> de ce courtier (bouton "
                                   "« Télécharger pour PC / Windows » de son site, <code>https://…</code>).", None, False)
            return
        server = self.awaiting_broker[4:]
        self.awaiting_broker = ""
        if not t.startswith("https://"):
            await self.sender.send(self.chat_id, "❌ Le lien doit commencer par https:// : 🏦 Courtiers → ➕ pour recommencer.",
                                   None, False)
            return
        k = brokers.key_of(server)
        self.ctx.db.add_action("", "AJOUT COURTIER", True, f"{k} · {t[:120]}")
        await self.sender.send(self.chat_id, f"⏳ Installation à part du MT5 du courtier <b>{esc(k)}</b> pour récupérer sa liste de "
                               "serveurs (2 à 10 min). BASE et les comptes ne sont pas touchés.", None, True)

        async def go() -> None:
            ok, msg = await asyncio.to_thread(brokers.add_from_installer, self.ctx.cfg, t, server)
            await self.sender.send(self.chat_id, (f"✅ Courtier <b>{esc(k)}</b> ajouté : tu peux créer ses comptes." if ok else
                                                  f"❌ Courtier non ajouté : {esc(msg)}"), None, True)
        asyncio.get_running_loop().create_task(go())

    SHELL_TIMEOUT_S = 120.0

    async def _shell_say(self, text: str, kb, mid: int = 0) -> int:
        """Bot 4.8.8 : écrit dans le message 💻 (édité en place), sinon un nouveau message en bas."""
        from .ui import EditResult
        if mid:
            try:
                if await self.sender.edit(self.chat_id, mid, text, kb) in (EditResult.OK, EditResult.SAME):
                    return mid
            except Exception:
                pass
        return await self.sender.send(self.chat_id, text, kb, True)

    async def _shell_ask(self, text: str) -> None:
        """Bot 4.6.0 / 4.8.8 : 💻 commande reçue → confirmation dans le message de l'invite (pas dans le tableau de bord)."""
        import re as _re
        import secrets as _secrets
        cmd = text.strip()
        cmd = _re.sub(r"^sudo\s+(-\S+\s+)*", "", cmd)              # le bot n'a pas les droits administrateur
        mid, self._shell_msg = self._shell_msg, 0
        if not cmd or len(cmd) > 2000:
            await self._shell_say("❌ Commande vide ou trop longue (2000 caractères au plus) : 🖥 Serveur → 💻 pour recommencer.",
                                  [[("✖ Fermer", "d:close")]], mid)
            return
        tok = _secrets.token_hex(4)
        self._shell_pending = {tok: cmd}                    # une seule confirmation en attente
        await self._shell_say(f"💻 <b>Exécuter cette commande ?</b>\n<pre>{esc(cmd)}</pre>Utilisateur <code>scalpx</code>, arrêt au "
                              f"bout de {int(self.SHELL_TIMEOUT_S // 60)} min.",
                              [[("▶️ Exécuter", f"d:shx:{tok}"), ("✖ Annuler", f"d:shn:{tok}")]], mid)

    async def _shell_run(self, cmd: str, mid: int = 0) -> None:
        import os as _os
        t0 = time.monotonic()
        self.ctx.db.add_action("", "COMMANDE", True, cmd[:200])
        mid = await self._shell_say(f"⏳ <b>En cours…</b>\n<pre>{esc(cmd[:300])}</pre>", None, mid)
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                cwd=_os.path.expanduser("~"), start_new_session=True)
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), self.SHELL_TIMEOUT_S)
                code = proc.returncode
            except asyncio.TimeoutError:
                try:
                    _os.killpg(proc.pid, 9)                     # la commande et tout ce qu'elle a lancé
                except OSError:
                    pass
                out, code = b"", None
        except Exception as exc:
            out, code = str(exc).encode(), -1
        text = out.decode("utf-8", errors="replace").replace("\x00", "")
        head = (f"💻 Code {code} · {time.monotonic() - t0:.0f} s" if code is not None
                else f"💻 ⏱ arrêtée au bout de {int(self.SHELL_TIMEOUT_S)} s")
        close = [[("✖ Fermer", "d:close")]]
        if len(text) <= 3500:
            await self._shell_say(f"{head}\n<pre>{esc(cmd[:200])}</pre>\n<pre>{esc(text.strip() or '(aucune sortie)')}</pre>"[:4090],
                                  close, mid)
        else:
            await self._shell_say(f"{head} · sortie complète dans le fichier ci-dessous.\n<pre>{esc(cmd[:200])}</pre>", close, mid)
            await self.sender.document(self.chat_id, text.encode("utf-8"), "sortie.txt", head)

    BKP_WAIT = "une sauvegarde existe déjà dans le dépôt : rien n'est écrasé"

    async def may_write_backup(self) -> bool:
        """Bot 4.4.0 : ce serveur peut-il écrire la sauvegarde ? Oui s'il en est le « propriétaire » (il a déjà sauvegardé,
        il vient de restaurer, ou tu as choisi 🔁 Remplacer) ou si le dépôt n'en contient aucune. Sinon (serveur neuf et
        sauvegarde d'un autre serveur dans le dépôt) : rien n'est écrasé, ☁️ Restaurer / 🔁 Remplacer proposés (1×/jour)."""
        from core import ghbackup
        db = self.ctx.db
        if db.meta_get("bkp_owner") == "1" or db.meta_get("fbk_ts"):
            return True
        names, err = await asyncio.to_thread(ghbackup.existing, self.ctx.cfg)
        if err:
            return False                                        # incertain : on n'écrit pas (nouvel essai plus tard)
        if not names:
            db.meta_set("bkp_owner", "1")
            return True
        today = time.strftime("%Y-%m-%d")
        if db.meta_get("bkp_conflict_ask") != today:
            db.meta_set("bkp_conflict_ask", today)
            if self.dash.screen[0] == "bkp":                    # Bot 4.8.3 : l'écran Sauvegarde le montre déjà (pas de doublon)
                return False
            await self.sender.send(
                self.chat_id, f"☁️ <b>Une sauvegarde existe déjà</b> dans <b>{esc(ghbackup.repo(self.ctx.cfg))}</b> "
                "(serveur précédent). Je ne l'écrase pas.\n• <b>☁️ Restaurer</b> : la récupérer sur ce serveur (son mot de "
                "passe sera demandé) ;\n• <b>🔁 Remplacer</b> : la remplacer par ce serveur (l'ancienne reste dans "
                "l'historique du dépôt GitHub).", [[("☁️ Restaurer", "d:frs")], [("🔁 Remplacer", "d:bkown"), ("✖️ Plus tard", "d:close")]],
                False)
        return False

    async def full_backup(self, quiet: bool = False) -> str:
        from core import fullbackup, ghbackup
        from core.version import bot_version
        cfg, db = self.ctx.cfg, self.ctx.db
        if not await self.backup_ready():
            return db.meta_get("ghb_err") or "sauvegarde GitHub non activée : 🛠 Système → ☁️ Sauvegarde"
        if not await self.may_write_backup():
            return self.BKP_WAIT
        try:
            data = await asyncio.to_thread(fullbackup.build, db, cfg.paths.data, bot_version())
            err = await asyncio.to_thread(fullbackup.push, cfg, data)
        except Exception as exc:
            log.exception("sauvegarde complète")
            err = str(exc)
        if err:
            if not quiet or err != db.meta_get("fbk_err"):
                await self.sender.send(self.chat_id, f"⚠️ Sauvegarde complète sur GitHub : {esc(err)}", None, True)
            db.meta_set("fbk_err", err)
            return err
        db.meta_set("fbk_err", "")
        db.meta_set("fbk_ts", str(int(time.time())))
        db.meta_set("fbk_size", str(len(data)))
        log.info("sauvegarde complète envoyée sur GitHub (%d Ko)", len(data) // 1024)
        return ""

    async def _full_restore_ask(self, password: str = "", blob: bytes = b"") -> None:
        from core import crypt, fullbackup
        self.dash.goto("bkp")
        if not blob:
            if not await self.backup_ready():
                self.dash.flash("❌ Sauvegarde GitHub non activée : ☁️ Sauvegarde → 🔑 jeton d'abord.")
                await self.dash.refresh(force=True)
                return
            self.dash.flash("⏳ Téléchargement de la sauvegarde complète…")
            await self.dash.refresh(force=True)
            blob, err = await asyncio.to_thread(fullbackup.fetch, self.ctx.cfg)
        else:
            err = ""
        data, man = b"", {}
        if not err:
            try:
                data = crypt.decrypt(blob, self.ctx.cfg.backup_key, password)
            except ValueError as exc:
                if "requis" in str(exc) or (password and "incorrect" in str(exc)):
                    self._pending_restore = blob
                    self.awaiting_token = "RESTORE_PASSWORD"
                    self.dash.flash(("❌ Mot de passe incorrect. " if password else "")
                                    + "🔒 Sauvegarde chiffrée : envoie son <b>mot de passe</b> (ton message est effacé).", 300)
                    await self.dash.refresh(force=True)
                    return
                err = str(exc)
        if not err:
            try:
                man = await asyncio.to_thread(fullbackup.check, data)
            except ValueError as exc:
                err = str(exc)
        if err:
            self.dash.flash(f"❌ {esc(err)}")
            await self.dash.refresh(force=True)
            return
        when = time.strftime("%d/%m/%Y %H:%M", time.localtime(int(man.get("date", 0)))) if man.get("date") else "?"

        async def go() -> None:
            await asyncio.to_thread(fullbackup.stage, self.ctx.cfg.paths.data, data)
            self.ctx.db.add_action("", "RESTAURATION COMPLÈTE", True, when)
            await self.sender.send(self.chat_id, "☁️ Restauration posée : le bot redémarre et l'applique (≈ 15 s).", None, True)
            await self.restart_bot()
        await self._ask(f"☁️ <b>Restaurer la sauvegarde du {esc(when)} ?</b>\nServeur {esc(str(man.get('serveur', '?')))}, "
                        f"bot {esc(str(man.get('version_bot', '?')))}, {man.get('comptes', 0)} compte(s).\n"
                        "Remplace l'historique, l'état de l'optimiseur et les presets de ce serveur (les actuels sont gardés "
                        "à côté). Les comptes sans terminal ici seront archivés : recrée-les avec 📄 ou ➕ (historique repris).",
                        go, ("bkp",), "☁️ Restaurer")

    async def preset_restore_github(self) -> None:
        from core import ghbackup
        self.dash.goto("psl")
        if not await self.backup_ready():
            self.dash.flash("❌ Sauvegarde GitHub non activée : 🛠 Système → ☁️ Sauvegarde.")
            await self.dash.refresh(force=True)
            return
        self.dash.flash("⏳ Lecture de la sauvegarde GitHub…")
        await self.dash.refresh(force=True)
        blob, err = await asyncio.to_thread(ghbackup.fetch, self.ctx.cfg)
        if err:
            self.dash.flash(f"❌ {esc(err)}")
            await self.dash.refresh(force=True)
            return
        await self.preset_import_file(blob)

    async def preset_import_file(self, blob: bytes, password: str = "") -> None:
        """Bot 4.2.0 : 📥 n'importe quel fichier de sauvegarde (texte, presets.json, sauvegarde complète, chiffrés)."""
        try:
            text = await asyncio.to_thread(pui.ps.import_text_from, blob, self.ctx.cfg.backup_key, password)
        except ValueError as exc:
            self.dash.goto("psl")
            if "mot de passe" in str(exc):
                self._pending_import = blob
                self.awaiting_token = "IMPORT_PASSWORD"
                self.dash.flash(("❌ Mot de passe incorrect. " if password else "")
                                + "🔒 Fichier chiffré : envoie son <b>mot de passe</b> (ton message est effacé).", 300)
            else:
                self.dash.flash(f"❌ Import impossible : {esc(str(exc))}.")
            await self.dash.refresh(force=True)
            return
        await self.preset_import(text)

    # ---- Bot 4.2.0 : ☁️ sauvegarde GitHub propre à l'utilisateur (dépôt privé créé par le bot, chiffrement)
    async def backup_ready(self) -> bool:
        """Jeton présent → dépôt connu (sinon trouvé ou créé, puis noté dans .env). Jamais d'exception."""
        from core import envfile, ghbackup
        from .system import BOT_ROOT
        cfg = self.ctx.cfg
        if not cfg.backup_token:
            return False
        if ghbackup.repo(cfg):
            return True
        name, err = await asyncio.to_thread(ghbackup.ensure_repo, cfg)
        if err:
            if err != self.ctx.db.meta_get("ghb_err"):
                self.ctx.db.meta_set("ghb_err", err)
            return False
        cfg.backup_repo = name
        try:
            await asyncio.to_thread(envfile.set_key, BOT_ROOT / ".env", "BACKUP_REPO", name)
        except (OSError, ValueError):
            log.exception("BACKUP_REPO non écrit dans .env")
        self.ctx.db.meta_set("ghb_err", "")
        self.ctx.db.add_action("", "DÉPÔT DE SAUVEGARDE", True, name)
        return True

    async def offer_backup(self, force: bool = False) -> bool:
        """Au démarrage (une fois par jour) : sauvegarde non activée → proposition. « Jamais » arrête les propositions."""
        db = self.ctx.db
        today = time.strftime("%Y-%m-%d")
        if self.ctx.cfg.backup_token or (not force and (db.meta_get("backup_never") == "1" or db.meta_get("backup_offer") == today)):
            return False
        db.meta_set("backup_offer", today)
        await self.sender.send(self.chat_id, "☁️ <b>Activer la sauvegarde sur GitHub ?</b>\nTes presets (Martin…), l'historique "
                               "et l'état de l'optimiseur, dans un dépôt <b>privé</b> de TON compte GitHub, chiffrés par ton mot "
                               "de passe. Indispensable pour repartir sur un nouveau serveur.",
                               [[("✅ Activer", "d:bkp")], [("⏰ Plus tard", "d:close"), ("🚫 Jamais", "d:bknever")]], False)
        return True

    async def _after_plain(self) -> None:
        await self.backup_presets()
        await self.full_backup()
        await self.dash.refresh(force=True)

    async def _backup_off(self) -> None:
        from core import envfile
        from .system import BOT_ROOT
        for k in ("BACKUP_TOKEN", "BACKUP_REPO", "BACKUP_KEY"):
            try:
                await asyncio.to_thread(envfile.set_key, BOT_ROOT / ".env", k, "")
            except (OSError, ValueError):
                log.exception("%s non effacé du .env", k)
        cfg = self.ctx.cfg
        cfg.backup_token = cfg.backup_repo = cfg.backup_key = ""
        for k in ("ghb_hash", "ghb_err", "fbk_err", "backup_plain"):
            self.ctx.db.meta_set(k, "")
        self.ctx.db.meta_set("backup_never", "1")
        self.ctx.db.add_action("", "SAUVEGARDE DÉSACTIVÉE", True, "")
        self.dash.goto("bkp")
        self.dash.flash("☁️ Sauvegarde désactivée.")
        await self.dash.refresh(force=True)

    async def _password_received(self, kind: str, pw: str) -> None:
        """Mot de passe reçu (message déjà effacé) : chiffrement de la sauvegarde, import ou restauration chiffrés."""
        from core import crypt, envfile
        from .system import BOT_ROOT
        if kind == "IMPORT_PASSWORD":
            blob, self._pending_import = self._pending_import, b""
            await self.preset_import_file(blob, pw)
            return
        if kind == "RESTORE_PASSWORD":
            blob, self._pending_restore = self._pending_restore, b""
            await self._full_restore_ask(pw, blob)
            return
        # BACKUP_PASSWORD : nouvelle clé de chiffrement
        self.dash.goto("bkp")
        if len(pw) < 8:
            self.awaiting_token = "BACKUP_PASSWORD"
            self.dash.flash("❌ Trop court : 8 caractères au moins.", 300)
            await self.dash.show()
            return
        key = await asyncio.to_thread(crypt.new_key, pw)
        del pw
        try:
            await asyncio.to_thread(envfile.set_key, BOT_ROOT / ".env", "BACKUP_KEY", key)
        except (OSError, ValueError) as exc:
            self.dash.flash(f"❌ .env non modifié : {esc(str(exc))}")
            await self.dash.show()
            return
        self.ctx.cfg.backup_key = key
        self.ctx.db.meta_set("ghb_hash", "")
        self.ctx.db.add_action("", "SAUVEGARDE CHIFFRÉE", True, "")
        self.dash.flash("⏳ Mot de passe reçu : chiffrement activé, première sauvegarde en cours…")
        await self.dash.show()
        err = await self.full_backup()
        await self.backup_presets()
        if err == self.BKP_WAIT:
            self.dash.flash("✅ Chiffrement prêt. Une sauvegarde d'un serveur précédent existe déjà : rien n'a été écrasé. "
                            "☁️ Restaurer la récupère (avec SON mot de passe).", 600)
        else:
            self.dash.flash("✅ Sauvegarde chiffrée envoyée. <b>Note ce mot de passe</b>." if not err else f"❌ {esc(err)}", 600)
        await self.dash.show()

    async def backup_presets(self) -> None:
        """Toutes les 5 min : presets changés → réécrits sur GitHub (chiffrés si activé ; une alerte par problème)."""
        from core import ghbackup
        if not await self.backup_ready() or not await self.may_write_backup():
            return
        store = self.dash.store
        names = store.names()
        if not names:
            return
        text = pui.ps.export_text(store, names)
        h = ghbackup.digest(text + ("|chiffré" if self.ctx.cfg.backup_key else ""))
        db = self.ctx.db
        if h == db.meta_get("ghb_hash"):
            return
        err = await asyncio.to_thread(ghbackup.push, self.ctx.cfg, text)
        if err:
            if err != db.meta_get("ghb_err"):
                db.meta_set("ghb_err", err)
                await self.sender.send(self.chat_id, f"⚠️ Sauvegarde GitHub des presets : {esc(err)}", None, True)
            return
        db.meta_set("ghb_hash", h)
        db.meta_set("ghb_ts", str(int(time.time())))
        if db.meta_get("ghb_err"):
            db.meta_set("ghb_err", "")
            await self.sender.send(self.chat_id, "☁️ Sauvegarde GitHub des presets rétablie.", None, True)
        log.info("presets sauvegardés sur GitHub (%s)", ", ".join(names))

    async def _preset_export(self, names: list[str]) -> None:
        store = self.dash.store
        data = pui.ps.export_text(store, names).encode("utf-8")
        slug = re.sub(r"[^A-Za-z0-9_-]+", "_", names[0]).strip("_") or "preset" if len(names) == 1 else "presets"
        await self.sender.document(self.chat_id, data, f"scalpx_{slug}.txt",
                                   f"📤 {len(names)} preset(s) : " + ", ".join(names)[:300] +
                                   "\nPour l'autre VPS : transfère ce fichier à son bot après 💾 Presets → 📥 Importer.")
        self.ctx.db.add_action("", "PRESET EXPORTÉ", True, ", ".join(names)[:200])
        self.dash.flash(f"📤 Fichier envoyé ci-dessous ({len(names)} preset(s)).", 15)
        await self.dash.refresh(force=True)

    async def preset_import(self, text: str) -> None:
        """Texte / fichier exporté → presets créés ou remplacés (après confirmation s'il en remplace). Le preset ⭐ actif
        ne change pas : 📤 Appliquer reste un geste volontaire."""
        try:
            got, unknown = pui.ps.parse_import(text)
        except ValueError as exc:
            self.dash.goto("psl")
            self.dash.flash(f"❌ Import impossible : {esc(str(exc))}.")
            await self.dash.refresh(force=True)
            return
        store = self.dash.store
        replaced = [n for n in got if n in store.presets]
        desc = "\n".join(f"• {esc(n)} : {len(v)} réglages" + (" (remplace l'existant)" if n in replaced else " (nouveau)")
                         for n, v in got.items())
        note = f"\n<i>{unknown} réglage(s) ignoré(s) : inconnu(s) de cet EA (ex. réglages retirés) ou valeur illisible.</i>" if unknown else ""

        async def run() -> None:
            for n, v in got.items():
                store.put(n, v)
                self.dash.params.vals.pop(pui.PREFIX + n, None)
            if store.active in got:
                self.sync_base_set()
            self.ctx.db.add_action("", "PRESET IMPORTÉ", True, ", ".join(got)[:200])
            self.dash.goto("psl")
            self.dash.flash(f"📥 {len(got)} preset(s) importé(s)."
                            + ("" if store.active in got else " 📤 Appliquer pour l'envoyer aux robots et en faire le preset ⭐."))
            await self.dash.refresh(force=True)

        if replaced:
            await self._ask(f"📥 <b>Importer {len(got)} preset(s) ?</b>\n{desc}{note}", run, ("psl",), "📥 Importer")
        else:
            await run()

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
        t0 = time.monotonic()
        insts = await asyncio.to_thread(self._running)                 # parcours des processus : hors de la boucle du bot
        t1 = time.monotonic()
        self.dash.preset_check = await pui.check_all(self.ctx, store, insts)
        t2 = time.monotonic()
        await self.dash.refresh(force=True)
        t3 = time.monotonic()
        if t3 - t0 > 3.0:                                               # diagnostic : quelle étape est lente
            log.info("vérification des presets : %.1f s (processus %.1f s, lecture de %d robot(s) %.1f s, tableau de bord %.1f s)",
                     t3 - t0, t1 - t0, len(insts), t2 - t1, t3 - t2)

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
        """Tout message reçu (bouton « 📊 Tableau de bord », texte libre) : le tableau de bord revient en bas de la conversation.
        Bot 4.9.1 : déjà en bas (le message reçu vient d'être effacé) → simplement remis à l'accueil SUR PLACE, sans nouveau
        message (avant : un 2e tableau de bord apparaissait puis le premier s'effaçait)."""
        await self.dash.home_or_down()

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
        from mt5 import build as buildmod
        if buildmod.sources_newer_than_ex5(self.ctx.cfg):   # sources de BASE plus récentes que le .ex5 : pas encore compilé
            lines.append(f"\n⚠️ <b>BASE n'est pas encore compilée en v{esc(plan.base_version or '?')}</b> : les comptes ont déjà "
                         "le dernier .ex5 de BASE, mais c'est l'ancienne version."
                         + ("\n⏳ Compilation en cours…" if self.building else "\nLance la compilation, puis le déploiement sera proposé."))
            kb0: Keyboard = [] if self.building else [[("🔨 Compiler l'EA maintenant", "d:bld")]]
            await self.sender.send(self.chat_id, "\n".join(lines), kb0 + [[("✖ Fermer", "dep:close")]], False)
            return
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
        later = [str(x) for x in (upd.commit_info(plan.source).get("later") or [])]
        if left:                                 # ce que « ⏩ Tout installer » apporte en plus, version par version
            lines += ["", f"⏩ <b>Tout installer d'un coup</b> ajoute aussi {left} autre(s) version(s) :"]
            shown = 0
            for msg in later:
                line = "• " + esc(msg)
                if len("\n".join(lines)) + len(line) > 3700:        # limite d'un message Telegram
                    break
                lines.append(line)
                shown += 1
            if shown < left:
                lines.append(f"• … et {left - shown} autre(s)")
        self._upd_msg = await self.sender.send(self.chat_id, "\n".join(lines),
                                               [[("⬆ Installer", f"upd:go:{plan.stamp}"), ("⏰ Plus tard", "upd:later")]]
                                               + ([[(f"⏩ Tout installer d'un coup ({left + 1})", "upd:all")]] if left else []),
                                               False)          # Bot 4.8.9 : AVEC notification (avant : silencieux, rien sur le téléphone)

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
        if sub not in ("go", "all"):
            return ""
        if upd.STOP.is_set():                   # déjà installée, le bot redémarre : un 2e appui ne relance rien
            if mid:
                await self.sender.edit(self.chat_id, mid, "🔄 Mise à jour déjà installée : le bot redémarre (≈ 30 s).", None)
            return ""
        if self.ctx.deploy.running or self.building:   # mise en file : installée toute seule à la fin, l'utilisateur le voit
            self._upd_after = sub
            what = (f"une compilation de l'EA est en cours (≈ {self.build_pct} %)" if self.building
                    else "un déploiement de l'EA est en cours")
            log.info("mise à jour en file d'attente : %s", what)
            if mid:
                await self.sender.edit(self.chat_id, mid, f"⏳ <b>Mise à jour en file d'attente</b> : {what}.\n"
                                       "Elle s'installera toute seule dès la fin ; la suite s'affichera ici.", None)
            return ""
        if self.installing:                     # double appui sur « Installer » : une seule installation
            return "Installation déjà en cours…"
        self.installing = True
        try:
            if sub == "all":                    # ⏩ dernière publication directement (contient toutes les précédentes)
                from core import github as gh
                inst_c = (self.ctx.db.meta_get("installed_commit") or "").split(" ")[0]
                if mid:
                    await self.sender.edit(self.chat_id, mid, "⏳ Téléchargement de la dernière version…", None)
                res = await asyncio.to_thread(lambda: gh.fetch(self.ctx.cfg, force=True, installed=inst_c, latest=True))
                if not res.ok:
                    if mid:
                        await self.sender.edit(self.chat_id, mid, f"❌ Téléchargement impossible : {esc(res.error)}", None)
                    return ""
                log.info("installation groupée : dernière publication %s", (res.sha or "")[:7])
            return await self._upd_install(mid)
        finally:
            self.installing = False

    async def _upd_resume(self) -> None:
        """Fin d'une compilation ou d'un déploiement : installe la mise à jour mise en file d'attente."""
        if not self._upd_after or self.building or self.ctx.deploy.running:
            return
        sub, self._upd_after = self._upd_after, ""
        log.info("installation de la mise à jour mise en file d'attente")
        await self._upd_cb(["upd", sub])

    async def _upd_step(self, mid, text: str) -> None:
        log.info("mise à jour : %s", text)
        if mid:
            await self.sender.edit(self.chat_id, mid, text, None)

    async def _upd_install(self, mid) -> str:
        """Installe la mise à jour proposée. Copies et relance hors de la boucle du bot : le tableau de bord et les autres
        tâches continuent pendant l'installation (mêmes étapes, même ordre qu'avant)."""
        from core import update as upd
        from pathlib import Path as _P
        bot_root = _P(__file__).resolve().parent.parent
        await self._upd_step(mid, "⏳ <b>Installation</b> : vérification du paquet…")
        plan = await asyncio.to_thread(upd.plan_update, self.ctx.cfg, bot_root)
        if plan is None or plan.empty:
            if mid:
                await self.sender.edit(self.chat_id, mid, "✅ Déjà à jour.", None)
            return ""
        out = []
        log.info("installation de la mise à jour %s : %d fichier(s) du bot, %d de l'EA", plan.stamp,
                 len(plan.bot_files) + len(plan.bot_remove), len(plan.ea_files))
        try:
            if plan.ea_files:
                await self._upd_step(mid, f"⏳ <b>Installation</b> : copie de l'EA dans BASE ({len(plan.ea_files)} fichier(s))…")
                ver = await asyncio.to_thread(upd.install_ea, self.ctx.cfg, plan)
                out.append(f"📈 EA copié dans BASE" + (f" — compilation v{esc(ver)} dans quelques secondes" if ver else " (.set seulement)"))
            if plan.bot_files or plan.bot_remove:
                await self._upd_step(mid, "⏳ <b>Installation</b> : " + ("\n".join(out) + "\n" if out else "")
                                     + f"copie du bot ({len(plan.bot_files) + len(plan.bot_remove)} fichier(s))…")
                await asyncio.to_thread(upd.install_bot, plan, bot_root)
                if plan.requirements_changed:
                    await self._upd_step(mid, "⏳ <b>Installation</b> : bibliothèques Python (1 à 3 min)…")
                    ok, err = await asyncio.get_running_loop().run_in_executor(None, upd.pip_install, bot_root)
                    out.append("📦 Bibliothèques " + ("installées" if ok else "NON installées : " + esc(err)))
                out.append("🤖 Bot mis à jour : redémarrage, il revient dans ≈ 30 s (message « 🤖 Bot … démarré »).")
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
            self.note_restart(mid, "mise à jour", "✅ <b>Mise à jour installée</b>\n" + "\n".join(out))
            how = await asyncio.to_thread(upd.restart_bot, bot_root)
            log.warning("mise à jour du bot installée : redémarrage (%s)", how)
            upd.STOP.set()
        return ""

    # ------------------------------------------------------------------ suivi des redémarrages
    def note_restart(self, mid, why: str, text: str = "") -> None:
        """Avant un redémarrage voulu : le NOUVEAU bot complètera ce message (durée, relance par la surveillance ou non)."""
        import json as _json
        from core.version import bot_version
        self.ctx.db.meta_set("restart_note", _json.dumps({"mid": mid or 0, "t": time.time(), "why": why,
                                                         "from": bot_version(), "text": text}, ensure_ascii=False))

    async def restart_report(self) -> None:
        """Au démarrage : « ✅ Redémarré en N s » dans le message d'origine (ou un nouveau message s'il a disparu)."""
        import json as _json
        from core.version import bot_version
        raw = self.ctx.db.meta_get("restart_note")
        if not raw:
            return
        self.ctx.db.meta_set("restart_note", "")
        try:
            note = _json.loads(raw)
            t0 = float(note.get("t", 0))
        except (ValueError, TypeError, AttributeError):
            return
        dt = max(0, int(time.time() - t0))
        took = f"{dt} s" if dt < 60 else f"{dt // 60} min {dt % 60:02d} s"
        old, new = str(note.get("from") or "?"), bot_version()
        ver = f"Bot {esc(old)} → <b>{esc(new)}</b>" if old != new else f"Bot <b>{esc(new)}</b>"
        line = f"✅ <b>Redémarré en {took}</b> ({esc(str(note.get('why') or 'redémarrage'))}) · {ver}"
        if dt > 120:
            line += "\n⚠️ Plus long que prévu : 📜 Journaux → 📜 Bot pour le détail."
        log.info("redémarrage terminé en %d s (%s)", dt, note.get("why"))
        text = (str(note.get("text") or "") + "\n\n" if note.get("text") else "") + line
        mid = int(note.get("mid") or 0)
        if mid:
            from .ui import EditResult
            if await self.sender.edit(self.chat_id, mid, text, None) in (EditResult.OK, EditResult.SAME):
                return
        await self.sender.send(self.chat_id, line, None, True)

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
        rmid = await self.sender.send(self.chat_id, "🔄 Redémarrage du bot… il revient dans environ 30 s.", None, True)
        self.note_restart(rmid, "bouton Redémarrer", "🔄 Redémarrage du bot demandé.")
        how = await asyncio.to_thread(upd.restart_bot, _P(__file__).resolve().parent.parent)
        log.warning("redémarrage demandé depuis Telegram (%s)", how)
        upd.STOP.set()

    async def _compile_progress(self, mid, head: str, coro):
        """Compilation avec progression estimée (25, 50, 75, 90 %) d'après la durée de la compilation précédente
        (MetaEditor n'indique pas son avancement). Renvoie (résultat, durée en s)."""
        try:
            expected = max(10.0, float(self.ctx.db.meta_get("compile_s") or 120))
        except ValueError:
            expected = 120.0
        t0 = time.monotonic()
        task = asyncio.ensure_future(coro)
        shown, slow = 0, False
        log.info("compilation lancée (durée habituelle ≈ %d s)", expected)
        while not task.done():
            await asyncio.wait({task}, timeout=1)
            if task.done():
                break
            el = time.monotonic() - t0
            reached = max([m for m in (25, 50, 75, 90) if el >= expected * m / 100], default=0)
            late = el > expected * 1.15
            if mid and (reached > shown or (late and not slow)):
                shown, slow = reached, late
                self.build_pct = reached
                note = f" · plus long que d'habitude ({int(el)} s)" if late else f" · {int(el)} s sur ≈ {int(expected)} s"
                log.info("compilation : ≈ %d %% (%d s)", reached, el)
                await self.sender.edit(self.chat_id, mid, f"{head}\n{render.bar(reached / 100, 10)} ≈ {reached} %{note}", None)
        res = task.result()
        dt = time.monotonic() - t0
        if res.ok:                                         # moyenne glissante : l'estimation suit la vitesse réelle du VPS
            self.ctx.db.meta_set("compile_s", f"{(expected + dt) / 2 if self.ctx.db.meta_get('compile_s') else dt:.0f}")
        log.info("compilation terminée en %d s", dt)
        return res, dt

    async def _after_busy(self) -> None:
        if self._upd_after:
            self._bg(self._upd_resume())

    async def run_manual_build(self) -> None:
        """Bouton 🔨 Compiler l'EA : compile BASE tel quel, puis propose le déploiement si le .ex5 a changé."""
        from mt5 import build as buildmod
        from mt5.deploy import detect_new_build
        self.building = True
        try:
            head = "🔨 Compilation de l'EA dans BASE…"
            mid = await self.sender.send(self.chat_id, head, None, True)
            res, dt = await self._compile_progress(mid, head, buildmod.compile_ea(self.ctx.cfg))
            text = (f"✅ <b>EA v{esc(res.version)} compilé</b> ({res.warnings} avertissement(s)) · 100 % en {int(dt)} s." if res.ok else
                    f"❌ <b>Compilation échouée</b>" + (f" ({res.errors} erreur(s))" if res.errors > 0 else "") + f"\n{esc(res.detail)}")
            if mid:
                await self.sender.edit(self.chat_id, mid, text, [[("✖ Fermer", "d:close")]])
            if res.ok:
                nb = detect_new_build(self.ctx.cfg, self.ctx.db, settle_s=0)
                if nb is not None:
                    await self.on_new_build(nb)
        finally:
            self.building = False
            self.build_pct = 0
            await self._after_busy()

    async def run_auto_build(self, req) -> None:
        """version.txt modifié : version écrite dans les sources, compilation dans BASE, puis proposition de déploiement."""
        from mt5 import build as buildmod
        from mt5.deploy import detect_new_build
        self.building = True
        try:
            head = f"🛠 <b>version.txt = {esc(req.version)}</b> : mise à jour des sources et compilation dans BASE…"
            mid = await self.sender.send(self.chat_id, head, None, True)
            res, dt = await self._compile_progress(mid, head, buildmod.auto_build(self.ctx.cfg, self.ctx.db, req))
            if res.ok:
                text = f"✅ <b>EA v{esc(res.version)} compilé</b> (0 erreur, {res.warnings} avertissement(s)) · 100 % en {int(dt)} s."
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
            self.build_pct = 0
            await self._after_busy()

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
        lines.append("\nEn cas d'échec, le nouvel EA reste en place : redémarre le compte concerné.")
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
            wait = 1.5 - (time.time() - last[0])                # rythme d'édition limité, mais aucune étape n'est perdue
            if wait > 0:
                await asyncio.sleep(wait)
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
            text += "\n❌ Échec : nouvel EA laissé en place ; redémarre le(s) compte(s) concerné(s) (fiche du compte → 🔄)."
        await self.sender.edit(self.chat_id, mid, text, None)
        detail = "\n".join(f"{view[o.iid][0]} {labels.get(o.iid, o.iid)} : {o.message or o.status}" for o in outcomes)
        await self._after_busy()
        await self.notifier.emit(Notice("-", "Déploiement", "DEPLOIEMENT", f"🔄 Déploiement terminé : ✅ {ok} · ⏸ {len(wait)} · ❌ {len(bad)}"
                                        + (f"\n{detail}" if detail else ""), 2 if bad else 1))

    # ------------------------------------------------------------------ alertes internes (watchdog, etc.)
    async def alert(self, iid: str, kind: str, text: str, prio: int) -> None:
        inst = self.ctx.db.get(iid)
        label = inst.label if inst else iid
        self.ctx.db.add_incident(iid, kind, text)
        await self.notifier.emit(Notice(iid, label, kind, text, prio))
