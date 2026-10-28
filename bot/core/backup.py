"""Sauvegarde quotidienne de l'historique (data\\scalpx.db) : soldes, cycles, retraits, déploiements."""
from __future__ import annotations

import os
import time
from pathlib import Path

from core.config import Config
from storage.db import Database


def backup_dir(cfg: Config) -> Path:
    if cfg.backup.dir:
        return Path(cfg.backup.dir)
    od = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer")
    if od and Path(od).is_dir():
        return Path(od) / "ScalpX" / "sauvegardes"                 # hors du VPS : survit à une panne disque
    return cfg.paths.data / "backups"


def run_backup(cfg: Config, db: Database, now: float | None = None) -> Path:
    """Copie datée de la base, puis ne garde que les cfg.backup.keep plus récentes."""
    d = backup_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"scalpx_{time.strftime('%Y-%m-%d_%H%M', time.localtime(now or time.time()))}.db"
    db.backup_to(dest)
    files = sorted(d.glob("scalpx_*.db"))
    for old in files[:-max(1, cfg.backup.keep)]:
        try:
            old.unlink()
        except OSError:
            pass
    return dest


def last_backup(cfg: Config) -> Path | None:
    files = sorted(backup_dir(cfg).glob("scalpx_*.db"))
    return files[-1] if files else None
