"""Notifications : séparées du tableau de bord, regroupées par fenêtre, avec niveau de priorité.

prio 0 : info (toujours sans son) · prio 1 : important (sans son en mode silencieux) · prio 2 : critique (toujours avec son).
"""
from __future__ import annotations

import logging
import time

from core.service import Notice
from .render import esc, join_notices
from .ui import Sender

log = logging.getLogger(__name__)
RETRY_FOR_S = 1800          # Telegram injoignable : un envoi raté est retenté à chaque passage pendant 30 min
RETRY_ON = ("NetworkError", "TimedOut", "RetryAfter")   # erreurs de transport (python-telegram-bot) : seules à être retentées


def transient(exc: Exception) -> bool:
    """Coupure réseau ou limite de débit : l'envoi peut réussir plus tard. Tout le reste (message refusé, fichier absent…)
    est abandonné et journalisé, comme avant."""
    return isinstance(exc, (ConnectionError, TimeoutError)) or any(c.__name__ in RETRY_ON for c in type(exc).__mro__)


class Notifier:
    def __init__(self, sender: Sender, chat_id: int, silent_mode: bool = False) -> None:
        self.sender, self.chat_id = sender, chat_id
        self.silent_mode = silent_mode
        self._pending: list[Notice] = []
        self._retry: list[tuple[float, str, object, bool]] = []      # (premier échec, "text"/"photo", contenu, silencieux)

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

    def _keep(self, first: float, kind: str, payload, silent: bool, exc: Exception) -> None:
        """Envoi raté : gardé pour le prochain passage (coupure réseau), sauf erreur définitive ou délai dépassé."""
        if not transient(exc):
            return
        if time.time() - first > RETRY_FOR_S:
            log.error("notification abandonnée après %d min d'échecs d'envoi", RETRY_FOR_S // 60)
            return
        self._retry.append((first, kind, payload, silent))

    async def _send_retries(self) -> int:
        """Renvoie d'abord ce qui n'a pas pu partir (ordre d'origine conservé)."""
        todo, self._retry = self._retry, []
        sent = 0
        for i, (first, kind, payload, silent) in enumerate(todo):
            try:
                if kind == "text":
                    await self.sender.send(self.chat_id, payload, None, silent)
                else:
                    n = payload
                    await self.sender.photo(self.chat_id, str(n.photo), caption=f"{n.label}\n{n.text}"[:1000], silent=silent)
                sent += 1
            except Exception as exc:
                log.warning("nouvel envoi de notification impossible : %s", exc)
                self._keep(first, kind, payload, silent, exc)
                self._retry.extend(todo[i + 1:])                 # Telegram toujours injoignable : on garde le reste tel quel
                break
        if sent:
            log.info("%d notification(s) en retard envoyée(s)", sent)
        return sent

    async def flush(self) -> int:
        """Envoie les notifications en attente : texte regroupé (≤ 3800 car./message) + photos à part.
        Un envoi raté (réseau) est retenté aux passages suivants au lieu d'être perdu."""
        sent = await self._send_retries() if self._retry else 0
        if not self._pending:
            return sent
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
        items: list[tuple[str, object, bool]] = []
        if texts:
            silent = self._silent(texts)
            items += [("text", chunk, silent) for chunk in join_notices(self.format(n) for n in texts)]
        items += [("photo", n, self._silent([n])) for n in photos]
        now = time.time()
        if self._retry:                                  # Telegram encore injoignable : à la suite, l'ordre est gardé
            self._retry += [(now, k, x, sl) for k, x, sl in items]
            return sent
        for i, (kind, payload, silent) in enumerate(items):
            try:
                if kind == "text":
                    await self.sender.send(self.chat_id, payload, None, silent)
                else:
                    n = payload
                    await self.sender.photo(self.chat_id, str(n.photo), caption=f"{n.label}\n{n.text}"[:1000], silent=silent)
                sent += 1
            except Exception as exc:
                log.error("envoi de %s impossible : %s", "notification" if kind == "text" else "photo", exc)
                self._keep(now, kind, payload, silent, exc)
                if self._retry:                          # coupure : le reste attend derrière, dans l'ordre
                    self._retry += [(now, k, x, sl) for k, x, sl in items[i + 1:]]
                    break
        return sent
