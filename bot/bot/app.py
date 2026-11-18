"""Assemblage python-telegram-bot v21+ (asyncio, JobQueue) : handlers fins, toute la logique est dans router/wizard/dashboard."""
from __future__ import annotations

import asyncio
import collections
import io
import logging
import os
import time
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Conflict, Forbidden, NetworkError, RetryAfter, TelegramError
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
INPUT_SCREENS = ("btw", "bkp", "key", "opt")                 # Bot 4.8.7 : écrans du tableau de bord qui attendent une saisie


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
        except TelegramError as e:                                      # déjà effacé / trop ancien : courant, sans gravité
            (log.debug if type(e).__name__ == "BadRequest" else log.warning)("suppression de message impossible : %s", type(e).__name__)
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
        self.notifier = Notifier(self.sender, self.chat_id, cfg.alerts.silent_mode, hold_s=cfg.alerts.group_hold_s,
                                 total=lambda: len(ctx.db.list()))
        self.router = Router(ctx, self.sender, self.chat_id, self.dash, self.conf, self.notifier)
        self.dash.waiting = lambda: self.router.awaiting_token                  # Bot 4.8.3
        self.dash.waiting_any = lambda: self.router.awaiting_any                # Bot 4.8.7
        ctx.watchdog.alert = self.router.alert
        self.monitor = Monitor(cfg, ctx.db, ctx.status, EventReader(cfg.paths.common()), self.notifier.emit)
        self.wizard = WizardCore(ctx, self.sender, self.chat_id, self.on_created)
        self.wizard.dash_refresh = lambda: self.dash.refresh(force=True)
        self.router.remember_server = self.wizard._remember_server            # Bot 3.20.0 : création en lot

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
SEEN_UPDATES: "collections.deque[int]" = collections.deque(maxlen=500)   # Bot 4.8.10 : derniers update_id traités


def seen_before(update_id) -> bool:
    """Bot 4.8.10 : Telegram peut renvoyer le MÊME événement (connexion qui hoquette pendant la réception) : traité une seule
    fois. C'était la cause des doubles de la journée (bouton, message, tableau de bord en 2 exemplaires)."""
    if not isinstance(update_id, int):
        return False
    if update_id in SEEN_UPDATES:
        return True
    SEEN_UPDATES.append(update_id)
    return False


async def h_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ne répond qu'aux chat_id autorisés ; tout le reste est ignoré et journalisé. Un événement reçu deux fois est ignoré."""
    if seen_before(getattr(update, "update_id", None)):
        log.warning("événement Telegram %s reçu deux fois : ignoré", getattr(update, "update_id", "?"))
        raise ApplicationHandlerStop
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
    s = svc(context)
    if m is None:                                               # Bot 4.8.9 : message MODIFIÉ après envoi : jamais retraité
        return
    if m is not None:
        try:
            await s.sender.delete(update.effective_chat.id, m.message_id)    # garde la conversation propre
            s.dash.message_deleted(m.message_id)
        except Exception:
            pass
    # Bot 4.8.9 : plus de « 📊 Bouton remis en bas » glissé au milieu des échanges (seulement au démarrage du bot, et par
    # 🛠 Système → 📊 Remettre le bouton du bas).
    # Bot 4.8.8 : 💻 commande et 🏦 courtier ont leur propre message : le tableau de bord n'est jamais ramené pour eux
    busy = s.router.awaiting_any and not (s.router.awaiting_cmd or s.router.awaiting_broker)
    await s.router.on_text((m.text or "") if m is not None else "")
    # Bot 4.8.3 / 4.8.7 : réponse à une saisie (jeton, mot de passe, assistant, objectif…) → l'écran qui répond est EN BAS
    if busy and not s.dash.is_last and (s.dash.screen[0] in INPUT_SCREENS or s.dash._flash_text()):
        await s.dash.show()


