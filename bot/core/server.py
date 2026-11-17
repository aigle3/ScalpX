"""Bot 3.18.0 : 🖥 gestion du VPS Ubuntu depuis Telegram (Linux seulement).

Lecture (sans droits) : CPU, RAM, disque, charge, température, processus les plus gourmands, services, démarrage auto.
Actions (droits administrateur LIMITÉS, sans mot de passe) : l'installeur Linux écrit /etc/sudoers.d/scalpx qui autorise
l'utilisateur du bot à lancer SEULEMENT `systemctl start|stop|restart|enable|disable <service>`, `systemctl reboot` et le
script de désinstallation. Aucun mot de passe ne passe par Telegram. Services vitaux protégés (SSH, réseau, bot…).
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
ACTIONS = ("start", "stop", "restart", "enable", "disable")
_RE_UNIT = re.compile(r"^[A-Za-z0-9@._:-]{1,120}\.service$")
# jamais arrêtés / désactivés depuis Telegram : on perdrait l'accès au VPS ou le bot lui-même
_PROTECTED = ("ssh", "sshd", "dbus", "systemd-", "networkd", "NetworkManager", "network", "resolved", "user@", "getty",
              "polkit", "cloud-init", "netplan", "tailscaled", "wpa_supplicant") + tuple(u.removesuffix(".service") for u in OWN)


def _run(args: list[str], timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)


def protected(unit: str) -> bool:
    name = unit.removesuffix(".service")
    return any(name == p or name.startswith(p) for p in _PROTECTED)


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


# ---------------------------------------------------------------- services
@dataclass
class Service:
    unit: str
    active: str           # active / failed / inactive
    sub: str              # running / exited / dead…
    enabled: str          # enabled / disabled / static…
    mem_mb: float
    desc: str


def services(run=_run) -> list[Service]:
    """Services chargés en cours d'exécution ou en échec, triés par mémoire (les plus gourmands d'abord)."""
    r = run([SYSTEMCTL, "list-units", "--type=service", "--all", "--no-legend", "--plain", "--no-pager"])
    rows = []
    for line in (r.stdout or "").splitlines():
        parts = line.split(None, 4)
        if len(parts) >= 4 and parts[1] == "loaded" and (parts[3] == "running" or parts[2] == "failed"):
            rows.append(parts + [""] * (5 - len(parts)))
    if not rows:
        return []
    r = run([SYSTEMCTL, "show", *[p[0] for p in rows], "-p", "Id", "-p", "MemoryCurrent", "-p", "UnitFileState", "--no-pager"])
    info: dict[str, dict[str, str]] = {}
    cur: dict[str, str] = {}
    for line in (r.stdout or "").splitlines() + [""]:
        if not line.strip():
            if cur.get("Id"):
                info[cur["Id"]] = cur
            cur = {}
            continue
        k, _, v = line.partition("=")
        cur[k] = v
    out = []
    for unit, _, active, sub, desc in rows:
        i = info.get(unit, {})
        mem = i.get("MemoryCurrent", "")
        out.append(Service(unit, active, sub, i.get("UnitFileState", "?"),
                           int(mem) / 2**20 if mem.isdigit() and int(mem) < 2**60 else 0.0, desc.strip()))
    return sorted(out, key=lambda s: (s.active != "failed", -s.mem_mb))


def action(unit: str, act: str, run=_run) -> tuple[bool, str]:
    """systemctl <act> <unit> en administrateur (sudo sans mot de passe, limité par /etc/sudoers.d/scalpx)."""
    if act not in ACTIONS or not _RE_UNIT.match(unit):
        return False, "action ou service invalide"
    if act in ("stop", "disable", "restart") and protected(unit):
        return False, "service vital protégé (accès au VPS ou bot)"
    args = ["sudo", "-n", SYSTEMCTL, act] + (["--now"] if act in ("enable", "disable") else []) + [unit]
    r = run(args, timeout=60)
    if r.returncode == 0:
        return True, "fait"
    err = (r.stderr or r.stdout or "").strip()
    if "password is required" in err or "a password" in err or "not allowed" in err:
        return False, "droits administrateur non installés : relance linux/install.sh"
    return False, err[-200:] or f"code {r.returncode}"


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
