"""Assemblage python-telegram-bot v21+ (asyncio, JobQueue) : handlers fins, toute la logique est dans router/wizard/dashboard."""
from __future__ import annotations

import asyncio
import io
import logging
import os
import time
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Conflict, Forbidden, RetryAfter, TelegramError
from telegram.ext import (Application, ApplicationBuilder, ApplicationHandlerStop, CallbackQueryHandler,
                          ContextTypes, ConversationHandler, Defaults, MessageHandler, TypeHandler, filters)

from core.config import Config
from core.version import bot_version
from core.events import EventReader
from core.service import Monitor, Notice
from mt5.build import pending_build
from mt5.deploy import detect_new_build
from . import render
from .actions import Ctx
from .dashboard import Dashboard
from .notify import Notifier
from .reports import build_report
from .router import Router
from .ui import EditResult, Keyboard, Confirmations
from .wizard import ALIAS, CONFIRM, LOGIN, PASSWORD, SERVER, SERVER_TEXT, WizardCore

log = logging.getLogger(__name__)

MENU_BUTTON = "📊 Tableau de bord"


# --------------------------------------------------------------------------- Sender Telegram
# Préfixes des boutons traités par le routeur (Router.on_callback) : tout nouveau préfixe DOIT être ajouté ici.
CB_HEADS = ("d", "a", "c", "ok", "no", "dep", "x", "p", "upd", "e", "g", "z", "s")
CB_PATTERN = r"^(" + "|".join(CB_HEADS) + r")(:|$)"


class TelegramSender:
    """Implémente l'interface `Sender` avec python-telegram-bot (HTML, RetryAfter géré)."""

    def __init__(self, bot) -> None:
        self.bot = bot
        self.last_retry_after = 0.0
        self.on_sent = None                 # rappel(message_id) après chaque message envoyé (tableau de bord recouvert ?)

    def _sent(self, m) -> int:
        mid = getattr(m, "message_id", 0) or 0
        if self.on_sent and mid:
            try:
                self.on_sent(mid)
            except Exception:
                log.exception("on_sent")
        return mid

    @staticmethod
    def markup(kb: Keyboard | None) -> InlineKeyboardMarkup | None:
        if not kb:
            return None
        return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in kb])

    async def _retrying(self, fn, *a, **kw):
        """Réessaie sur RetryAfter (flood) jusqu'à 3 fois."""
        for _ in range(3):
            try:
                return await fn(*a, **kw)
            except RetryAfter as e:
                self.last_retry_after = float(getattr(e.retry_after, "total_seconds", lambda: e.retry_after)())
                await asyncio.sleep(self.last_retry_after + 0.5)
        return await fn(*a, **kw)

    async def send(self, chat_id: int, text: str, kb: Keyboard | None = None, silent: bool = False) -> int:
        m = await self._retrying(self.bot.send_message, chat_id=chat_id, text=text, parse_mode=ParseMode.HTML,
                                 reply_markup=self.markup(kb), disable_notification=silent)
        return self._sent(m)

    async def edit(self, chat_id: int, message_id: int, text: str, kb: Keyboard | None = None) -> str:
        try:
            await self.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text,
                                             parse_mode=ParseMode.HTML, reply_markup=self.markup(kb))
            return EditResult.OK
        except RetryAfter as e:
            self.last_retry_after = float(getattr(e.retry_after, "total_seconds", lambda: e.retry_after)())
            return EditResult.RETRY
        except BadRequest as e:
            msg = str(e).lower()
            if "not modified" in msg:
                return EditResult.SAME
            if "not found" in msg or "can't be edited" in msg or "message to edit" in msg:
                return EditResult.GONE
            log.warning("édition refusée : %s", e)
            return EditResult.SAME
        except Forbidden:
            return EditResult.GONE

    async def delete(self, chat_id: int, message_id: int) -> bool:
        try:
            await self.bot.delete_message(chat_id=chat_id, message_id=message_id)
            return True
        except TelegramError as e:
            log.warning("suppression de message impossible : %s", type(e).__name__)
            return False


    async def reply_menu(self, chat_id: int, text: str) -> int:
        """Message portant le bouton permanent « 📊 Tableau de bord » (clavier de réponse, toujours visible)."""
        kbd = ReplyKeyboardMarkup([[KeyboardButton(MENU_BUTTON)]], resize_keyboard=True, is_persistent=True)
        m = await self._retrying(self.bot.send_message, chat_id=chat_id, text=text, parse_mode=ParseMode.HTML,
                                 reply_markup=kbd, disable_notification=True)
        return self._sent(m)

    async def photo(self, chat_id: int, data: bytes | str, caption: str = "", silent: bool = False) -> None:
        if isinstance(data, str):
            with open(data, "rb") as f:
                payload = f.read()
        else:
            payload = data
        self._sent(await self._retrying(self.bot.send_photo, chat_id=chat_id, photo=io.BytesIO(payload), caption=caption[:1000],
                                        disable_notification=silent))

    async def document(self, chat_id: int, data: bytes, filename: str, caption: str = "") -> None:
        self._sent(await self._retrying(self.bot.send_document, chat_id=chat_id, document=io.BytesIO(data), filename=filename,
                                        caption=caption[:1000]))