async def h_doc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bot 3.16.0 : fichier reçu. Seul usage : 📥 import de presets (fichier exporté par l'autre bot)."""
    s = svc(context)
    m = update.message
    if m is None or m.document is None:
        return
    bulk = s.router.awaiting_bulk                            # Bot 3.20.0 : 📄 fichier de comptes (mots de passe)
    if bulk:
        try:                                                 # effacé AVANT toute lecture : rien ne reste dans la conversation
            await s.sender.delete(update.effective_chat.id, m.message_id)
        except Exception:
            log.warning("fichier de comptes : suppression du message impossible")
    if not s.router.awaiting_import and not bulk:
        # Bot 4.8.2 : fichier envoyé sans bouton → reconnu tout seul (avant : « ignoré », même juste après un import réussi)
        data = b""
        if (m.document.file_size or 0) <= 20 * 2**20:
            try:
                data = bytes(await (await m.document.get_file()).download_as_bytearray())
            except Exception as exc:
                log.warning("fichier reçu illisible : %s", exc)
        from core import presets as ps
        kind = ps.sniff_file(data)
        if kind == "presets":
            try:
                await s.sender.delete(update.effective_chat.id, m.message_id)
            except Exception:
                pass
            await s.router.preset_import_file(data)
            return
        if kind == "comptes":                                   # mots de passe : effacé, jamais traité sans le bouton
            try:
                await s.sender.delete(update.effective_chat.id, m.message_id)
            except Exception:
                log.warning("fichier de comptes : suppression du message impossible")
            await s.sender.send(s.chat_id, "🔒 Fichier de comptes reçu et <b>effacé</b> (mots de passe). Pour créer ces comptes : "
                                "👥 Comptes → 📄 Ajouter des comptes (fichier), puis renvoie-le.", [[("📄 Comptes (fichier)", "d:bulk"),
                                                                                                  ("✖ Fermer", "d:close")]], True)
            return
        await s.sender.send(s.chat_id, "📎 Fichier non reconnu : ni presets / sauvegarde ScalpX, ni liste de comptes "
                            "(numéro:mot de passe:serveur).", [[("✖ Fermer", "d:close")]], True)
        return
    s.router.awaiting_import = False
    s.router.awaiting_bulk = False
    if (m.document.file_size or 0) > (200_000 if bulk else 20 * 2**20):   # Telegram : 20 Mo au plus pour un bot
        if bulk:
            await s.router.bulk_received("")
        else:
            await s.router.preset_import("")               # message « fichier trop gros / rien de reconnu »
        return
    try:
        f = await m.document.get_file()
        data = bytes(await f.download_as_bytearray())
    except Exception as exc:
        log.warning("import de presets : fichier illisible : %s", exc)
        data = b""
    if bulk:
        await s.router.bulk_received(data.decode("utf-8-sig", errors="replace"))
        return
    try:
        await s.sender.delete(update.effective_chat.id, m.message_id)
    except Exception:
        pass
    await s.router.preset_import_file(data)                         # Bot 4.2.0 : tout fichier de sauvegarde, chiffré ou non


async def h_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q is None or q.data is None:
        return
    try:
        await q.answer()                                # coupe le sablier tout de suite
    except TelegramError:
        pass
    s = svc(context)
    await s.router.on_callback(q.data, q.message.message_id if q.message else 0)
    # Bot 4.8.3 : bouton de sauvegarde / jeton touché sur un AUTRE message (proposition, alerte) : l'écran qui s'ouvre
    # est montré en bas, au lieu d'être modifié plus haut dans la conversation.
    sub = q.data.split(":")[1] if q.data.startswith("d:") and ":" in q.data else ""
    # Bot 4.8.7 : toute étape qui attend une saisie dans le tableau de bord (assistant du terminal de test…) aussi
    if not s.dash.is_last and (sub in ("bkp", "bkpw", "bkplain", "kset", "bkt", "bkown", "key")
                               or (s.router.awaiting_any and s.dash.screen[0] in INPUT_SCREENS)):
        await s.dash.show()


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


async def w_retry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Création refusée (mauvais mot de passe) : 🔑 ressaisir / 🌐 changer de serveur / ✖ abandonner."""
    q = update.callback_query
    await q.answer()
    w, chat = svc(context).wizard, update.effective_chat.id
    act = q.data.split(":")[1]
    if act == "rpw":
        return await w.retry_password(chat)
    if act == "rsrv":
        return await w.retry_server(chat)
    return await w.retry_abandon(chat)


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
    if isinstance(err, NetworkError):                    # coupure réseau (polling) : une ligne, sans trace, comme avant
        log.error("erreur non gérée : %s: %s", type(err).__name__, err)
        return
    where = ""
    if isinstance(update, Update) and update.callback_query is not None:
        where = f" (bouton {update.callback_query.data!r})"
    elif getattr(context, "job", None) is not None:
        where = f" (tâche {getattr(context.job, 'name', '?')})"
    log.error("erreur non gérée%s : %s: %s", where, type(err).__name__, err,
              exc_info=(type(err), err, err.__traceback__) if err is not None else None)


# ---- tâches périodiques
# Réglages APScheduler de toutes les tâches : jamais deux exécutions en parallèle, les exécutions en retard sont fusionnées,
# et un retard jusqu'à 5 s est toléré (défaut APScheduler : 1 s → tâche sautée au moindre blocage de la boucle).
JOB_KW = {"coalesce": True, "max_instances": 1, "misfire_grace_time": 5}
SLOW_JOB_S = 1.0          # au-delà, la tâche est signalée dans bot.log (elle a retenu la boucle : cause des « missed by »)


RUNNING_JOBS: dict[str, float] = {}      # tâches en cours (nom → début) : citées quand la boucle est bloquée


def timed(fn):
    """Note la tâche en cours (pour le détecteur de blocage de la boucle). Une tâche longue qui ATTEND (réseau, compilation
    dans un fil) ne bloque rien : seule la très longue durée est notée, sans alarme."""
    import functools

    @functools.wraps(fn)
    async def run(context):
        t0 = time.monotonic()
        RUNNING_JOBS[fn.__name__] = t0
        try:
            return await fn(context)
        finally:
            RUNNING_JOBS.pop(fn.__name__, None)
            dt = time.monotonic() - t0
            if dt > 60:
                log.info("tâche %s : %.0f s (attente normale : compilation, réseau…)", fn.__name__, dt)
    return run


async def loop_lag_watch(step: float = 0.5, freeze_path: "Path | None" = None) -> None:
    """Détecteur de blocage : si la boucle du bot a été figée plus de SLOW_JOB_S (calcul ou accès disque fait dans la boucle),
    une ligne dit combien de temps et quelles tâches tournaient. C'est la vraie cause des « missed by » d'APScheduler."""
    # Bot 3.14.2 : boucle figée plus de 30 s → pile de chaque fil écrite dans data\logs\freeze_trace.log (par un fil à part,
    # pendant le blocage) : on voit enfin OÙ le bot attend ; la ligne du journal donne aussi la RAM et le CPU du VPS.
    import faulthandler
    freeze_f = None
    if freeze_path is not None:
        try:
            freeze_path.parent.mkdir(parents=True, exist_ok=True)
            freeze_f = open(freeze_path, "a", encoding="utf-8")
        except OSError:
            freeze_f = None
    while True:
        if freeze_f is not None:
            try:
                faulthandler.dump_traceback_later(30, repeat=False, file=freeze_f)   # réarmé à chaque tour (0,5 s)
            except (OSError, ValueError):
                freeze_f = None
        t0 = time.monotonic()
        await asyncio.sleep(step)
        lag = time.monotonic() - t0 - step
        if lag > SLOW_JOB_S:
            who = ", ".join(sorted(RUNNING_JOBS)) or "aucune tâche périodique (bouton ou message Telegram ?)"
            try:
                import psutil
                vm = psutil.virtual_memory()
                load = f" · VPS : RAM {vm.percent:.0f} % ({vm.available / 1048576:.0f} Mo libres), CPU {psutil.cpu_percent(None):.0f} %"
            except Exception:
                load = ""
            log.warning("boucle du bot bloquée %.1f s (en cours : %s)%s", lag, who, load)
JOB_KW_DAILY = {**JOB_KW, "misfire_grace_time": 300}        # tâches quotidiennes : jamais ratées pour quelques secondes de retard

async def j_presets(context: ContextTypes.DEFAULT_TYPE) -> None:
    await svc(context).router.filters_alerts_off()      # une fois par compte (voir la fonction)
    await svc(context).router.check_presets()


async def j_dash(context: ContextTypes.DEFAULT_TYPE) -> None:
    await svc(context).dash.refresh()


async def j_status(context: ContextTypes.DEFAULT_TYPE) -> None:
    await asyncio.to_thread(svc(context).monitor.poll_status)      # Bot 3.10.0 : lectures et base hors de la boucle du bot


async def j_events(context: ContextTypes.DEFAULT_TYPE) -> None:
    await svc(context).monitor.poll_events()


async def j_clean(context: ContextTypes.DEFAULT_TYPE) -> None:
    svc(context).ctx.cmdq.cleanup_orphans()


async def j_flush(context: ContextTypes.DEFAULT_TYPE) -> None:
    await svc(context).notifier.flush()


async def j_opt(context: ContextTypes.DEFAULT_TYPE) -> None:
    """🤖 Optimiseur : lancé chaque nuit à l'heure choisie (vérifié toutes les minutes)."""
    s = svc(context)
    o = getattr(s.router, "optim", None)
    if o is not None and o.due():
        s.router._bg(o.run())


async def j_watchdog(context: ContextTypes.DEFAULT_TYPE) -> None:
    await svc(context).ctx.watchdog.tick()


async def j_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    s = svc(context)
    import time
    await s.sender.send(s.chat_id, build_report(s.ctx, time.time() - 86400, title="📝 Rapport quotidien"), None, True)


async def j_withdraw_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Chaque jour : rappel des comptes à 500 qui attendent toujours un retrait (rien s'il n'y en a pas)."""
    import time
    s = svc(context)
    txt = render.withdraw_reminder(s.ctx.views(), s.dash.withdraw_since(), time.time())
    if txt:
        log.info("rappel des retraits envoyé")
        await s.sender.send(s.chat_id, txt, None, False)


async def j_build(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toutes les 20 s : version.txt changé → compilation auto ; nouvel .ex5 dans BASE → proposition de déploiement."""
    from core.update import STOP
    s = svc(context)
    if s.ctx.deploy.running or s.router.building or s.router.installing:   # installation : version.txt pas encore écrit
        return
    if STOP.is_set():                     # le bot redémarre : la compilation sera faite par le nouveau (ne retarde pas l'arrêt)
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
    if not cfg.update.enabled or s.ctx.deploy.running or s.router.building or s.router.installing:
        return
    if upd.STOP.is_set():                 # redémarrage en cours : le nouveau bot vérifiera lui-même
        return
    loop = asyncio.get_running_loop()
    if cfg.update.github:
        inst_c = (s.ctx.db.meta_get("installed_commit") or "").split(" ")[0]
        res = await loop.run_in_executor(None, lambda: gh.fetch(cfg, installed=inst_c))
        err = "" if res.ok else res.error
        if err != s.ctx.db.meta_get("gh_err"):                    # une alerte par problème, et une à la résolution
            s.ctx.db.meta_set("gh_err", err)
            if err:
                log.warning("mise à jour GitHub : %s", err)
                await s.sender.send(s.chat_id, f"⚠️ Mises à jour : {render.esc(err)}", None, True)
        if not res.ok:
            return
    # Bot 3.6.2 : la comparaison paquet ↔ installation relit tous les fichiers (bloquait la boucle du bot 3 à 14 s sur un
    # VPS chargé) : refaite seulement si le commit, l'installation ou le preset ⭐ changent, ou toutes les 15 min.
    # Bot 4.9.1 : clé = commit du paquet RÉELLEMENT téléchargé (avant : commit vu sur GitHub, déjà connu pendant les 2 min
    # d'attente → le paquet arrivé ensuite gardait le plan « rien à installer » en cache jusqu'à 15 min : pas de 🔔)
    key = (s.ctx.db.meta_get("installed_commit") or "", gh.local_sha(Path(cfg.update.source)) if cfg.update.github else "",
           s.dash.store.active,
           s.ctx.db.meta_get("update_offered") or "")
    cache = getattr(s, "_plan_cache", None)
    if cache is not None and cache[0] == key and time.monotonic() - cache[1] < 900:
        plan = cache[2]
    else:
        plan = await loop.run_in_executor(None, upd.plan_update, cfg, Path(__file__).resolve().parent.parent)
        s._plan_cache = (key, time.monotonic(), plan)
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


async def j_prepare(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bot 4.5.0 : BASE pas encore prêt (compilation de départ de MT5) → préparé en tâche de fond."""
    asyncio.get_running_loop().create_task(svc(context).router.prepare_base())


async def j_groups(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bot 4.10.0 : comptes encore dans le préfixe Wine principal → répartis dans les environnements (un par un),
    en tâche de fond, avec un message de suivi."""
    from pathlib import Path as _P
    from mt5 import groups
    s = svc(context)
    cfg = s.ctx.cfg
    if getattr(s, "_groups_task", None) is not None:
        return
    if s.ctx.deploy.running or s.router.building or s.router.installing:
        return
    on = int(getattr(cfg.wine, "group_size", 0) or 0) > 0
    if on:
        if not any(groups.group_of(_P(i.folder)) == 0 and s.ctx.mgr.folder_ok(i) and i.desired_state != "EN_CREATION"
                   for i in s.ctx.db.list()):
            return
    else:                                                       # Bot 4.11.0 : groupes désactivés → retour au principal
        insts, pres = groups.leftovers(cfg, s.ctx.db)
        if not insts and not pres:
            return

    async def run() -> None:
        mid = 0

        async def say(text: str) -> None:
            nonlocal mid
            try:
                if mid and await s.sender.edit(s.chat_id, mid, text, None) in (EditResult.OK, EditResult.SAME):
                    return
            except Exception:
                pass
            mid = await s.sender.send(s.chat_id, text, None, True)

        try:
            if on:
                await say("🍷 <b>Répartition des terminaux</b> dans plusieurs environnements Wine (moins d'attente derrière "
                          "wineserver) : un compte à la fois, chacun redémarre ≈ 1 min…")
                rep = await groups.rebalance(s.ctx, say)
                if rep:
                    await say("🍷 <b>Répartition terminée</b>\n" + render.esc(groups.summary(cfg, s.ctx.db)) + "\n"
                              + "\n".join(render.esc(r) for r in rep))
            else:
                await say("🍷 <b>Retour à un seul environnement Wine</b> (la répartition n'a pas fait gagner de CPU) : "
                          "un compte à la fois, chacun redémarre ≈ 1 min…")
                rep = await groups.regroup(s.ctx, say)
                await say("🍷 <b>Retour terminé</b> : tous les terminaux sont dans l'environnement principal.\n"
                          + "\n".join(render.esc(r) for r in rep))
        except Exception as exc:
            log.exception("répartition des terminaux")
            await s.sender.send(s.chat_id, f"⚠️ Répartition des terminaux interrompue : {render.esc(str(exc))}", None, True)
        finally:
            s._groups_task = None

    s._groups_task = asyncio.get_running_loop().create_task(run())


async def j_install(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bot 3.19.0 (Linux) : installation système plus ancienne que le paquet → 🔔 🔧."""
    await svc(context).router.check_install()


async def j_ghbackup(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bot 3.17.0 : ☁️ presets sauvegardés sur GitHub quand ils changent."""
    await svc(context).router.backup_presets()


async def j_backup(context: ContextTypes.DEFAULT_TYPE) -> None:
    from core import backup
    s = svc(context)
    try:
        dest = await asyncio.get_running_loop().run_in_executor(None, backup.run_backup, s.ctx.cfg, s.ctx.db)
        log.info("historique sauvegardé : %s", dest)
        await s.router.full_backup(quiet=True)                      # Bot 3.21.0 : ☁️ copie complète sur GitHub
    except Exception as exc:
        log.warning("sauvegarde de l'historique impossible : %s", exc)
        await s.sender.send(s.chat_id, f"⚠️ Sauvegarde de l'historique impossible : {render.esc(str(exc))}", None, True)


async def j_prune(context: ContextTypes.DEFAULT_TYPE) -> None:
    svc(context).ctx.db.prune()


async def startup(app: Application) -> None:
    """Après le démarrage : relance échelonnée des instances DEMARRE, reprise d'un déploiement interrompu."""
    s: Services = app.bot_data["svc"]
    s.router.sync_base_set()                                        # preset ⭐ actif → .set de BASE
    note = s.ctx.db.meta_get("restore_note")                        # Bot 3.21.0 : ☁️ restauration appliquée au démarrage
    if note:
        s.ctx.db.meta_set("restore_note", "")
        await s.sender.send(s.chat_id, ("❌ <b>Restauration impossible</b> : " if note.startswith("ÉCHEC") else
                                        "☁️ <b>Restauration terminée</b> : ") + render.esc(note.removeprefix("ÉCHEC : ")), None, False)
    w = getattr(s.ctx.mgr.procs, "wine", None)                      # Bot 3.21.0 : choix WebView2 fait dans 🖥 Serveur
    if w is not None and s.ctx.db.meta_get("webview"):
        w.webview = s.ctx.db.meta_get("webview") == "on"
    try:                                                            # Bot 4.2.0 : ☁️ sauvegarde proposée si non activée
        await s.router.offer_backup()
        await s.router.backup_ready()                               # dépôt de sauvegarde retrouvé (ancienne installation)
    except Exception:
        log.exception("sauvegarde : proposition / dépôt")
    try:
        from core import presets as cps
        gone = cps.remove_recommended(s.dash.store)                 # Bot 3.18.1 : preset « conseille » retiré (non voulu)
        if gone:
            log.info("preset(s) %s supprimé(s)", ", ".join(gone))
    except Exception:
        log.exception("preset conseillé : suppression impossible")
    try:
        n = s.ctx.db.bt_interrupted()                              # 🧪 backtest en cours au redémarrage : suivi perdu
        if n:
            log.info("démarrage : %d backtest(s) interrompu(s)", n)
    except Exception:
        log.exception("démarrage : backtests interrompus")
    try:
        n = await s.ctx.creator.cleanup_refused()                  # créations refusées laissées en suspens
        if n:
            log.info("démarrage : %d création(s) refusée(s) en suspens abandonnée(s)", n)
    except Exception:
        log.exception("démarrage : nettoyage des créations refusées")
    try:
        launched = await s.ctx.watchdog.startup_launch()
        await s.sender.reply_menu(s.chat_id, f"🤖 Bot {bot_version()} démarré · {len(launched)} terminal(aux) relancé(s).\n"
                                              f"Le bouton <b>{MENU_BUTTON}</b> (en bas) affiche le panneau.")
        await s.router.restart_report()                            # « ✅ Redémarré en N s » dans le message de la mise à jour
        await s.dash.bring_down()
        if s.ctx.deploy.interrupted():
            await s.router.cmd_deployer()
    except Exception:
        log.exception("démarrage : erreur")


async def post_init(app: Application) -> None:
    s: Services = app.bot_data["svc"]
    await app.bot.delete_my_commands()                  # aucune commande « / » : tout passe par des boutons
    app.create_task(startup(app))
    app.create_task(loop_lag_watch(freeze_path=s.ctx.cfg.paths.data / "logs" / "freeze_trace.log"))


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
                      CallbackQueryHandler(w_recon, pattern=r"^a:[0-9a-f]{8}:recon$"),
                      CallbackQueryHandler(w_retry, pattern=r"^wz:(rpw|rsrv|rab)$")],
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
    app.add_handler(MessageHandler(filters.Document.ALL, h_doc))                 # Bot 3.16.0 : import de presets
    app.add_error_handler(h_error)

    jq = app.job_queue
    t = cfg.timing
    jq.run_repeating(timed(j_status), interval=t.status_interval_s, first=2, job_kwargs=JOB_KW)
    jq.run_repeating(timed(j_events), interval=t.events_interval_s, first=3, job_kwargs=JOB_KW)
    jq.run_repeating(timed(j_clean), interval=60, first=30, job_kwargs=JOB_KW)
    jq.run_repeating(timed(j_flush), interval=max(1, cfg.alerts.batch_window_s), first=4, job_kwargs=JOB_KW)
    jq.run_repeating(timed(j_presets), interval=max(60, t.presets_check_s), first=90, job_kwargs=JOB_KW)  # robots conformes au preset ⭐ ?
    jq.run_repeating(timed(j_dash), interval=2, first=5, job_kwargs=JOB_KW)        # refresh() : toutes les refresh_s, republication après repost_s
    jq.run_repeating(timed(j_watchdog), interval=t.watchdog_interval_s, first=20, job_kwargs=JOB_KW)
    jq.run_repeating(timed(j_opt), interval=60, first=120, job_kwargs=JOB_KW)
    jq.run_repeating(timed(j_build), interval=20, first=40, job_kwargs=JOB_KW)
    # Bot 4.2.0 : dépôt public lu sans jeton → 60 appels GitHub par heure au plus : vérification toutes les 5 min
    upd_s = max(120, cfg.update.check_s)          # Bot 4.8.1 : dépôt public, sans jeton (60 requêtes/h max chez GitHub)
    jq.run_repeating(timed(j_update), interval=upd_s, first=60, job_kwargs=JOB_KW)
    jq.run_repeating(timed(j_health), interval=60, first=15, job_kwargs=JOB_KW)
    jq.run_daily(timed(j_report), time=dtime(cfg.reports.daily_hour, 0), job_kwargs=JOB_KW_DAILY)
    if 0 <= cfg.reports.withdraw_reminder_hour <= 23:
        jq.run_daily(timed(j_withdraw_reminder), time=dtime(cfg.reports.withdraw_reminder_hour, 0), job_kwargs=JOB_KW_DAILY)
    jq.run_daily(timed(j_prune), time=dtime(4, 0), job_kwargs=JOB_KW_DAILY)
    jq.run_daily(timed(j_backup), time=dtime(cfg.backup.hour % 24, 0), job_kwargs=JOB_KW_DAILY)
    jq.run_repeating(timed(j_ghbackup), interval=max(60, cfg.backup.github_check_s), first=90, job_kwargs=JOB_KW)
    jq.run_repeating(timed(j_install), interval=300, first=120, job_kwargs=JOB_KW)
    jq.run_repeating(timed(j_groups), interval=300, first=240, job_kwargs=JOB_KW)      # Bot 4.10.0 : environnements Wine
    jq.run_repeating(timed(j_prepare), interval=120, first=10, job_kwargs=JOB_KW)
    return app
