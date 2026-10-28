"""Mise à jour automatique depuis un dossier « paquet » local, téléchargé depuis GitHub (core.github).

Paquet (structure du dépôt) :
  <source>\\bot\\…                      code du bot
  <source>\\ea\\MQL5\\Experts\\*.mq5 *.mqh   sources de l'EA
  <source>\\ea\\MQL5\\Presets\\*.set        préréglages
  <source>\\ea\\version.txt              version publiée

Le bot compare le paquet à ce qui est installé et propose « Installer » dans Telegram :
  EA  → sources et .set copiés dans BASE, puis BASE\\version.txt écrit en dernier (la compilation automatique suit,
        puis la proposition de déploiement sur les comptes) ;
  bot → fichiers copiés dans le dossier du bot (jamais .env, config.toml, data\\, .venv\\), puis redémarrage.
Jamais de .ex5 copié : BASE compile lui-même.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from core.config import Config

log = logging.getLogger(__name__)

BOT_EXT = {".py", ".txt", ".md", ".ini", ".xml", ".toml", ".bat", ".ps1"}
BOT_SKIP_DIRS = {".venv", "data", "__pycache__", ".pytest_cache"}
BOT_SKIP_FILES = {".env", "config.toml"}
_VERSION_LINES = re.compile(r'^\s*(#define\s+SX_VERSION\b|#property\s+version\b|#property\s+description\s+"ScalpX v).*$', re.M)


@dataclass
class UpdatePlan:
    source: Path
    bot_files: list[str] = field(default_factory=list)      # chemins relatifs (bot/…)
    ea_files: list[str] = field(default_factory=list)       # chemins relatifs (MQL5/…)
    version: str = ""                                       # version.txt du paquet
    requirements_changed: bool = False
    stamp: str = ""
    note: str = ""                                          # message du commit GitHub
    bot_remove: list[str] = field(default_factory=list)     # fichiers du bot qui n'existent plus dans le dépôt

    @property
    def empty(self) -> bool:
        return not self.bot_files and not self.ea_files and not self.bot_remove


def find_source(cfg: Config) -> Path | None:
    """Dossier paquet local ([update] source), alimenté par core.github depuis le dépôt."""
    c = Path(cfg.update.source) if cfg.update.source else None
    return c if c is not None and (c / "bot" / "main.py").is_file() else None


def commit_info(src: Path) -> dict:
    """Commit GitHub du paquet (écrit par core.github) : sha, message, date."""
    try:
        import json
        return json.loads((src / ".commit.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _norm_ea(raw: bytes) -> bytes:
    """Sources MQL sans les lignes de version (réécrites par la compilation automatique) ni différences de fins de ligne."""
    for bom in (b"\xef\xbb\xbf",):
        raw = raw.removeprefix(bom)
    try:
        txt = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.replace(b"\r\n", b"\n")
    return _VERSION_LINES.sub("", txt.replace("\r\n", "\n")).encode("utf-8")


def _same(a: Path, b: Path, ea: bool = False) -> bool:
    try:
        ra, rb = a.read_bytes(), b.read_bytes()
    except OSError:
        return False
    return (_norm_ea(ra) == _norm_ea(rb)) if ea else ra == rb


def _bot_sources(src: Path) -> list[Path]:
    return _bot_files(src / "bot")


def _bot_files(root: Path) -> list[Path]:
    """Fichiers de code/doc d'un dossier de bot (hors .env, config.toml, data\\, .venv\\, caches)."""
    out = []
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in BOT_EXT or p.name in BOT_SKIP_FILES:
            continue
        if any(part in BOT_SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        out.append(p)
    return sorted(out)


def _ea_sources(src: Path) -> list[Path]:
    root = src / "ea" / "MQL5"
    out: list[Path] = []
    for sub, pats in (("Experts", ("*.mq5", "*.mqh")), ("Presets", ("*.set",))):
        for pat in pats:
            out.extend((root / sub).glob(pat))
    return sorted(out)


def _vt(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.strip().split("."))
    except ValueError:
        return ()


def bump(v: str) -> str:
    parts = v.strip().split(".") if _vt(v) else ["5", "0"]
    parts[-1] = str(int(parts[-1]) + 1)
    return ".".join(parts)


def plan_update(cfg: Config, bot_root: Path) -> UpdatePlan | None:
    """Différences paquet → installation (None si aucun paquet). Le paquet est extrait d'un coup par core.github."""
    src = find_source(cfg)
    if src is None:
        return None
    vfile = src / "ea" / "version.txt"
    plan = UpdatePlan(src)
    try:
        plan.version = vfile.read_text(encoding="utf-8-sig").strip()
    except OSError:
        pass
    pkg = set()
    for p in _bot_sources(src):
        rel = p.relative_to(src / "bot")
        pkg.add(rel)
        if not _same(p, bot_root / rel):
            plan.bot_files.append(str(rel).replace("\\", "/"))
    if (bot_root / "main.py").is_file():                              # fichiers retirés du dépôt : supprimés à l'installation
        plan.bot_remove = [str(p.relative_to(bot_root)).replace("\\", "/")
                           for p in _bot_files(bot_root) if p.relative_to(bot_root) not in pkg]
    for p in _ea_sources(src):
        rel = p.relative_to(src / "ea")
        if not _same(p, cfg.paths.base / rel, ea=p.suffix.lower() in (".mq5", ".mqh")):
            plan.ea_files.append(str(rel).replace("\\", "/"))
    plan.requirements_changed = "requirements.txt" in plan.bot_files
    h = hashlib.sha256()
    for rel in plan.bot_files + plan.ea_files:
        f = src / ("bot" if rel in plan.bot_files else "ea") / rel
        h.update(rel.encode())
        h.update(f.read_bytes())
    for rel in plan.bot_remove:
        h.update(("-" + rel).encode())
    plan.stamp = h.hexdigest()[:16]
    plan.note = commit_info(src).get("message", "")
    return plan


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".upd")
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)
    os.utime(dst, None)                                               # date = maintenant (ordre version.txt / sources)


