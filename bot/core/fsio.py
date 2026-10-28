"""Entrées/sorties fichiers tolérantes : UTF-8 sans BOM, écriture atomique, lecture JSON tolérante."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def atomic_write(path: Path, text: str) -> None:
    """Écrit `text` en UTF-8 sans BOM via un fichier .tmp puis os.replace (atomique)."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    # os.replace peut échouer brièvement si le lecteur (l'EA) tient le fichier : on réessaie
    for i in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05 * (i + 1))
    os.replace(tmp, path)


def read_text(path: Path) -> str | None:
    """Lit un fichier texte (tolère un BOM éventuel). None si absent/verrouillé."""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return f.read()
    except (FileNotFoundError, PermissionError, OSError, UnicodeDecodeError):
        return None


def read_json(path: Path) -> dict[str, Any] | None:
    """Lit un objet JSON ; fichier absent, partiel ou corrompu → None (on réessaiera)."""
    raw = read_text(path)
    if raw is None or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def safe_remove(path: Path) -> bool:
    """Supprime un fichier sans lever d'exception. True si supprimé ou absent."""
    try:
        Path(path).unlink()
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False
