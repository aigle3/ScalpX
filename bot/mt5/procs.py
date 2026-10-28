"""Gestion des processus terminal64.exe. Tous s'appellent pareil : on identifie par le CHEMIN de l'exécutable."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Protocol

import psutil

log = logging.getLogger(__name__)


def norm_path(p: str | Path) -> str:
    """Chemin normalisé pour comparaison (casse et séparateurs Windows)."""
    return os.path.normcase(os.path.abspath(str(p)))


class ProcessAPI(Protocol):
    def is_running(self, exe: Path) -> bool: ...
    def launch(self, exe: Path, ini: Path | None = None) -> int: ...
    def kill(self, exe: Path) -> int: ...


class PsutilProcesses:
    """Implémentation réelle (psutil + subprocess)."""

    def find(self, exe: Path) -> list[psutil.Process]:
        target = norm_path(exe)
        found: list[psutil.Process] = []
        for p in psutil.process_iter(["exe", "name"]):
            try:
                pe = p.info.get("exe")
                if pe and norm_path(pe) == target:
                    found.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return found

    def is_running(self, exe: Path) -> bool:
        return bool(self.find(exe))

    def launch(self, exe: Path, ini: Path | None = None) -> int:
        """Lance `terminal64.exe /portable [/config:ini]` détaché, dans le dossier de l'instance."""
        args = [str(exe), "/portable"]
        if ini is not None:
            args.append(f"/config:{ini}")
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        proc = subprocess.Popen(args, cwd=str(Path(exe).parent), creationflags=flags,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                close_fds=True)
        log.info("terminal lancé : %s (pid %s)%s", Path(exe).parent.name, proc.pid, " avec /config" if ini else "")
        return proc.pid

    def kill(self, exe: Path) -> int:
        """Termine tous les processus de CET exécutable ; renvoie le nombre tué."""
        n = 0
        procs = self.find(exe)
        for p in procs:
            try:
                p.kill()
                n += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        psutil.wait_procs(procs, timeout=10)
        return n


def vps_load(path: Path) -> dict[str, float]:
    """Charge du VPS : CPU %, RAM %, disque % (pour l'en-tête du tableau de bord)."""
    try:
        disk = psutil.disk_usage(str(path)).percent
    except OSError:
        disk = 0.0
    return {"cpu": psutil.cpu_percent(interval=None), "ram": psutil.virtual_memory().percent, "disk": disk}


def uptime_s() -> float:
    return time.time() - psutil.boot_time()