def install_ea(cfg: Config, plan: UpdatePlan) -> str:
    """Copie les sources/.set dans BASE. Si des sources ont changé, écrit BASE\\version.txt EN DERNIER → compilation auto.
    Renvoie la version demandée ('' si aucune compilation à faire)."""
    base = cfg.paths.base
    src_changed = False
    for rel in plan.ea_files:
        _copy(plan.source / "ea" / rel, base / rel)
        src_changed = src_changed or rel.lower().endswith((".mq5", ".mqh"))
    if not src_changed:
        return ""
    try:
        cur = (base / "version.txt").read_text(encoding="utf-8-sig").strip()
    except OSError:
        cur = ""
    new = plan.version if _vt(plan.version) > _vt(cur) else bump(cur or plan.version)
    time.sleep(1.1)                                                   # version.txt strictement plus récent que les sources
    (base / "version.txt").write_text(new, encoding="utf-8")
    return new


def install_bot(plan: UpdatePlan, bot_root: Path) -> None:
    for rel in plan.bot_files:
        _copy(plan.source / "bot" / rel, bot_root / rel)
    for rel in plan.bot_remove:
        try:
            (bot_root / rel).unlink()
        except OSError:
            pass
    if not (bot_root / "config.toml").exists() and (plan.source / "bot" / "config.toml").exists():
        _copy(plan.source / "bot" / "config.toml", bot_root / "config.toml")


def pip_install(bot_root: Path) -> tuple[bool, str]:
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r",
                        str(bot_root / "requirements.txt")], capture_output=True, text=True, timeout=600,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.returncode == 0, (r.stderr or r.stdout or "")[-300:]


TASK = "ScalpX Bot"
STOP = threading.Event()                  # positionné après une mise à jour du bot : main.py arrête proprement ce processus


def restart_bot(bot_root: Path) -> str:
    """Relance le bot avec le nouveau code. Le nouveau processus remplace l'ancien (verrou : QUIT).
    Tâche planifiée présente → elle est relancée après l'arrêt de celui-ci ; sinon nouveau processus détaché."""
    no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    detached = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    has_task = os.name == "nt" and subprocess.run(["schtasks", "/query", "/tn", TASK], capture_output=True,
                                                  creationflags=no_win).returncode == 0
    if has_task:
        # la tâche n'accepte qu'une instance : on la relance 20 s plus tard, une fois ce processus arrêté
        subprocess.Popen(f'cmd /c ping -n 21 127.0.0.1 >nul & schtasks /run /tn "{TASK}"',
                         creationflags=detached | no_win, close_fds=True)
        return "task"
    exe = Path(sys.executable)
    if os.name == "nt" and exe.name.lower() == "python.exe" and (exe.with_name("pythonw.exe")).exists():
        exe = exe.with_name("pythonw.exe")                            # pas de fenêtre noire orpheline
    subprocess.Popen([str(exe), str(bot_root / "main.py")], cwd=str(bot_root), creationflags=detached | no_win, close_fds=True)
    return "process"