# --------------------------------------------------------------------------- services
class Services:
    def __init__(self, cfg: Config, ctx: Ctx, bot) -> None:
        self.cfg, self.ctx = cfg, ctx
        self.chat_id = cfg.allowed_chats[0]
        self.sender = TelegramSender(bot)
        self.conf = Confirmations()
        self.dash = Dashboard(ctx, self.sender, self.chat_id, self.conf)
        self.sender.on_sent = self.dash.note_message
        self.notifier = Notifier(self.sender, self.chat_id, cfg.alerts.silent_mode)
        self.router = Router(ctx, self.sender, self.chat_id, self.dash, self.conf, self.notifier)
        ctx.watchdog.alert = self.router.alert
        self.monitor = Monitor(cfg, ctx.db, ctx.status, EventReader(cfg.paths.common()), self.notifier.emit)
        self.wizard = WizardCore(ctx, self.sender, self.chat_id, self.on_created)

    async def on_created(self, res, mode: str, mid: int) -> None:
        """Fin de l'assistant : message de résultat + carte du compte (ou échec clair)."""
        if res.ok:
            text = "✅ <b>Compte " + ("créé" if mode == "new" else "reconnecté") + "</b> et connecté."
            if res.warnings:
                text += "\n" + "\n".join(render.esc(w) for w in res.warnings)
                inst = self.ctx.db.get(res.iid)
                await self.notifier.emit(Notice(res.iid, inst.label if inst else res.iid, "DOUBLON", "\n".join(res.warnings), 2))
            if mode == "new":                                       # preset ⭐ actif : appliqué au nouveau robot
                text += "\n" + await self.router.preset_for_new(res.iid)
            await self.sender.edit(self.chat_id, mid, text, None)
            self.dash.goto("acc", res.iid)
            await self.dash.refresh(force=True)
        else:
            await self.sender.edit(self.chat_id, mid, "❌ <b>Échec</b> : " + render.esc(res.message), None)
            await self.dash.refresh(force=True)


def svc(context: ContextTypes.DEFAULT_TYPE) -> Services:
    return context.application.bot_data["svc"]


# --------------------------------------------------------------------------- handlers
async def h_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ne répond qu'aux chat_id autorisés ; tout le reste est ignoré et journalisé."""
    chat = update.effective_chat
    allowed = context.application.bot_data["allowed"]
    msg = getattr(update, "message", None)
    s = context.application.bot_data.get("svc")
    if chat is not None and chat.id in allowed and msg is not None and s is not None:   # message de l'utilisateur
        s.dash.note_message(msg.message_id)
    if chat is None or chat.id not in allowed:
        log.warning("message ignoré d'un chat non autorisé : chat_id=%s user_id=%s",
                    getattr(chat, "id", None), getattr(update.effective_user, "id", None))
        raise ApplicationHandlerStop


