"""Compilation automatique de l'EA dans BASE quand version.txt change.

Déroulé : version.txt (écrit par l'utilisateur) ≠ SX_VERSION des sources
  → SX_VERSION (SX_Core.mqh) et #property version / description (ScalpX_v5.mq5) mis à la version demandée
  → MetaEditor64.exe /compile en ligne de commande, journal analysé (0 erreur exigé)
  → le nouvel .ex5 est ensuite repéré par detect_new_build() qui propose le déploiement.
Seules ces lignes de version sont modifiées : aucune logique de trading n'est touchée.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from core.config import Config
from storage.db import Database
from . import base as basemod

log = logging.getLogger(__name__)

METAEDITOR = "MetaEditor64.exe"
_RE_DEFINE = re.compile(r'(#define\s+SX_VERSION\s+")([^"]*)(")')
_RE_PROP = re.compile(r'(#property\s+version\s+")([^"]*)(")')
_RE_DESC = re.compile(r'(#property\s+description\s+"ScalpX v)([0-9][0-9.]*)')
_RE_VER = re.compile(r"^\d+(\.\d+){0,2}$")


@dataclass
class BuildRequest:
    version: str
    stamp: str                                  # version + date de version.txt : une seule tentative par modification


@dataclass
class BuildResult:
    ok: bool
    version: str
    errors: int
    warnings: int
    detail: str = ""


def experts(cfg: Config) -> Path:
    return cfg.paths.base / "MQL5" / "Experts"


def source_version(cfg: Config) -> str:
    try:
        m = _RE_DEFINE.search(_read(experts(cfg) / "SX_Core.mqh")[0])
        return m.group(2).strip() if m else ""
    except OSError:
        return ""


def txt_version(cfg: Config) -> tuple[str, float]:
    p = cfg.paths.base / "version.txt"
    try:
        return p.read_text(encoding="utf-8-sig").strip(), p.stat().st_mtime
    except OSError:
        return "", 0.0


def pending_build(cfg: Config, db: Database, settle_s: float = 5.0, now: float | None = None) -> BuildRequest | None:
    """version.txt modifié (stable depuis settle_s), différent des sources, pas encore tenté → compilation à faire."""
    ver, mtime = txt_version(cfg)
    if not ver or not _RE_VER.match(ver) or (now or time.time()) - mtime < settle_s:
        return None
    if ver == source_version(cfg):
        # même version dans les sources (mise à jour GitHub : SX_VERSION déjà écrit) : compiler si le .ex5 est plus
        # ancien que les sources, sinon les comptes resteraient sur l'ancien EA
        if not sources_newer_than_ex5(cfg):
            return None
    else:
        try:                                     # version.txt doit être PLUS RÉCENT que les sources : si tu as changé
            if mtime <= (experts(cfg) / "SX_Core.mqh").stat().st_mtime:   # SX_VERSION à la main après, le bot ne l'écrase pas
                return None
        except OSError:
            return None
    stamp = f"{ver}@{int(mtime)}"
    if db.meta_get("autobuild_stamp") == stamp:
        return None
    if not (experts(cfg) / f"{cfg.mt5.ea_name}.mq5").is_file() or not (cfg.paths.base / METAEDITOR).is_file():
        return None
    return BuildRequest(ver, stamp)


def sources_newer_than_ex5(cfg: Config) -> bool:
    """Le .ex5 de BASE est absent ou plus ancien qu'un fichier source (.mq5/.mqh) : il ne correspond pas aux sources."""
    ex = experts(cfg)
    try:
        built = (ex / f"{cfg.mt5.ea_name}.ex5").stat().st_mtime
    except OSError:
        return True
    return any(f.stat().st_mtime > built for f in ex.iterdir() if f.suffix.lower() in (".mq5", ".mqh"))


# ---- édition des sources (encodage et fins de ligne conservés)
def _read(p: Path) -> tuple[str, str, bytes]:
    raw = p.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16"), "utf-16", b""
    bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
    return raw[len(bom):].decode("utf-8"), "utf-8", bom


