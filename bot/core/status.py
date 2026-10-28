"""Lecture des fichiers statut_<id>.json écrits par l'EA (heartbeat toutes les 5 s)."""
from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from .fsio import read_json
from .models import Statut


class StatusReader:
    def __init__(self, common: Path) -> None:
        self.common = Path(common)

    def path(self, iid: str) -> Path:
        return self.common / f"statut_{iid}.json"

    def read(self, iid: str) -> Statut | None:
        """Statut courant ; None si absent, partiel ou corrompu (on réessaiera au cycle suivant)."""
        data = read_json(self.path(iid))
        if not data:
            return None
        try:
            return Statut.model_validate(data)
        except ValidationError:
            return None

    def remove(self, iid: str) -> None:
        """Efface un statut périmé (avant un lancement, pour ne pas lire un ancien heartbeat)."""
        try:
            self.path(iid).unlink()
        except OSError:
            pass
