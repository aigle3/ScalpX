"""Gestion des processus terminal64.exe (MT5 sous Wine). Tous s'appellent pareil : on identifie par le CHEMIN de
l'exécutable Windows, lu dans la ligne de commande (sous Wine le processus s'appelle « main »)."""
from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Protocol

import psutil

log = logging.getLogger(__name__)
COMM_LEN = 15                       # nom de processus Linux tronqué à 15 caractères (metatester64.exe → metatester64.ex)
UID = os.getuid()                   # les terminaux tournent sous l'utilisateur du bot : les autres processus sont ignorés


def own_procs(*attrs: str):
    """Processus de l'utilisateur du bot seulement (les terminaux Wine sont lancés par lui) : parcours bien plus court,
    et seuls ces processus ont une ligne de commande et un dossier courant lisibles."""
    for p in psutil.process_iter(["uids", *attrs]):
        u = p.info.get("uids")
        if u is None or u.real == UID:
            yield p


def norm_path(p: str | Path) -> str:
    """Chemin normalisé pour comparaison."""
    return os.path.normcase(os.path.abspath(str(p)))


class ProcessAPI(Protocol):
    def is_running(self, exe: Path) -> bool: ...
    def launch(self, exe: Path, ini: Path | None = None) -> int: ...
    def kill(self, exe: Path) -> int: ...


class PsutilProcesses:
    """Implémentation réelle (psutil + subprocess)."""

    CACHE_S = 2.0                     # le tableau de bord interroge chaque compte : un seul parcours des processus

    def __init__(self, wine=None) -> None:
        self._running: set[str] = set()
        self._running_at = 0.0
        self._names: set[str] = set()                 # noms d'exécutables suivis (terminal64.exe)
        self._pid_exe: dict[tuple[int, str], str | None] = {}   # Bot 3.10.0 : (pid, nom) → chemin déjà lu (pas rouvert à chaque parcours)
        from .wine import WineOptions
        self.wine = wine or WineOptions()             # préfixe, écran virtuel, WebView2

    def _exe_of(self, p: psutil.Process, names: set[str]) -> str | None:
        """Chemin Linux de l'exécutable Windows d'un processus Wine. Le processus porte un nom quelconque (« main » pour
        terminal64.exe) : la ligne de commande est lue (une fois par processus, résultat mémorisé), sinon le dossier courant."""
        name = (p.info.get("name") or "").lower()
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

    def _running_exes(self, name: str = "") -> set[str]:
        now = time.monotonic()
        if name and name not in self._names:
            self._names.add(name)
            self._running_at = 0.0                     # nouveau nom suivi : parcours complet tout de suite
        if now - self._running_at > self.CACHE_S:
            t0 = time.monotonic()
            exes: set[str] = set()
            seen: dict[tuple[int, str], str | None] = {}
            for p in own_procs("name"):
                try:
                    key = (getattr(p, "pid", None), (p.info.get("name") or "").lower())
                    if key[0] is not None and key in self._pid_exe:
                        pe = self._pid_exe[key]
                    else:
                        pe = self._exe_of(p, self._names)
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
        for p in own_procs("name"):
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
        return proc.pid

    def command(self, exe: Path, args: list[str]) -> tuple[list[str], dict | None, dict]:
        """(ligne de commande, environnement, options Popen) pour lancer un exécutable MT5 détaché par Wine.
        Arguments `/clé:chemin` convertis en chemins Wine."""
        from . import wine as w
        conv = []
        for a in args:
            if a.startswith("/") and ":" in a[1:]:
                k, v = a.split(":", 1)
                a = f"{k}:{w.to_win(v)}"
            conv.append(a)
        opts = self.wine
        try:                                                  # Bot 4.10.0 : environnement Wine du groupe du terminal
            from dataclasses import replace
            from .groups import prefix_for
            main = self.wine.prefix_path()
            pre = prefix_for(Path(exe).parent, main)
            if pre != main:
                opts = replace(self.wine, prefix=pre)
        except Exception as exc:
            log.warning("environnement Wine du terminal : %s", exc)
        return [self.wine.cmd, str(exe), *conv], w.env(opts), {"start_new_session": True}

    def companions(self, exe: Path) -> list[psutil.Process]:
        """Bot 3.21.0 : processus annexes d'un terminal (WebView2 / Edge…) qui citent son dossier. Sous Wine ils
        survivent au terminal (et peuvent même l'empêcher de se fermer)."""
        from .wine import mentions
        folder = Path(exe).parent
        out = []
        for p in own_procs("cmdline"):
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


def pin_plan(running: list, ncpu: int | None = None) -> dict[str, int]:
    """Bot 4.14.5 : répartition ÉQUILIBRÉE et STABLE des terminaux EN MARCHE (avant : rang parmi TOUS les comptes, arrêtés
    compris → jusqu'à 4 terminaux sur un cœur et 2 sur un autre, et tout décalé à chaque ajout / suppression de compte).
    running : instances lancées (attribut login) ; renvoie id → processeur. Tri par numéro de compte ; le cœur 0 (bot,
    écran virtuel, système) est servi en dernier quand la division ne tombe pas juste."""
    ncpu = ncpu or os.cpu_count() or 1
    order = sorted(running, key=lambda i: (int(getattr(i, "login", 0) or 0), str(getattr(i, "id", ""))))
    return {i.id: (k + 1) % ncpu for k, i in enumerate(order)}


def pin_terminals(procs: "PsutilProcesses", targets: dict[str, int], ncpu: int | None = None) -> int:
    """Bot 4.12.0 : chaque terminal (TOUS ses fils d'exécution) attaché à un seul processeur ; wineserver garde tous les
    processeurs. Sous Wine, un terminal réparti sur plusieurs cœurs passe beaucoup de temps à se synchroniser entre eux
    (temps « système ») : mesuré sur le VPS (12 terminaux, 4 cœurs) : système 42 % → 29 %, attente CPU 58 % → 38 %.
    targets : chemin normalisé de terminal64.exe → numéro de processeur. Renvoie le nombre de fils modifiés."""
    ncpu = ncpu or os.cpu_count() or 1
    if ncpu < 2 or not hasattr(os, "sched_setaffinity"):
        return 0
    every = set(range(ncpu))
    names = {Path(t).name.lower() for t in targets} or {"terminal64.exe"}
    changed = 0
    for p in own_procs("name"):
        try:
            name = (p.info.get("name") or "").lower()
            if name == "wineserver":
                want = every
            else:
                pe = procs._exe_of(p, names)
                if not pe or norm_path(pe) not in targets:
                    continue
                want = {targets[norm_path(pe)] % ncpu}
            for t in p.threads():
                try:
                    if os.sched_getaffinity(t.id) != want:
                        os.sched_setaffinity(t.id, want)
                        changed += 1
                except OSError:
                    continue
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            continue
    return changed


def vps_load(path: Path) -> dict[str, float]:
    """Charge du VPS : CPU %, RAM %, disque % (pour l'en-tête du tableau de bord)."""
    try:
        disk = psutil.disk_usage(str(path)).percent
    except OSError:
        disk = 0.0
    return {"cpu": psutil.cpu_percent(interval=None), "ram": psutil.virtual_memory().percent, "disk": disk}
