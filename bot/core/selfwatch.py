"""Surveillance du bot lui-même (Windows) : tâche « ScalpX Watchdog » toutes les 2 min (watchdog.ps1).

- Bot vivant (son verrou 127.0.0.1:<lock_port> répond) : rien.
- Redémarrage NORMAL en cours (mise à jour, bouton Redémarrer) : le bot écrit data\\restarting juste avant ; la surveillance
  n'intervient pas pendant 5 min (le redémarrage se fait seul, sans elle).
- Bot absent deux passages de suite (≈ 4 min) : relance de la tâche « ScalpX Bot » ; tâche bloquée : arrêt puis relance.
Tout est noté dans data\\logs\\watchdog.log.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)
TASK = "ScalpX Watchdog"
BOT_TASK = "ScalpX Bot"
EVERY_MIN = 2
# lanceur sans fenêtre : powershell seul afficherait une console noire toutes les 2 min
VBS = ('Set sh = CreateObject("WScript.Shell")\r\n'
       'sh.Run "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{ps1}""", 0, True\r\n')


def marker(bot_root: Path) -> Path:
    return Path(bot_root) / "data" / "restarting"


def mark_restarting(bot_root: Path) -> None:
    """Juste avant un redémarrage voulu : « horodatage pid ». La surveillance attend la fin de CE processus puis relance
    le bot elle-même, tout de suite (sans attendre ses deux passages)."""
    try:
        m = marker(bot_root)
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(f"{int(time.time())} {os.getpid()}", encoding="utf-8")
    except OSError as exc:
        log.warning("marqueur de redémarrage non écrit : %s", exc)


def clear_restarting(bot_root: Path) -> None:
    try:
        marker(bot_root).unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("marqueur de redémarrage non effacé : %s", exc)


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=30,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def kick(runner=None) -> bool:
    """Lance la tâche de surveillance tout de suite (redémarrage voulu). Elle tourne dans SA tâche planifiée : rien ne
    peut l'interrompre quand l'ancien bot s'arrête (un processus lancé par le bot lui-même peut être tué avec lui)."""
    if os.name != "nt" and runner is None:
        return False
    run = runner or _run
    try:
        if run(["schtasks", "/query", "/tn", TASK]).returncode != 0:
            return False
        return run(["schtasks", "/run", "/tn", TASK]).returncode == 0
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("surveillance non lancée : %s", exc)
        return False


def ensure_watchdog(bot_root: Path, runner=None) -> str:
    """Crée (ou met à jour) la tâche de surveillance. Seulement sous Windows et si la tâche « ScalpX Bot » existe."""
    if os.name != "nt" and runner is None:
        return "hors Windows"
    run = runner or _run
    bot_root = Path(bot_root)
    ps1 = bot_root / "watchdog.ps1"
    if not ps1.is_file():
        return "watchdog.ps1 absent"
    if run(["schtasks", "/query", "/tn", BOT_TASK]).returncode != 0:
        return "pas de tâche « ScalpX Bot » (bot lancé à la main)"
    vbs = bot_root / "watchdog.vbs"
    content = VBS.format(ps1=str(ps1))
    try:
        if not vbs.is_file() or vbs.read_text(encoding="ascii", errors="replace") != content:
            vbs.write_bytes(content.encode("mbcs" if os.name == "nt" else "utf-8"))
    except OSError as exc:
        return f"watchdog.vbs non écrit : {exc}"
    r = run(["schtasks", "/create", "/f", "/it", "/sc", "minute", "/mo", str(EVERY_MIN), "/tn", TASK,
             "/tr", f'wscript.exe //B //Nologo "{vbs}"'])
    if r.returncode != 0:
        return f"tâche non créée : {(r.stderr or r.stdout or '').strip()[:200]}"
    return "ok"
