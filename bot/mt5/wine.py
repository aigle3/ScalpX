"""Bot 3.15.0 : MT5 sous Linux avec Wine.

Les terminaux, MetaEditor et le testeur sont des programmes Windows : sous Linux ils sont lancés par `wine`, dans un
seul préfixe (faux disque C:), avec un écran virtuel Xvfb (DISPLAY). Wine voit tout le disque Linux sous la lettre Z: :
/home/scalpx/mt5/BASE  ↔  Z:\\home\\scalpx\\mt5\\BASE.

Sous Wine, le « chemin de l'exécutable » d'un processus est celui du chargeur Wine : le terminal est reconnu par le
premier argument de sa ligne de commande (chemin Windows de terminal64.exe), reconverti ici en chemin Linux.
Sous Windows, rien de ce module ne sert (IS_WINDOWS).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

IS_WINDOWS = os.name == "nt"
_RE_DRIVE = re.compile(r"^([A-Za-z]):[\\/]")


@dataclass
class WineOptions:
    cmd: str = "wine"                    # commande Wine (wine, ou chemin complet)
    prefix: Path | None = None           # préfixe Wine ; None → $WINEPREFIX, sinon ~/.wine
    display: str = ":99"                 # écran virtuel Xvfb
    debug: str = "-all"                  # WINEDEBUG : aucun message de débogage (moins de CPU et de journaux)

    def prefix_path(self) -> Path:
        if self.prefix:
            return Path(self.prefix).expanduser()
        env = os.environ.get("WINEPREFIX", "")
        return Path(env).expanduser() if env else Path.home() / ".wine"


def to_win(path: str | Path) -> str:
    """Chemin Linux absolu → chemin vu par Wine (Z:\\…). Un chemin déjà Windows est rendu tel quel."""
    s = str(path)
    if _RE_DRIVE.match(s):
        return s
    return "Z:" + str(PurePosixPath(os.path.abspath(s))).replace("/", "\\")


def from_win(path: str, prefix: Path | None = None) -> str:
    """Chemin Windows (vu par Wine) → chemin Linux. Z: = racine Linux ; autres lettres : <préfixe>/dosdevices/<lettre>:
    (C: = <préfixe>/drive_c). Chemin non Windows : rendu tel quel."""
    m = _RE_DRIVE.match(path or "")
    if not m:
        return path
    drive = m.group(1).lower()
    rest = path[3:].replace("\\", "/")
    if drive == "z":
        return "/" + rest
    pre = Path(prefix) if prefix else WineOptions().prefix_path()
    root = pre / "drive_c" if drive == "c" else pre / "dosdevices" / f"{drive}:"
    return str(root / rest) if rest else str(root)


def env(opts: WineOptions) -> dict[str, str]:
    """Environnement d'un programme lancé par Wine."""
    e = dict(os.environ)
    e["WINEPREFIX"] = str(opts.prefix_path())
    e["DISPLAY"] = opts.display or e.get("DISPLAY", ":99")
    e["WINEDEBUG"] = opts.debug
    e.setdefault("WINEDLLOVERRIDES", "mscoree,mshtml=")   # pas d'assistant Mono / Gecko au premier lancement
    return e


def common_files(opts: WineOptions, user: str | None = None) -> Path:
    """Dossier Common\\Files de MT5 dans le préfixe (fichiers d'échange bot ↔ EA)."""
    user = user or os.environ.get("USER") or os.environ.get("LOGNAME") or "scalpx"
    return (opts.prefix_path() / "drive_c" / "users" / user / "AppData" / "Roaming" / "MetaQuotes" / "Terminal"
            / "Common" / "Files")


_RE_EXE = re.compile(r'^\s*"?((?:[A-Za-z]:[\\/]|/)?[^"]*?\.exe)(?="|\s|$)', re.I)
_WINE_NAMES = ("wine", "wine64", "wine.exe", "start.exe", "wine64-preloader", "wine-preloader", "wineserver")


def cmdline_exe(cmdline: list[str], prefix: Path | None = None, cwd: str | None = None) -> str | None:
    """Chemin Linux de l'exécutable Windows d'un processus Wine, d'après sa ligne de commande.
    Formes vues : ["Z:\\…\\terminal64.exe", "/portable", …], ["wine", "/home/…/terminal64.exe", …],
    ligne Windows entière dans un seul argument (« C:\\…\\terminal64.exe /portable /config:… », chemin entre
    guillemets s'il contient des espaces), ou nom seul (« terminal64.exe », relatif au dossier courant `cwd`)."""
    for arg in (cmdline or [])[:3]:
        m = _RE_EXE.match(arg or "")
        if not m:
            continue
        path = m.group(1)
        name = PurePosixPath(path.replace("\\", "/")).name.lower()
        if name in _WINE_NAMES:
            continue
        if _RE_DRIVE.match(path):
            return from_win(path, prefix)
        if path.startswith("/"):
            return os.path.abspath(path)
        if cwd:
            return os.path.abspath(os.path.join(cwd, path.replace("\\", "/")))
        return None
    return None
