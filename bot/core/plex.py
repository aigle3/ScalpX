"""Bot 3.8.0 : serveur Plex du VPS allumé / éteint depuis Telegram (🛠 Système → 🎬 Plex).

Plex est déjà installé et configuré ; le bot ne fait que lancer « Plex Media Server.exe » (priorité basse par défaut :
les terminaux MT5 passent avant) et arrêter tous ses processus (serveur, transcodeur, scripts, tuner).
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SERVER_EXE = "Plex Media Server.exe"
CANDIDATES = (r"C:\Program Files\Plex\Plex Media Server\Plex Media Server.exe",
              r"C:\Program Files (x86)\Plex\Plex Media Server\Plex Media Server.exe")


def _is_plex(name: str) -> bool:
    n = (name or "").lower()
    return n.startswith("plex") and n.endswith(".exe") and "update" not in n   # « Plex Update Service » : service Windows


def find_exe(configured: str = "") -> Path | None:
    """Chemin du serveur : celui de config.toml, sinon les emplacements d'installation habituels, sinon le processus lancé."""
    for c in ([configured] if configured else []) + list(CANDIDATES):
        if c and Path(c).is_file():
            return Path(c)
    for p in _procs():
        try:
            if p.name().lower() == SERVER_EXE.lower():
                return Path(p.exe())
        except Exception:
            continue
    return None


_CACHE: dict = {}                          # pid → Process gardé : cpu_percent() mesure depuis l'appel précédent


def _procs():
    import psutil
    out = []
    for p in psutil.process_iter(["name"]):
        try:
            if _is_plex(p.info.get("name") or ""):
                out.append(_CACHE.setdefault(p.pid, p))
        except Exception:
            continue
    for pid in [k for k in _CACHE if all(k != q.pid for q in out)]:
        _CACHE.pop(pid, None)
    return out


@dataclass
class PlexState:
    running: bool
    procs: int = 0
    cpu: float = 0.0                       # % du CPU total du VPS
    ram_mb: float = 0.0
    since: float = 0.0                     # démarrage du serveur (epoch)
    exe: str = ""


def state(configured: str = "") -> PlexState:
    import psutil
    ps = _procs()
    exe = find_exe(configured)
    if not ps:
        return PlexState(False, exe=str(exe or ""))
    cpu = ram = 0.0
    since = 0.0
    ncpu = psutil.cpu_count() or 1
    for p in ps:
        try:
            cpu += p.cpu_percent(interval=None) / ncpu
            ram += p.memory_info().rss / 1_048_576
            if p.name().lower() == SERVER_EXE.lower():
                since = p.create_time()
        except Exception:
            continue
    return PlexState(True, len(ps), cpu, ram, since, str(exe or ""))


def start(configured: str = "", low_priority: bool = True) -> str:
    """Lance le serveur. Renvoie un texte pour Telegram."""
    if any(p.name().lower() == SERVER_EXE.lower() for p in _procs()):
        return "déjà en marche"
    exe = find_exe(configured)
    if exe is None:
        return "❌ Plex Media Server.exe introuvable (indique le chemin dans config.toml, section [plex], exe = …)"
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    if low_priority:
        flags |= getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    subprocess.Popen([str(exe)], cwd=str(exe.parent), creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log.info("Plex : serveur lancé (%s)%s", exe, ", priorité basse" if low_priority else "")
    return "lancé" + (" (priorité basse : les terminaux MT5 passent avant)" if low_priority else "")


def stop(wait_s: float = 15.0) -> str:
    """Arrête tous les processus Plex (fermeture demandée, puis forcée après wait_s)."""
    import psutil
    ps = _procs()
    if not ps:
        return "déjà arrêté"
    for p in ps:
        try:
            p.terminate()
        except Exception:
            pass
    gone, alive = psutil.wait_procs(ps, timeout=wait_s)
    for p in alive:
        try:
            p.kill()
        except Exception:
            pass
    if alive:
        psutil.wait_procs(alive, timeout=5)
    left = _procs()
    log.info("Plex : arrêté (%d processus, %d forcé(s), %d restant(s))", len(ps), len(alive), len(left))
    return "arrêté" if not left else f"⚠️ {len(left)} processus Plex encore présents"


def status_text(st: PlexState, now: float | None = None) -> str:
    now = now or time.time()
    if not st.running:
        return "🎬 <b>Plex</b> : ⚫ arrêté"
    up = int(now - st.since) if st.since else 0
    dur = f"{up // 3600} h {up % 3600 // 60:02d}" if up >= 3600 else f"{up // 60} min"
    return f"🎬 <b>Plex</b> : 🟢 en marche depuis {dur} · CPU {st.cpu:.0f} % · RAM {st.ram_mb:.0f} Mo ({st.procs} processus)"
