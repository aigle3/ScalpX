"""Notifications : séparées du tableau de bord, regroupées par fenêtre, avec niveau de priorité.

prio 0 : info (toujours sans son) · prio 1 : important (sans son en mode silencieux) · prio 2 : critique (toujours avec son).
"""
from __future__ import annotations

import logging

from core.service import Notice
from .render import esc, join_notices
from .ui import Sender

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, sender: Sender, chat_id: int, silent_mode: bool = False) -> None:
        self.sender, self.chat_id = sender, chat_id
        self.silent_mode = silent_mode
        self._pending: list[Notice] = []

    async def emit(self, notice: Notice) -> None:
        """Met la notification en file ; elle partira au prochain flush (regroupement)."""
        self._pending.append(notice)

    @staticmethod
    def format(n: Notice) -> str:
        return f"<b>{esc(n.label)}</b>\n{esc(n.text)}"

    def _silent(self, batch: list[Notice]) -> bool:
        if any(n.prio >= 2 for n in batch):
            return False
        if self.silent_mode:
            return True
        return not any(n.prio >= 1 for n in batch)

    async def flush(self) -> int:
        """Envoie les notifications en attente : texte regroupé (≤ 3800 car./message) + photos à part."""
        if not self._pending:
            return 0
        batch, self._pending = self._pending, []
        texts: list[Notice] = []
        seen: set[tuple[str, str]] = set()
        photos: list[Notice] = []
        for n in batch:
            key = (n.iid, n.text)
            if n.photo is not None:
                photos.append(n)
            elif key not in seen:
                seen.add(key)
                texts.append(n)
        sent = 0
        if texts:
            silent = self._silent(texts)
            for chunk in join_notices(self.format(n) for n in texts):
                try:
                    await self.sender.send(self.chat_id, chunk, None, silent)
                    sent += 1
                except Exception as exc:
                    log.error("envoi de notification impossible : %s", exc)
        for n in photos:
            try:
                await self.sender.photo(self.chat_id, str(n.photo), caption=f"{n.label}\n{n.text}"[:1000],
                                        silent=self._silent([n]))
                sent += 1
            except Exception as exc:
                log.error("envoi de photo impossible : %s", exc)
        return sent
