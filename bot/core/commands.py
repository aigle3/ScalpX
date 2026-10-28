"""File de commandes vers l'EA : un fichier cmd_<id>_<n>.json par commande, ack_<id>_<n>.json en retour."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from .fsio import atomic_write, read_json, safe_remove
from .models import Ack

log = logging.getLogger(__name__)


@dataclass
class CmdResult:
    """Résultat d'une commande : ok, message de l'EA, ou timeout."""
    ok: bool
    message: str
    timeout: bool = False
    n: int = 0


class CommandQueue:
    def __init__(self, common: Path) -> None:
        self.common = Path(common)
        self._last_n = 0

    def _next_n(self) -> int:
        """Numéro croissant (horodatage en ms) : l'ordre est conservé même après un redémarrage du bot."""
        n = max(self._last_n + 1, int(time.time() * 1000))
        self._last_n = n
        return n

    def cmd_path(self, iid: str, n: int) -> Path:
        return self.common / f"cmd_{iid}_{n}.json"

    def ack_path(self, iid: str, n: int) -> Path:
        return self.common / f"ack_{iid}_{n}.json"

    def send(self, iid: str, cmd: str, args: list[str] | None = None) -> int:
        """Écrit la commande (écriture atomique) et renvoie son numéro."""
        n = self._next_n()
        payload = {"id": iid, "cmd": cmd.upper(), "args": [str(a) for a in (args or [])], "ts": int(time.time())}
        atomic_write(self.cmd_path(iid, n), json.dumps(payload, ensure_ascii=False))
        return n

    def read_ack(self, iid: str, n: int) -> Ack | None:
        """Lit (et supprime) l'accusé de réception s'il existe."""
        p = self.ack_path(iid, n)
        data = read_json(p)
        if not data:
            return None
        try:
            ack = Ack.model_validate(data)
        except ValidationError:
            return None
        safe_remove(p)
        return ack

    async def send_and_wait(self, iid: str, cmd: str, args: list[str] | None = None,
                            timeout: float = 20.0, poll: float = 0.25) -> CmdResult:
        """Envoie une commande et attend son ack (⏳ → ✅/❌). Sur timeout, supprime la commande non lue."""
        n = self.send(iid, cmd, args)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ack = self.read_ack(iid, n)
            if ack is not None:
                return CmdResult(ack.ok, ack.message, False, n)
            await asyncio.sleep(poll)
        safe_remove(self.cmd_path(iid, n))      # l'EA ne l'a pas lue : on ne la laisse pas traîner
        return CmdResult(False, "Pas de réponse de l'EA (terminal arrêté ou occupé ?)", True, n)

    def cleanup_orphans(self, max_age_s: float = 600.0) -> int:
        """Supprime les cmd_/ack_ orphelins de plus de max_age_s (ack jamais réclamé, commande jamais lue)."""
        n, now = 0, time.time()
        for pat in ("cmd_*.json", "ack_*.json", "cmd_*.json.tmp", "ack_*.json.tmp"):
            for p in self.common.glob(pat):
                try:
                    if now - p.stat().st_mtime > max_age_s and safe_remove(p):
                        n += 1
                except OSError:
                    pass
        return n

    def purge(self, iid: str) -> None:
        """Supprime les fichiers cmd/ack/statut/events d'une instance archivée."""
        for pat in (f"cmd_{iid}_*.json", f"ack_{iid}_*.json", f"statut_{iid}.json",
                    f"events_{iid}.jsonl*", f"etat_{iid}.json"):
            for p in self.common.glob(pat):
                safe_remove(p)