def _write(p: Path, text: str, enc: str, bom: bytes) -> None:
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(text.encode("utf-16") if enc == "utf-16" else bom + text.encode("utf-8"))
    os.replace(tmp, p)


def prop_version(ver: str) -> str:
    """#property version attend « X.YY » : majeur.mineur sur 2 chiffres (5.3.1 → 5.03, 5.12.0 → 5.12).
    La version complète à 3 niveaux est dans SX_VERSION (affichée partout)."""
    parts = ver.split(".")
    major = parts[0]
    minor = (parts[1] if len(parts) > 1 else "0")[-2:].rjust(2, "0")
    return f"{major}.{minor}"


def set_source_version(cfg: Config, ver: str) -> None:
    core = experts(cfg) / "SX_Core.mqh"
    text, enc, bom = _read(core)
    if not _RE_DEFINE.search(text):
        raise ValueError("#define SX_VERSION introuvable dans SX_Core.mqh")
    _write(core, _RE_DEFINE.sub(lambda m: m.group(1) + ver + m.group(3), text, count=1), enc, bom)
    mq5 = experts(cfg) / f"{cfg.mt5.ea_name}.mq5"
    text, enc, bom = _read(mq5)
    new = _RE_PROP.sub(lambda m: m.group(1) + prop_version(ver) + m.group(3), text, count=1)
    new = _RE_DESC.sub(lambda m: m.group(1) + ver, new, count=1)
    if new != text:
        _write(mq5, new, enc, bom)


# ---- compilation
def parse_log(text: str) -> tuple[int, int, list[str]]:
    """Journal MetaEditor → (erreurs, avertissements, lignes d'erreur)."""
    m = re.search(r"(\d+)\s+errors?,\s*(\d+)\s+warnings?", text, re.I)
    errs = [l.strip() for l in text.splitlines() if re.search(r"\berror\b", l, re.I) and not re.search(r"\d+\s+errors?,", l, re.I)]
    if m:
        return int(m.group(1)), int(m.group(2)), errs
    return (len(errs) or -1), 0, errs                     # -1 : résultat illisible


def command_line(cfg: Config, log_path: Path) -> str | list[str]:
    """Windows : ligne de commande de MetaEditor. Linux (Bot 3.15.0) : liste `wine MetaEditor64.exe …` avec chemins Z:\\…"""
    base = cfg.paths.base
    mq5 = experts(cfg) / f"{cfg.mt5.ea_name}.mq5"
    if os.name != "nt":
        from .wine import to_win
        return [cfg.wine.cmd, str(base / METAEDITOR), "/portable", f"/compile:{to_win(mq5)}",
                f"/inc:{to_win(base / 'MQL5')}", f"/log:{to_win(Path(log_path).resolve())}"]
    return (f'"{base / METAEDITOR}" /portable /compile:"{mq5}" '
            f'/inc:"{base / "MQL5"}" /log:"{log_path}"')


COMPILE_TIMEOUT_S = 900.0           # Bot 3.6.4 : 15 min (5 min avant : VPS chargé, compilation coupée à 95 %)


def _kill_tree(pid: int) -> None:
    try:
        import psutil
        p = psutil.Process(pid)
        for c in p.children(recursive=True):
            try:
                c.kill()
            except psutil.Error:
                pass
        p.kill()
    except Exception:                                         # psutil absent / processus déjà fini
        pass


