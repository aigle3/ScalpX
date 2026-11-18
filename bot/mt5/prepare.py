"""Bot 4.5.0 : BASE « prêt ».

À sa première ouverture (MT5 neuf ou mis à jour), MetaTrader recompile tous les programmes MQL5 fournis par MetaQuotes
(~130 fichiers : « full recompilation has been started … finished » dans son journal). Si BASE n'a pas fait ce travail,
CHAQUE compte créé le refait : plusieurs minutes à 100 % de CPU par compte, et aucun signal de l'EA pendant ce temps.

Le bot ouvre donc MT5 une fois dans BASE, attend la fin de cette compilation, le ferme et pose une marque
(`.scalpx_base_prete` = taille:date de terminal64.exe). Tant que la marque manque ou ne correspond plus au terminal64.exe
(MT5 remplacé), la création de comptes est refusée. linux/install.sh fait la même chose (même marque) à l'installation.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

MARK = ".scalpx_base_prete"
STARTED = "recompilation has been started"
FINISHED = "recompilation has been finished"
QUIET_S = 120.0                     # aucune compilation annoncée après 2 min : rien à compiler
TIMEOUT_S = 1500.0                  # 25 min au plus (VPS chargé, Wine)


def signature(base: Path, exe: str) -> str:
    st = (Path(base) / exe).stat()
    return f"{st.st_size}:{int(st.st_mtime)}"


def is_ready(base: Path, exe: str) -> bool:
    try:
        return (Path(base) / MARK).read_text(encoding="utf-8").strip() == signature(base, exe)
    except OSError:
        return False


def mark_ready(base: Path, exe: str) -> None:
    (Path(base) / MARK).write_text(signature(base, exe) + "\n", encoding="utf-8")


def _decode(raw: bytes) -> str:
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or raw[1:2] == b"\x00":
        return raw.decode("utf-16", errors="ignore")
    return raw.decode("utf-8", errors="ignore")


def journal_since(base: Path, t0: float) -> str:
    """Texte des journaux du terminal de BASE (logs/*.log) modifiés depuis t0."""
    out = []
    for f in sorted((Path(base) / "logs").glob("*.log")):
        try:
            if f.stat().st_mtime >= t0 - 1:
                out.append(_decode(f.read_bytes()))
        except OSError:
            continue
    return "\n".join(out)


def compile_state(text: str, before: str = "") -> str:
    """'finished' / 'started' / '' d'après les lignes APPARUES depuis le lancement (`before` = journal d'avant)."""
    new = text[len(before):] if before and text.startswith(before) else text
    if FINISHED in new:
        return "finished"
    if STARTED in new:
        return "started"
    return ""


def _kill_exe(path: Path) -> int:
    """Ferme les processus Wine lancés depuis `path` (terminal ou MetaEditor de BASE)."""
    from .build import _kill_tree
    from .procs import own_procs
    from .wine import cmdline_exe
    target = os.path.normcase(os.path.abspath(str(path)))
    n = 0
    for p in own_procs("name"):
        try:
            exe = cmdline_exe(p.cmdline()) or ""
            if exe and os.path.normcase(os.path.abspath(exe)) == target:
                _kill_tree(p.pid)
                n += 1
        except Exception:
            continue
    return n


def prepare(cfg, timeout: float = TIMEOUT_S, poll: float = 5.0, launcher=None, clock=time.monotonic,
            sleep=time.sleep) -> tuple[bool, str]:
    """Ouvre MT5 dans BASE, attend la fin de sa compilation de départ, le ferme, pose la marque. (ok, message)."""
    from . import wine
    from .build import METAEDITOR
    base, exe = Path(cfg.paths.base), cfg.mt5.terminal_exe
    if not (base / exe).is_file():
        return False, f"{exe} absent de BASE"
    if is_ready(base, exe):
        return True, "déjà prêt"
    t0w = time.time()
    before = journal_since(base, t0w - 86400)               # journal du jour avant le lancement (lignes anciennes ignorées)
    cmd = [cfg.wine.cmd, str(base / exe), "/portable"]
    env = {**os.environ, **wine.env(cfg.wine.options())}
    if launcher is None:
        proc = subprocess.Popen(cmd, cwd=str(base), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    else:
        proc = launcher(cmd, env)
    t0 = clock()
    state, ok, msg = "", False, ""
    try:
        while True:
            sleep(poll)
            state = compile_state(journal_since(base, t0w - 86400), before) or state
            el = clock() - t0
            if state == "finished":
                ok, msg = True, "compilation de départ de MT5 terminée"
                break
            if state == "" and el >= QUIET_S:
                ok, msg = True, "rien à compiler"
                break
            if el >= timeout:
                msg = f"compilation de départ de MT5 non terminée en {int(timeout // 60)} min"
                break
        if ok:
            sleep(5)                                        # fin d'écriture des .ex5
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        if launcher is None:
            _kill_exe(base / exe)
            _kill_exe(base / METAEDITOR)
    if ok:
        mark_ready(base, exe)
        log.info("BASE prêt : %s", msg)
    else:
        log.warning("BASE non prêt : %s", msg)
    return ok, msg
