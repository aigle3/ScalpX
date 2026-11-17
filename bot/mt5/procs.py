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
FAST_NAMES = os.name == "nt"        # ailleurs (Linux) le nom peut être tronqué : chemin lu pour chaque processus
COMM_LEN = 15                       # Linux : nom de processus tronqué à 15 caractères (metatester64.exe → metatester64.ex)


def norm_path(p: str | Path) -> str:
    """Chemin normalisé pour comparaison (casse et séparateurs Windows)."""
    return os.path.normcase(os.path.abspath(str(p)))


class ProcessAPI(Protocol):
    def is_running(self, exe: Path) -> bool: ...
    def launch(self, exe: Path, ini: Path | None = None) -> int: ...
    def kill(self, exe: Path) -> int: ...


class PsutilProcesses:
    """Implémentation réelle (psutil + subprocess)."""

    CACHE_S = 2.0                     # le tableau de bord interroge chaque compte : un seul parcours des processus

    def __init__(self, window_opts=None, win_api=None, wine=None) -> None:
        self._running: set[str] = set()
        self._running_at = 0.0
        self._names: set[str] = set()                 # noms d'exécutables suivis (terminal64.exe)
        self._pid_exe: dict[tuple[int, str], str | None] = {}   # Bot 3.10.0 : (pid, nom) → chemin déjà lu (pas rouvert à chaque parcours)
        self.window_opts = window_opts                # mt5.window.WindowOptions : rangement de la fenêtre à l'ouverture
        self.win_api = win_api
        self.wine = wine                              # Bot 3.15.0 : mt5.wine.WineOptions → terminaux lancés par Wine (Linux)

    def _exe_of(self, p: psutil.Process, names: set[str]) -> str | None:
        """Chemin de l'exécutable si le NOM du processus est suivi. Le nom est quasi gratuit sous Windows, alors que lire
        le chemin ouvre chaque processus : on ne le lit plus que pour les terminaux (même résultat, parcours bien plus rapide).
        Nom illisible : chemin lu comme avant.
        Wine (Linux) : seuls les processus nommés comme un exécutable suivi (nom tronqué) ou « wine… » sont examinés ;
        le chemin vient de la ligne de commande (chemin Windows reconverti), pas du chargeur Wine."""
        name = (p.info.get("name") or "").lower()
        if FAST_NAMES and name and name not in names:              # Windows : nom du processus = nom de l'exécutable
            return None
        if self.wine is not None:
            # Bot 3.15.2 : sous Wine le processus porte un nom quelconque (« main » pour terminal64.exe) : aucun tri
            # par nom, la ligne de commande de chaque processus est lue (une fois par processus, résultat mémorisé)
            from .wine import cmdline_exe
            try:
                cwd = None
                try:
                    cwd = p.cwd()
                except (psutil.AccessDenied, OSError):
                    pass
                found = cmdline_exe(p.cmdline(), self.wine.prefix_path(), cwd)
                if found is None and cwd and (name == "main" or name[:COMM_LEN] in {n[:COMM_LEN] for n in names}
                                              or name.startswith("wine")):   # Bot 3.15.1 : ligne de commande illisible → dossier courant
                    for n in names:                          # (le terminal est lancé dans le dossier de l'instance)
                        if (Path(cwd) / n).is_file():
                            return str(Path(cwd) / n)
                return found
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                return None
        try:
            return p.exe() or None
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            return None

    def _running_exes(self, name: str = "") -> set[str]:
        now = time.monotonic()
        if name and name not in self._names:
            self._names.add(name)
            self._running_at = 0.0                     # nouveau nom suivi : parcours complet tout de suite
        if now - self._running_at > self.CACHE_S:
            t0 = time.monotonic()
            exes: set[str] = set()
            seen: dict[tuple[int, str], str | None] = {}
            for p in psutil.process_iter(["name"]):
                try:
                    key = (getattr(p, "pid", None), (p.info.get("name") or "").lower())
                    if key[0] is not None and key in self._pid_exe:
                        pe = self._pid_exe[key]
                    else:
                        pe = self._exe_of(p, self._names)
                        if pe is None and FAST_NAMES and key[1] and key[1] not in self._names:
                            continue                           # processus non suivi : rien à retenir
                    if key[0] is not None:
                        seen[key] = pe
                    if pe:
                        exes.add(norm_path(pe))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            self._pid_exe = seen                               # processus disparus oubliés
            self._running, self._running_at = exes, now
            dt = time.monotonic() - t0
            if dt > 1.0:
                log.warning("parcours des processus lent : %.1f s", dt)
        return self._running

    def _invalidate(self) -> None:
        self._running_at = 0.0
        self._pid_exe = {}                             # lancement / arrêt : chemins relus (aucun pid réutilisé mal attribué)

    def lower_priority(self, exe: Path, free_cores: int = 0) -> int:
        """Priorité « basse » (inactivité) pour les processus de cet exécutable : le testeur ne prend que le processeur
        laissé libre par les terminaux réels. Bot 3.6.2 : `free_cores` premiers cœurs interdits au testeur (réservés au
        bot et aux comptes). Renvoie le nombre de processus modifiés."""
        cls = getattr(psutil, "IDLE_PRIORITY_CLASS", 19)
        ncpu = psutil.cpu_count() or 1
        aff = list(range(min(free_cores, ncpu - 1), ncpu)) if free_cores > 0 and ncpu > 1 else None
        n = 0
        for p in self.find(exe):
            changed = False
            try:
                if p.nice() != cls:
                    p.nice(cls)
                    changed = True
                if aff is not None and hasattr(p, "cpu_affinity") and sorted(p.cpu_affinity()) != aff:
                    p.cpu_affinity(aff)
                    changed = True
            except (psutil.Error, OSError, AttributeError):
                pass
            n += changed
        return n

    def find(self, exe: Path) -> list[psutil.Process]:
        target = norm_path(exe)
        names = {Path(exe).name.lower()}
        found: list[psutil.Process] = []
        for p in psutil.process_iter(["name"]):
            try:
                pe = self._exe_of(p, names)
                if pe and norm_path(pe) == target:
                    found.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return found

    def is_running(self, exe: Path) -> bool:
        return norm_path(exe) in self._running_exes(Path(exe).name.lower())

    def launch(self, exe: Path, ini: Path | None = None) -> int:
        """Lance `terminal64.exe /portable [/config:ini]` détaché, dans le dossier de l'instance.
        Linux (Bot 3.15.0) : `wine <exe> /portable /config:Z:\\…` avec le préfixe et l'écran virtuel, dans sa propre session."""
        args, env, extra = self.command(exe, ["/portable"] + ([f"/config:{ini}"] if ini is not None else []))
        proc = subprocess.Popen(args, cwd=str(Path(exe).parent), env=env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                close_fds=True, **extra)
        log.info("terminal lancé : %s (pid %s)%s", Path(exe).parent.name, proc.pid, " avec /config" if ini else "")
        self._invalidate()
        self.rearrange(exe, extra_pid=proc.pid)
        return proc.pid

    def command(self, exe: Path, args: list[str]) -> tuple[list[str], dict | None, dict]:
        """(ligne de commande, environnement, options Popen) pour lancer un exécutable MT5 détaché.
        Arguments `/clé:chemin` convertis en chemins Wine sous Linux."""
        if self.wine is None:
            flags = 0
            if sys.platform == "win32":
                flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            return [str(exe), *args], None, {"creationflags": flags}
        from . import wine as w
        conv = []
        for a in args:
            if a.startswith("/") and ":" in a[1:]:
                k, v = a.split(":", 1)
                a = f"{k}:{w.to_win(v)}"
            conv.append(a)
        return [self.wine.cmd, str(exe), *conv], w.env(self.wine), {"start_new_session": True}

    def rearrange(self, exe: Path, extra_pid: int | None = None, settle: bool = True) -> None:
        """Panneaux masqués, graphique agrandi, fenêtre réduite (en arrière-plan ; Windows seulement)."""
        if self.window_opts is None or self.win_api is None:
            return
        from dataclasses import replace
        from .window import arrange_in_background
        opts = self.window_opts if settle else replace(self.window_opts, settle_s=0, timeout_s=5)
        pids = lambda: {p.pid for p in self.find(exe)} | ({extra_pid} if extra_pid else set())   # noqa: E731
        arrange_in_background(self.win_api, pids, opts, Path(exe).parent.name)

    def ensure_minimized(self, exe: Path) -> str:
        """Contrôle périodique de la fenêtre de ce terminal (voir window.ensure_arranged)."""
        if self.window_opts is None or self.win_api is None:
            return ""
        from .window import ensure_arranged
        return ensure_arranged(self.win_api, lambda: {p.pid for p in self.find(exe)}, self.window_opts, Path(exe).parent.name)

    def companions(self, exe: Path) -> list[psutil.Process]:
        """Bot 3.21.0 (Wine) : processus annexes d'un terminal (WebView2 / Edge…) qui citent son dossier. Sous Wine ils
        survivent au terminal (et peuvent même l'empêcher de se fermer)."""
        if self.wine is None:
            return []
        from .wine import mentions
        folder = Path(exe).parent
        out = []
        for p in psutil.process_iter(["cmdline"]):
            try:
                if mentions(p.info.get("cmdline") or [], folder):
                    out.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return out

    def kill(self, exe: Path) -> int:
        """Termine tous les processus de CET exécutable (et, sous Wine, ses processus annexes) ; renvoie le nombre tué."""
        n = 0
        procs = self.find(exe)
        seen = {p.pid for p in procs}
        procs += [p for p in self.companions(exe) if p.pid not in seen]
        for p in procs:
            try:
                p.kill()
                n += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        psutil.wait_procs(procs, timeout=10)
        self._invalidate()
        return n


def vps_load(path: Path) -> dict[str, float]:
    """Charge du VPS : CPU %, RAM %, disque % (pour l'en-tête du tableau de bord)."""
    try:
        disk = psutil.disk_usage(str(path)).percent
    except OSError:
        disk = 0.0
    return {"cpu": psutil.cpu_percent(interval=None), "ram": psutil.virtual_memory().percent, "disk": disk}