def kill_stale(base: Path) -> int:
    """Bot 3.2.4 : ferme les MetaEditor de BASE restés ouverts (compilation précédente bloquée, fenêtre oubliée) :
    tant qu'il en reste un, chaque nouvelle compilation attend derrière lui et finit en « n'a pas fini »."""
    try:
        import psutil
    except ImportError:
        return 0
    target = os.path.normcase(os.path.abspath(str(Path(base) / METAEDITOR)))
    n = 0
    wine = os.name != "nt"                                    # Bot 3.15.0 : Linux → nom tronqué à 15, chemin dans la ligne de commande
    for p in psutil.process_iter(["name", "exe"]):
        try:
            if (p.info.get("name") or "").lower()[:15 if wine else None] != METAEDITOR.lower()[:15 if wine else None]:
                continue
            if wine:
                from .wine import cmdline_exe
                exe = cmdline_exe(p.cmdline()) or ""
            else:
                exe = p.info.get("exe") or ""
            if exe and os.path.normcase(os.path.abspath(exe)) != target:
                continue
            _kill_tree(p.pid)
            n += 1
        except Exception:
            continue
    if n:
        log.warning("compilation : %d MetaEditor de BASE resté(s) ouvert(s) fermé(s)", n)
        time.sleep(2)
    return n


_ENV: dict | None = None                                      # Linux : environnement Wine de la compilation en cours


def _run(cmd: str | list[str], timeout: float, env: dict | None = None) -> None:
    # Bot 3.6.4 : MetaEditor en priorité « au-dessus de la normale » : avec le VPS à 100 % (terminaux, testeur),
    # la phase d'optimisation de la compilation (après « generating code 95 % ») n'avançait plus.
    # Linux : priorité normale (monter la priorité exige les droits root) ; environnement Wine.
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "ABOVE_NORMAL_PRIORITY_CLASS", 0)
    proc = subprocess.Popen(cmd, shell=False, creationflags=flags, env=env or _ENV,
                            **({} if os.name == "nt" else {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                                                           "stderr": subprocess.DEVNULL}))
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc.pid)                                  # sinon MetaEditor reste bloqué et bloque les suivantes
        raise


async def compile_ea(cfg: Config, timeout: float = COMPILE_TIMEOUT_S, runner=None) -> BuildResult:
    if runner is None:
        await asyncio.get_running_loop().run_in_executor(None, kill_stale, cfg.paths.base)
    global _ENV
    if os.name != "nt":                                        # Linux : MetaEditor lancé par Wine (préfixe, écran virtuel)
        from .wine import env as wine_env
        _ENV = wine_env(cfg.wine.options())
    runner = runner or _run
    ver = source_version(cfg)
    ex5 = experts(cfg) / cfg.mt5.ea_file
    log_path = cfg.paths.data / "logs" / "compile.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        log_path.unlink()
    except OSError:
        pass
    before = ex5.stat().st_mtime if ex5.exists() else 0.0
    try:
        await asyncio.get_running_loop().run_in_executor(None, runner, command_line(cfg, log_path), timeout)
    except subprocess.TimeoutExpired:
        try:
            tail = [l for l in basemod._decode(log_path.read_bytes()).splitlines() if l.strip()][-2:]
        except OSError:
            tail = []
        return BuildResult(False, ver, -1, 0, f"MetaEditor n'a pas fini en {int(timeout // 60)} min (fermé ; réessaie)"
                           + ("\n" + "\n".join(tail) if tail else ""))
    except OSError as exc:
        return BuildResult(False, ver, -1, 0, f"MetaEditor non lancé : {exc}")
    try:
        text = basemod._decode(log_path.read_bytes())
    except OSError:
        text = ""
    errors, warnings, lines = parse_log(text)
    changed = ex5.exists() and ex5.stat().st_mtime > before
    ok = errors == 0 and changed
    detail = "\n".join(lines[:5]) if lines else ("" if ok else ("journal de compilation vide" if not text else ".ex5 non régénéré"))
    log.info("compilation v%s : %s (%s erreur(s), %s avertissement(s))", ver, "OK" if ok else "ÉCHEC", errors, warnings)
    return BuildResult(ok, ver, errors, warnings, detail)


async def auto_build(cfg: Config, db: Database, req: BuildRequest, runner=None) -> BuildResult:
    """Met la version dans les sources puis compile. Une seule tentative par modification de version.txt."""
    db.meta_set("autobuild_stamp", req.stamp)
    try:
        set_source_version(cfg, req.version)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        return BuildResult(False, req.version, -1, 0, f"sources non modifiées : {exc}")
    return await compile_ea(cfg, runner=runner)
