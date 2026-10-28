"""Lecture des événements events_<id>.jsonl : RENOMMER → lire la copie → supprimer.

L'EA ajoute des lignes en continu ; en renommant d'abord, on lit un fichier que l'EA ne touche plus
(il recrée events_<id>.jsonl à l'événement suivant). Si le fichier est verrouillé : nouvelle tentative.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from pydantic import ValidationError

from .fsio import read_text
from .models import Evenement

log = logging.getLogger(__name__)


def parse_lines(text: str) -> list[Evenement]:
    """Parse tolérant : une ligne corrompue est ignorée, les autres sont conservées."""
    out: list[Evenement] = []
    for line in text.splitlines():
        line = line.strip().lstrip("﻿")
        if not line:
            continue
        try:
            out.append(Evenement.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValidationError):
            log.warning("ligne d'événement illisible ignorée")
    return out


def _read_bytes(p: Path) -> bytes | None:
    try:
        return Path(p).read_bytes()
    except OSError:
        return None


class EventReader:
    @staticmethod
    def _read_stable(p: Path, settle: float = 0.02) -> str | None:
        """Lit le fichier renommé en vérifiant qu'il ne grossit plus (une écriture de l'EA déjà en cours
        au moment du renommage peut encore y arriver) : on relit tant que la taille change."""
        data = _read_bytes(p)
        if data is None:
            return None
        for _ in range(5):
            time.sleep(settle)
            again = _read_bytes(p)
            if again is None or len(again) == len(data):
                break
            data = again
        return data.decode("utf-8-sig", errors="replace")

    def __init__(self, common: Path) -> None:
        self.common = Path(common)

    def _leftovers(self, iid: str) -> list[Path]:
        """Copies .proc laissées par un crash du bot (à relire avant le fichier courant)."""
        return sorted(self.common.glob(f"events_{iid}.jsonl.*.proc"))

    def drain(self, iid: str, retries: int = 5, delay: float = 0.1) -> list[Evenement]:
        """Renvoie les nouveaux événements de l'instance et supprime le fichier traité."""
        events: list[Evenement] = []
        for p in self._leftovers(iid):
            txt = read_text(p)
            if txt is not None:
                events += parse_lines(txt)
                try:
                    p.unlink()
                except OSError:
                    pass
        src = self.common / f"events_{iid}.jsonl"
        if not src.exists():
            return events
        dst = self.common / f"events_{iid}.jsonl.{time.time_ns()}.proc"
        for i in range(retries):
            try:
                os.replace(src, dst)
                break
            except FileNotFoundError:
                return events
            except PermissionError:
                time.sleep(delay * (i + 1))
        else:
            return events                    # verrouillé : on réessaiera au prochain cycle
        txt = self._read_stable(dst)
        if txt is not None:
            events += parse_lines(txt)
            try:
                dst.unlink()
            except OSError:
                pass
        events.sort(key=lambda e: (e.ts, e.seq))
        return events
