"""Dossier BASE : lecture seule. Contrôles de santé, version, copie, empreinte."""
from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

from core.config import Config


_VER_RE = re.compile(r'#define\s+SX_VERSION\s+"([^"]+)"')


def read_version(base: Path) -> str:
    """Version de l'EA de BASE : SX_VERSION de SX_Core.mqh (ce qui est compilé), sinon BASE\\version.txt ('' si rien)."""
    try:
        raw = (Path(base) / "MQL5" / "Experts" / "SX_Core.mqh").read_bytes()
        m = _VER_RE.search(_decode(raw.removeprefix(b"\xef\xbb\xbf")))
        if m:
            return m.group(1).strip()
    except OSError:
        pass
    try:
        return (Path(base) / "version.txt").read_text(encoding="utf-8-sig").strip()
    except OSError:
        return ""


def _decode(raw: bytes) -> str:
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="ignore")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="ignore")


def chart_has_expert(chr_path: Path) -> bool:
    """Un fichier .chr contient une section <expert> si un EA est attaché au graphique."""
    try:
        txt = _decode(Path(chr_path).read_bytes())
    except OSError:
        return False
    return "<expert>" in txt.lower()


def charts_with_ea(base: Path) -> list[Path]:
    """Graphiques de BASE qui portent un EA (le profil doit être vierge de tout EA)."""
    out: list[Path] = []
    for root in (Path(base) / "Profiles" / "Charts", Path(base) / "MQL5" / "Profiles" / "Charts"):
        if root.is_dir():
            for p in root.rglob("*.chr"):
                if chart_has_expert(p):
                    out.append(p)
    return out


def check_base(cfg: Config) -> list[str]:
    """Liste des problèmes bloquants de BASE (vide = sain)."""
    base = cfg.paths.base
    problems: list[str] = []
    if not base.is_dir():
        return [f"Dossier BASE introuvable : {base}"]
    if not (base / cfg.mt5.terminal_exe).is_file():
        problems.append(f"{cfg.mt5.terminal_exe} absent de BASE")
    if not (base / "MQL5" / "Experts" / cfg.mt5.ea_file).is_file():
        problems.append(f"EA {cfg.mt5.ea_file} absent de MQL5\\Experts")
    if not (base / "MQL5" / "Presets" / cfg.mt5.set_name).is_file():
        problems.append(f"{cfg.mt5.set_name} absent de MQL5\\Presets")
    bad = charts_with_ea(base)
    if bad:
        problems.append(f"Le profil BASE contient {len(bad)} graphique(s) avec un EA attaché "
                        f"(risque d'EA en double) : retire l'EA de BASE")
    return problems


def free_disk_gb(path: Path) -> float:
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    return shutil.disk_usage(p).free / 1e9


def folder_size_gb(path: Path) -> float:
    total = 0
    for f in Path(path).rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total / 1e9


def copy_base(base: Path, dest: Path) -> None:
    """Copie BASE → dest (BASE n'est jamais modifié). dest ne doit pas exister."""
    if Path(dest).exists():
        raise FileExistsError(str(dest))
    shutil.copytree(base, dest, symlinks=False)


def tree_hash(folder: Path) -> str:
    """Empreinte SHA-256 d'un dossier (chemins relatifs + contenu) : test « BASE inchangé »."""
    h = hashlib.sha256()
    root = Path(folder)
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).replace("\\", "/").encode())
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
    return h.hexdigest()


def file_hash(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def set_keys(set_path: Path) -> list[str]:
    """Noms des paramètres d'un fichier .set (UTF-16 ou UTF-8), dans l'ordre : détection d'inputs modifiés."""
    try:
        txt = _decode(Path(set_path).read_bytes())
    except OSError:
        return []
    keys: list[str] = []
    for line in txt.splitlines():
        line = line.strip().lstrip("﻿")
        if not line or line.startswith(";") or "=" not in line:
            continue
        keys.append(line.split("=", 1)[0].strip())
    return keys


_HASH_CACHE: dict[str, tuple[int, int, str]] = {}


def cached_hash(p: Path) -> str:
    """Empreinte d'un fichier, recalculée seulement si sa taille ou sa date change ('' si absent)."""
    try:
        st = Path(p).stat()
    except OSError:
        return ""
    key = str(p)
    c = _HASH_CACHE.get(key)
    if c and c[0] == st.st_mtime_ns and c[1] == st.st_size:
        return c[2]
    h = file_hash(p)
    _HASH_CACHE[key] = (st.st_mtime_ns, st.st_size, h)
    return h