async def h_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Aucune commande « / » : tout message (bouton 📊 du clavier, texte libre, /start) ramène le tableau de bord en bas."""
    m = update.message
    if m is not None:
        try:
            await svc(context).sender.delete(update.effective_chat.id, m.message_id)    # garde la conversation propre
        except Exception:
            pass
    await svc(context).router.on_text((m.text or "") if m is not None else "")


async def h_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q is None or q.data is None:
        return
    try:
        await q.answer()                                # coupe le sablier tout de suite
    except TelegramError:
        pass
    await svc(context).router.on_callback(q.data, q.message.message_id if q.message else 0)


# ---- assistant de création / reconnexion
async def w_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:
        await update.callback_query.answer()
    return await svc(context).wizard.begin_new(update.effective_chat.id)


async def w_recon(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    return await svc(context).wizard.begin_recon(update.effective_chat.id, q.data.split(":")[1])


async def w_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await svc(context).wizard.on_login(update.effective_chat.id, update.message.text or "")


async def w_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    m = update.message
    return await svc(context).wizard.on_password(update.effective_chat.id, m.message_id, m.text or "")


async def w_server(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    arg = q.data.split(":")[1]
    if arg == "new":
        return await svc(context).wizard.on_server_other(update.effective_chat.id)
    return await svc(context).wizard.on_server(update.effective_chat.id, int(arg))


async def w_server_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await svc(context).wizard.on_server_text(update.effective_chat.id, update.message.text or "")


async def w_alias(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await svc(context).wizard.on_alias(update.effective_chat.id, update.message.text or "")


async def w_alias_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    return await svc(context).wizard.on_alias(update.effective_chat.id, "")


async def w_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    return await svc(context).wizard.on_confirm(update.effective_chat.id, q.data == "wz:go")


async def w_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:
        await update.callback_query.answer()
    return await svc(context).wizard.cancel(update.effective_chat.id)


async def w_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    svc(context).wizard.timeout(svc(context).chat_id)
    return ConversationHandler.END


# ---- erreurs
async def h_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, Conflict):                        # 409 : un autre bot utilise ce token
        log.critical("Conflit Telegram (409) : une autre instance du bot tourne. Arrêt.")
        os._exit(3)
    if isinstance(err, RetryAfter):
        log.warning("Flood Telegram : attendre %s s", err.retry_after)
        return
    log.error("erreur non gérée : %s: %s", type(err).__name__, err)


# ---- tâches périodiques
# Réglages APScheduler de toutes les tâches : jamais deux exécutions en parallèle, les exécutions en retard sont fusionnées,
# et un retard jusqu'à 5 s est toléré (défaut APScheduler : 1 s → tâche sautée au moindre blocage de la boucle).
JOB_KW = {"coalesce": True, "max_instances": 1, "misfire_grace_time": 5}
JOB_KW_DAILY = {**JOB_KW, "misfire_grace_time": 300}        # tâches quotidiennes : jamais ratées pour quelques secondes de retard

async def j_presets(context: ContextTypes.DEFAULT_TYPE) -> None:
    await svc(context).router.check_presets()


async def j_dash(context: ContextTypes.DEFAULT_TYPE) -> None:
    await svc(context).dash.refresh()


async def j_status(context: ContextTypes.DEFAULT_TYPE) -> None:
    svc(context).monitor.poll_status()


async def j_events(context: ContextTypes.DEFAULT_TYPE) -> None:
    await svc(context).monitor.poll_events()


async def j_clean(context: ContextTypes.DEFAULT_TYPE) -> None:
    svc(context).ctx.cmdq.cleanup_orphans()


async def j_flush(context: ContextTypes.DEFAULT_TYPE) -> None:
    await svc(context).notifier.flush()


async def j_watchdog(context: ContextTypes.DEFAULT_TYPE) -> None:
    await svc(context).ctx.watchdog.tick()


async def j_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    s = svc(context)
    import time
    await s.sender.send(s.chat_id, build_report(s.ctx, time.time() - 86400, title="📝 Rapport quotidien"), None, True)


async def j_build(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toutes les 20 s : version.txt changé → compilation auto ; nouvel .ex5 dans BASE → proposition de déploiement."""
    s = svc(context)
    if s.ctx.deploy.running or s.router.building:
        return
    req = pending_build(s.ctx.cfg, s.ctx.db)
    if req is not None:                                          # version.txt modifié → compilation automatique
        await s.router.run_auto_build(req)
        return
    nb = detect_new_build(s.ctx.cfg, s.ctx.db)
    if nb is not None:
        await s.router.on_new_build(nb)


