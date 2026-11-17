"""Bot 3.18.0 : 🖥 gestion du VPS Ubuntu depuis Telegram (Linux seulement).

Lecture (sans droits) : CPU, RAM, disque, charge, température, processus les plus gourmands, démarrage auto.
Actions (droits administrateur LIMITÉS, sans mot de passe) : l'installeur Linux écrit /etc/sudoers.d/scalpx qui autorise
l'utilisateur du bot à lancer SEULEMENT : réactiver les deux services ScalpX au démarrage, `systemctl reboot`, la mise à
jour root de l'installation et la désinstallation. Aucun mot de passe ne passe par Telegram.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import PurePosixPath

SYSTEMCTL = "/usr/bin/systemctl"
UNINSTALL = "/usr/local/sbin/scalpx-uninstall"
ROOT_UPDATE = "/usr/local/sbin/scalpx-root-update"
OWN = ("scalpx-bot.service", "scalpx-xvfb.service")


def _run(args: list[str], timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)



# ---------------------------------------------------------------- état du serveur
@dataclass
class Proc:
    label: str
    cpu: float
    mem_mb: float


@dataclass
class Stats:
    cpu: float
    ncpu: int
    load: tuple[float, float, float]
    ram_pct: float
    ram_used_gb: float
    ram_total_gb: float
    swap_pct: float
    disk_pct: float
    disk_free_gb: float
    uptime_s: float
    temp: float | None
    top_cpu: list[Proc]
    top_mem: list[Proc]


def proc_label(name: str, cmdline: list[str]) -> str:
    """Nom lisible : sous Wine les processus s'appellent « main » ou « CrBrowserMain » ; on montre l'exécutable Windows
    et le compte (dossier de l'instance) : « terminal64 · 52039799_RoboForex_Pro », « WebView2 · 52039799_… »."""
    joined = " ".join(cmdline or [])
    m = re.search(r"([^\\/\s\"]+)\.exe\b", (cmdline or [""])[0]) if cmdline else None
    exe = m.group(1) if m else ""
    acct = re.search(r"instances[\\/]+([^\\/\s\"]+)", joined)
    if "msedgewebview2" in joined.lower():
        exe = "WebView2"
    if exe:
        return exe + (f" · {acct.group(1)}" if acct else "")
    if cmdline and cmdline[0].endswith(("python", "python3")) or "main.py" in joined:
        return "bot Python" if "main.py" in joined else (name or "python")
    return name or (PurePosixPath(cmdline[0]).name if cmdline else "?")


def stats(sample_s: float = 1.0, top: int = 6) -> Stats:
    import psutil
    procs = []
    for p in psutil.process_iter(["name", "cmdline", "memory_info"]):
        try:
            p.cpu_percent(None)
            procs.append(p)
        except psutil.Error:
            pass
    cpu = psutil.cpu_percent(interval=sample_s)
    rows: list[Proc] = []
    for p in procs:
        try:
            mi = p.info.get("memory_info")
            rows.append(Proc(proc_label(p.info.get("name") or "", p.info.get("cmdline") or []), p.cpu_percent(None),
                             (mi.rss if mi else 0) / 2**20))
        except psutil.Error:
            pass
    vm, sw, du = psutil.virtual_memory(), psutil.swap_memory(), psutil.disk_usage("/")
    temp = None
    try:
        vals = [t.current for ts in (psutil.sensors_temperatures() or {}).values() for t in ts if t.current]
        temp = max(vals) if vals else None
    except (AttributeError, OSError):
        pass
    load = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
    return Stats(cpu, psutil.cpu_count() or 1, load, vm.percent, vm.used / 2**30, vm.total / 2**30, sw.percent, du.percent,
                 du.free / 2**30, time.time() - psutil.boot_time(), temp,
                 sorted(rows, key=lambda r: -r.cpu)[:top], sorted(rows, key=lambda r: -r.mem_mb)[:top])


# ---------------------------------------------------------------- démarrage automatique des services ScalpX
def enable_own(run=_run) -> tuple[bool, str]:
    """Bot et écran virtuel relancés au démarrage du VPS (sudo limité à ces deux commandes exactes)."""
    errs = []
    for unit in OWN:
        r = run(["sudo", "-n", SYSTEMCTL, "enable", "--now", unit], timeout=60)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            errs.append("droits administrateur absents : relance linux/install.sh" if "password" in err else f"{unit} : {err[-120:]}")
    return not errs, "; ".join(errs) or "fait"


def autostart(run=_run) -> dict[str, str]:
    """État au démarrage du VPS des services ScalpX (enabled = relancés automatiquement)."""
    r = run([SYSTEMCTL, "is-enabled", *OWN])
    vals = (r.stdout or "").split()
    return {u: (vals[i] if i < len(vals) else "?") for i, u in enumerate(OWN)}


def sudo_ready(run=_run) -> bool:
    """Les autorisations de l'installeur sont-elles en place ? (test sans rien exécuter)"""
    try:
        return run(["sudo", "-n", "-l", SYSTEMCTL, "reboot"], timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def reboot(run=_run) -> tuple[bool, str]:
    r = run(["sudo", "-n", SYSTEMCTL, "reboot"], timeout=30)
    return r.returncode == 0, (r.stderr or "").strip()[-200:]


def uninstall(run=_run) -> tuple[bool, str]:
    """Lance la désinstallation complète (script root, détaché par systemd-run : il survit à l'arrêt du bot)."""
    r = run(["sudo", "-n", UNINSTALL], timeout=30)
    return r.returncode == 0, (r.stderr or r.stdout or "").strip()[-200:]


def root_update(run=_run) -> tuple[bool, str]:
    """🔧 Réinstallation (droits administrateur) depuis la branche stable : le script root télécharge lui-même le paquet,
    relance install.sh --auto (aucune question, réglages et comptes gardés) et redémarre le bot."""
    r = run(["sudo", "-n", ROOT_UPDATE], timeout=30)
    return r.returncode == 0, (r.stderr or r.stdout or "").strip()[-200:]
