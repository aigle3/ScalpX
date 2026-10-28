"""Abstractions d'interface : claviers neutres (sans dépendance Telegram) et encodage des callbacks.

callback_data (≤ 64 octets) : <espace>:<verbe>[:<arg>…]. Les comptes y sont désignés par un code court
dérivé de l'identifiant (jamais d'identifiant ni de chemin libre reçu depuis Telegram).
"""
from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from storage.db import Database, Instance

Keyboard = list[list[tuple[str, str]]]            # lignes de (libellé, callback_data)


ALL_ID = "*"                                       # « tous les comptes » (lancés) dans les écrans partagés


def short(iid: str) -> str:
    """Code court (8 hex) d'un identifiant : sûr à placer dans un callback_data. « all » pour tous les comptes."""
    return "all" if iid == ALL_ID else hashlib.sha1(iid.encode()).hexdigest()[:8]


@dataclass
class Group:
    """Cible « tous les comptes » : les commandes partent vers chaque membre ; les lectures se font sur le premier."""
    members: list[Instance]
    id: str = ALL_ID

    @property
    def label(self) -> str:
        return f"Tous les comptes ({len(self.members)})"

    @property
    def ref(self) -> Instance:
        return self.members[0]


def resolve(db: Database, code: str) -> Instance | None:
    """Retrouve une instance DU REGISTRE à partir de son code court."""
    for inst in db.list():
        if short(inst.id) == code:
            return inst
    return None


class EditResult:
    OK, SAME, GONE, RETRY = "ok", "same", "gone", "retry"


class Sender(Protocol):
    """Ce dont la logique a besoin de Telegram (implémenté par TelegramSender, simulé en test)."""
    async def send(self, chat_id: int, text: str, kb: Keyboard | None = None, silent: bool = False) -> int: ...
    async def edit(self, chat_id: int, message_id: int, text: str, kb: Keyboard | None = None) -> str: ...
    async def delete(self, chat_id: int, message_id: int) -> bool: ...
    async def reply_menu(self, chat_id: int, text: str) -> int: ...
    async def photo(self, chat_id: int, data: bytes | str, caption: str = "", silent: bool = False) -> None: ...
    async def document(self, chat_id: int, data: bytes, filename: str, caption: str = "") -> None: ...


@dataclass
class Pending:
    """Action en attente de confirmation (arrêt, all, suppression, création, reconnexion, déploiement)."""
    text: str
    run: Callable[[], Awaitable[Any]]
    back: str = "dash"
    expires: float = field(default_factory=lambda: time.time() + 120)


class Confirmations:
    def __init__(self) -> None:
        self._items: dict[str, Pending] = {}

    def ask(self, p: Pending) -> str:
        self._purge()
        tok = secrets.token_hex(4)
        self._items[tok] = p
        return tok

    def take(self, tok: str) -> Pending | None:
        self._purge()
        return self._items.pop(tok, None)

    def peek(self, tok: str) -> Pending | None:
        self._purge()
        return self._items.get(tok)

    def _purge(self) -> None:
        now = time.time()
        for k in [k for k, v in self._items.items() if v.expires < now]:
            self._items.pop(k, None)


def confirm_kb(tok: str, yes: str = "✅ Confirmer") -> Keyboard:
    return [[(yes, f"ok:{tok}"), ("✖ Annuler", f"no:{tok}")]]