async def j_update(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Nouveau commit sur GitHub → paquet téléchargé → proposition d'installation (une fois par état du paquet)."""
    from core import github as gh, update as upd
    s = svc(context)
    cfg = s.ctx.cfg
    if not cfg.update.enabled or s.ctx.deploy.running or s.router.building:
        return
    loop = asyncio.get_running_loop()
    if cfg.update.github:
        inst_c = (s.ctx.db.meta_get("installed_commit") or "").split(" ")[0]
        res = await loop.run_in_executor(None, lambda: gh.fetch(cfg, installed=inst_c))
        if res.token_expires:
            s.ctx.db.meta_set("gh_token_exp", res.token_expires)
            left = gh.days_left(res.token_expires)
            today = time.strftime("%Y-%m-%d")
            if left is not None and left <= 15 and s.ctx.db.meta_get("gh_exp_alert") != today:
                s.ctx.db.meta_set("gh_exp_alert", today)                # une alerte par jour
                await s.sender.send(s.chat_id, f"🔑 Le jeton GitHub expire dans <b>{max(left, 0)} jour(s)</b> ({res.token_expires}).\n"
                                    "Crée-en un nouveau (lecture seule sur ScalpX), remplace GITHUB_TOKEN dans "
                                    "C:\\ScalpX\\bot\\.env puis 🛠 Système → 🔄 Redémarrer le bot.", None, True)
        err = "" if res.ok else res.error
        if err != s.ctx.db.meta_get("gh_err"):                    # une alerte par problème, et une à la résolution
            s.ctx.db.meta_set("gh_err", err)
            if err:
                log.warning("mise à jour GitHub : %s", err)
                await s.sender.send(s.chat_id, f"⚠️ Mises à jour : {render.esc(err)}", None, True)
        if not res.ok:
            return
    plan = await loop.run_in_executor(None, upd.plan_update, cfg, Path(__file__).resolve().parent.parent)
    if plan is None:
        return
    pending = "" if plan.empty else plan.stamp
    if pending != s.ctx.db.meta_get("update_pending"):          # 🔔 sur le bouton « Mises à jour » du tableau de bord
        s.ctx.db.meta_set("update_pending", pending)
        await s.dash.refresh(force=True)
    if plan.empty or s.ctx.db.meta_get("update_offered") == plan.stamp:
        return
    s.ctx.db.meta_set("update_offered", plan.stamp)
    await s.router.on_update_available(plan)


async def j_health(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ping de surveillance externe (HEALTHCHECK_URL) : le silence signale un VPS ou un bot arrêté."""
    from core import monitor
    s = svc(context)
    if s.ctx.cfg.healthcheck_url:
        ok = await asyncio.get_running_loop().run_in_executor(None, monitor.ping, s.ctx.cfg)
        s.ctx.db.meta_set("health_ok", "1" if ok else "0")


async def j_backup(context: ContextTypes.DEFAULT_TYPE) -> None:
    from core import backup
    s = svc(context)
    try:
        dest = await asyncio.get_running_loop().run_in_executor(None, backup.run_backup, s.ctx.cfg, s.ctx.db)
        log.info("historique sauvegardé : %s", dest)
    except Exception as exc:
        log.warning("sauvegarde de l'historique impossible : %s", exc)
        await s.sender.send(s.chat_id, f"⚠️ Sauvegarde de l'historique impossible : {render.esc(str(exc))}", None, True)


async def j_prune(context: ContextTypes.DEFAULT_TYPE) -> None:
    svc(context).ctx.db.prune()


async def startup(app: Application) -> None:
    """Après le démarrage : relance échelonnée des instances DEMARRE, reprise d'un déploiement interrompu."""
    s: Services = app.bot_data["svc"]
    s.router.sync_base_set()                                        # preset ⭐ actif → .set de BASE
    try:
        launched = await s.ctx.watchdog.startup_launch()
        await s.sender.reply_menu(s.chat_id, f"🤖 Bot {bot_version()} démarré · {len(launched)} terminal(aux) relancé(s).\n"
                                              f"Le bouton <b>{MENU_BUTTON}</b> (en bas) affiche le panneau.")
        await s.dash.bring_down()
        if s.ctx.deploy.interrupted():
            await s.router.cmd_deployer()
    except Exception:
        log.exception("démarrage : erreur")


async def post_init(app: Application) -> None:
    s: Services = app.bot_data["svc"]
    await app.bot.delete_my_commands()                  # aucune commande « / » : tout passe par des boutons
    app.create_task(startup(app))


def build_application(cfg: Config, ctx_factory) -> Application:
    """ctx_factory() → Ctx. Crée l'application, les services et enregistre handlers et tâches."""
    tz = ZoneInfo(cfg.reports.timezone)
    app = (ApplicationBuilder().token(cfg.token).defaults(Defaults(tzinfo=tz)).post_init(post_init)
           .connect_timeout(20).read_timeout(30).write_timeout(30)              # réseau du VPS parfois lent
           .connection_pool_size(8).pool_timeout(20)                            # plusieurs envois en parallèle :
           .build())                                                            # un bouton n'attend plus les éditions du panneau
    ctx: Ctx = ctx_factory()
    app.bot_data["svc"] = Services(cfg, ctx, app.bot)
    app.bot_data["allowed"] = set(cfg.allowed_chats)

    app.add_handler(TypeHandler(Update, h_guard), group=-1)
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(w_new, pattern=r"^n$"),
                      CallbackQueryHandler(w_recon, pattern=r"^a:[0-9a-f]{8}:recon$")],
        states={
            LOGIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, w_login)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, w_password)],
            SERVER: [CallbackQueryHandler(w_server, pattern=r"^srv:(\d+|new)$")],
            SERVER_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, w_server_text)],
            ALIAS: [MessageHandler(filters.TEXT & ~filters.COMMAND, w_alias),
                    CallbackQueryHandler(w_alias_skip, pattern=r"^alias:skip$")],
            CONFIRM: [CallbackQueryHandler(w_confirm, pattern=r"^wz:(go|no)$")],
            ConversationHandler.TIMEOUT: [TypeHandler(Update, w_timeout)],
        },
        fallbacks=[CallbackQueryHandler(w_cancel, pattern=r"^wz:cancel$")],
        conversation_timeout=300, allow_reentry=True,
    )
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(h_cb, pattern=CB_PATTERN))
    app.add_handler(MessageHandler(filters.TEXT, h_text))
    app.add_error_handler(h_error)

    jq = app.job_queue
    t = cfg.timing
    jq.run_repeating(j_status, interval=t.status_interval_s, first=2, job_kwargs=JOB_KW)
    jq.run_repeating(j_events, interval=t.events_interval_s, first=3, job_kwargs=JOB_KW)
    jq.run_repeating(j_clean, interval=60, first=30, job_kwargs=JOB_KW)
    jq.run_repeating(j_flush, interval=max(1, cfg.alerts.batch_window_s), first=4, job_kwargs=JOB_KW)
    jq.run_repeating(j_presets, interval=300, first=90, job_kwargs=JOB_KW)  # robots conformes au preset ⭐ actif ?
    jq.run_repeating(j_dash, interval=2, first=5, job_kwargs=JOB_KW)        # refresh() : toutes les refresh_s, republication après repost_s
    jq.run_repeating(j_watchdog, interval=t.watchdog_interval_s, first=20, job_kwargs=JOB_KW)
    jq.run_repeating(j_build, interval=20, first=40, job_kwargs=JOB_KW)
    jq.run_repeating(j_update, interval=max(20, cfg.update.check_s), first=60, job_kwargs=JOB_KW)
    jq.run_repeating(j_health, interval=60, first=15, job_kwargs=JOB_KW)
    jq.run_daily(j_report, time=dtime(cfg.reports.daily_hour, 0), job_kwargs=JOB_KW_DAILY)
    jq.run_daily(j_prune, time=dtime(4, 0), job_kwargs=JOB_KW_DAILY)
    jq.run_daily(j_backup, time=dtime(cfg.backup.hour % 24, 0), job_kwargs=JOB_KW_DAILY)
    return app
