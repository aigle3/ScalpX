"""Lecture des fichiers statut_<id>.json écrits par l'EA (heartbeat toutes les 5 s).

Bot 3.10.0 : le dernier statut lu est gardé en mémoire ; tant que le fichier n'a pas changé (même identifiant, date et
taille), il n'est ni relu ni revalidé (le tableau de bord et les tâches le demandent bien plus souvent que l'EA ne l'écrit).
"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import ValidationError

from .fsio import read_json
from .models import Statut


class StatusReader:
    def __init__(self, common: Path) -> None:
        self.common = Path(common)
        self._cache: dict[str, tuple[tuple[int, int, int], Statut]] = {}

    def path(self, iid: str) -> Path:
        return self.common / f"statut_{iid}.json"

    def read(self, iid: str) -> Statut | None:
        """Statut courant ; None si absent, partiel ou corrompu (on réessaiera au cycle suivant)."""
        p = self.path(iid)
        try:
            s = os.stat(p)
            key = (s.st_ino, s.st_mtime_ns, s.st_size)
        except OSError:
            self._cache.pop(iid, None)
            return None
        hit = self._cache.get(iid)
        if hit is not None and hit[0] == key:
            return hit[1]
        data = read_json(p)
        if not data:
            return None
        try:
            st = Statut.model_validate(data)
        except ValidationError:
            return None
        self._cache[iid] = (key, st)
        return st

    def remove(self, iid: str) -> None:
        """Efface un statut périmé (avant un lancement, pour ne pas lire un ancien heartbeat)."""
        self._cache.pop(iid, None)
        try:
            self.path(iid).unlink()
        except OSError:
            pass
