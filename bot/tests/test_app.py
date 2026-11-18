"""Assemblage Telegram : utilise python-telegram-bot s'il est installé, sinon un stub minimal (import + câblage)."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

try:
    import telegram  # noqa: F401
    REAL = True
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent / "stubs"))
    REAL = False

from telegram.error import BadRequest, Conflict, Forbidden, RetryAfter   # noqa: E402
from telegram.ext import ApplicationHandlerStop                          # noqa: E402

from bot import app as botapp                                            # noqa: E402
from bot.ui import EditResult                                            # noqa: E402
from core.config import Config                                           # noqa: E402


class FakeBot:
    def __init__(self, exc=None):
        self.exc, self.sent, self.deleted = exc, [], []

    async def edit_message_text(self, **kw):
        if self.exc:
            raise self.exc

    async def send_message(self, **kw):
        self.sent.append(kw)
        return SimpleNamespace(message_id=77)

    async def delete_message(self, **kw):
        if self.exc:
            raise self.exc
        self.deleted.append(kw)


@pytest.mark.asyncio
async def test_sender_mappe_les_erreurs_d_edition():
    s = botapp.TelegramSender(FakeBot(BadRequest("Message is not modified: specified new message content...")))
    assert await s.edit(1, 2, "x") == EditResult.SAME
    s = botapp.TelegramSender(FakeBot(BadRequest("Message to edit not found")))
    assert await s.edit(1, 2, "x") == EditResult.GONE
    s = botapp.TelegramSender(FakeBot(Forbidden("bot was blocked")))
    assert await s.edit(1, 2, "x") == EditResult.GONE
    s = botapp.TelegramSender(FakeBot(RetryAfter(12)))
    assert await s.edit(1, 2, "x") == EditResult.RETRY and s.last_retry_after == 12
    s = botapp.TelegramSender(FakeBot())
    assert await s.edit(1, 2, "x") == EditResult.OK
    assert await s.send(1, "hello", [[("A", "a:1")]], True) == 77
    assert await botapp.TelegramSender(FakeBot(Forbidden("x"))).delete(1, 2) is False


@pytest.mark.asyncio
async def test_garde_chat_non_autorise_ignore_et_journalise(caplog):
    ctx = SimpleNamespace(application=SimpleNamespace(bot_data={"allowed": {42}}))
    good = SimpleNamespace(effective_chat=SimpleNamespace(id=42), effective_user=SimpleNamespace(id=1))
    bad = SimpleNamespace(effective_chat=SimpleNamespace(id=666), effective_user=SimpleNamespace(id=2))
    await botapp.h_guard(good, ctx)                                       # autorisé : rien
    with caplog.at_level("WARNING"):
        with pytest.raises(ApplicationHandlerStop):
            await botapp.h_guard(bad, ctx)
    assert "non autorisé" in caplog.text and "666" in caplog.text
    with pytest.raises(ApplicationHandlerStop):
        await botapp.h_guard(SimpleNamespace(effective_chat=None, effective_user=None), ctx)


@pytest.mark.asyncio
async def test_erreur_409_arrete_le_bot(monkeypatch):
    codes = []
    monkeypatch.setattr(botapp.os, "_exit", lambda c: codes.append(c))
    await botapp.h_error(None, SimpleNamespace(error=Conflict("terminated by other getUpdates request")))
    assert codes == [3]


def test_assemblage_complet(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    from bot.actions import Ctx
    from mt5.creator import InstanceCreator
    from mt5.deploy import DeployManager
    from mt5.watchdog import Watchdog
    cfg.token, cfg.allowed_chats = "123456:ABC-test-token", [42]

    def factory():
        return Ctx(cfg, db, status, cmdq, mgr, InstanceCreator(cfg, db, mgr, status), DeployManager(cfg, db, mgr, status, cmdq),
                   Watchdog(cfg, db, mgr, status, lambda *a: None))

    app = botapp.build_application(cfg, factory)
    assert app.bot_data["allowed"] == {42} and app.bot_data["svc"].chat_id == 42
    assert app.bot_data["svc"].ctx.watchdog.alert == app.bot_data["svc"].router.alert
    if not REAL:
        groups = [g for g, _ in app.handlers]
        assert -1 in groups and len(app.handlers) >= 4
        assert {j[1] for j in app.job_queue.jobs} >= {"j_dash", "j_events", "j_status", "j_flush", "j_watchdog", "j_report"}
        # chaque tâche : une seule exécution à la fois, retards fusionnés, tolérance de retard > 1 s (défaut APScheduler)
        for j in app.job_queue.jobs:
            kw = j[3]
            assert kw and kw["max_instances"] == 1 and kw["coalesce"] is True and kw["misfire_grace_time"] >= 5, j


def test_tous_les_prefixes_de_boutons_sont_captes():
    """Chaque préfixe traité par le routeur doit passer le filtre du CallbackQueryHandler (sinon le bouton ne fait rien)."""
    import inspect
    import re
    from bot.router import Router
    src = inspect.getsource(Router._on_callback)          # Bot 4.8.4 : on_callback filtre les doubles appuis
    heads = set(re.findall(r'head == "(\w+)"', src)) | {h for g in re.findall(r'head in \(([^)]*)\)', src) for h in re.findall(r'"(\w+)"', g)}
    assert heads and all(re.match(botapp.CB_PATTERN, h + ":x") for h in heads), heads


def test_le_handler_des_boutons_utilise_cb_pattern():
    import inspect
    assert "CallbackQueryHandler(h_cb, pattern=CB_PATTERN)" in inspect.getsource(botapp.build_application)
