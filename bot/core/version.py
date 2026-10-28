"""Versions à trois niveaux (majeur.mineur.correctif) : bot (version.txt du bot) et EA (indépendantes)."""
from __future__ import annotations

from pathlib import Path

BOT_ROOT = Path(__file__).resolve().parent.parent


def read(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8-sig").strip()
    except OSError:
        return ""


def bot_version(root: Path | None = None) -> str:
    return read((root or BOT_ROOT) / "version.txt") or "?"
