"""Bot 4.7.0 : courtiers (listes de serveurs MT5).

Un terminal MT5 ne se connecte qu'aux serveurs présents dans son fichier Config/servers.dat. Ce fichier vient de
l'installeur MT5 DU COURTIER (celui de MetaQuotes ne connaît que ses serveurs de démonstration : les comptes d'un autre
courtier ne se connectent jamais, sans aucun message).

Le bot garde une liste par courtier dans BASE/Config/brokers/<clé>.dat ; la clé est le début du nom de serveur
(« RoboForex-Pro » → roboforex, « Exness-MT5Real8 » → exness, « ICMarketsSC-Demo » → icmarketssc). À la création d'un
compte, la liste de SON courtier est copiée dans son dossier (Config/servers.dat).

Ajouter un courtier : le bot télécharge l'installeur MT5 pour PC du courtier (lien du bouton « Télécharger pour PC » de son
site), l'installe à part dans Wine (C:\\scalpx-courtier), récupère sa liste de serveurs puis supprime ce dossier. BASE et
les comptes ne sont jamais touchés.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

TMP_DIR = "scalpx-courtier"
MAX_SETUP_BYTES = 80 * 1024 * 1024
RF_MARK = ".scalpx_serveurs_roboforex"          # Bot 4.6.2 : BASE/Config/servers.dat = liste RoboForex


def key_of(server: str) -> str:
    """Clé du courtier d'après un nom de serveur (début, jusqu'au premier séparateur)."""
    parts = re.split(r"[-\s_.]+", (server or "").strip())
    k = re.sub(r"[^a-z0-9]", "", (parts[0] if parts else "").lower())
    return k[:40]


def folder(base: Path) -> Path:
    return Path(base) / "Config" / "brokers"


def known(base: Path) -> list[str]:
    try:
        return sorted(p.stem for p in folder(base).glob("*.dat") if p.stat().st_size > 0)
    except OSError:
        return []


def has(base: Path, server: str) -> bool:
    k = key_of(server)
    return bool(k) and (folder(base) / f"{k}.dat").is_file()


def migrate(base: Path) -> list[str]:
    """Listes déjà présentes avant 4.7.0 (BASE/Config/servers.dat RoboForex, servers.dat.metaquotes) → brokers/."""
    base, done = Path(base), []
    try:
        d = folder(base)
        cfgdir = base / "Config"
        if (base / RF_MARK).exists() and (cfgdir / "servers.dat").is_file() and not (d / "roboforex.dat").exists():
            d.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cfgdir / "servers.dat", d / "roboforex.dat")
            done.append("roboforex")
        if (cfgdir / "servers.dat.metaquotes").is_file() and not (d / "metaquotes.dat").exists():
            d.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cfgdir / "servers.dat.metaquotes", d / "metaquotes.dat")
            done.append("metaquotes")
    except OSError as exc:
        log.warning("migration des listes de serveurs : %s", exc)
    return done


def apply(base: Path, server: str, inst_dir: Path) -> bool:
    """Copie la liste de serveurs du courtier de `server` dans le dossier du compte. False : courtier inconnu."""
    k = key_of(server)
    src = folder(base) / f"{k}.dat"
    if not k or not src.is_file():
        return False
    dst = Path(inst_dir) / "Config" / "servers.dat"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def missing_message(server: str) -> str:
    return (f"Courtier de « {server} » inconnu du bot : son MT5 ne connaîtrait pas ce serveur et le compte ne se "
            "connecterait jamais. 👥 Comptes → 🏦 Courtiers → ➕ Ajouter un courtier (lien de son installeur MT5 pour PC).")


def _download(url: str, dest: Path, opener=None) -> None:
    if not re.match(r"^https://[^\s]+$", url or ""):
        raise ValueError("lien invalide : il doit commencer par https://")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (ScalpX)"})
    with (opener or urllib.request.urlopen)(req, timeout=60) as r:       # redirections suivies (liens raccourcis)
        data = r.read(MAX_SETUP_BYTES + 1)
    if len(data) > MAX_SETUP_BYTES:
        raise ValueError("fichier trop gros pour un installeur MT5")
    if data[:2] != b"MZ":
        raise ValueError("ce lien ne donne pas un programme Windows (.exe) : prends le lien « Télécharger pour PC »")
    dest.write_bytes(data)


def _kill_tmp(prefix_drive: Path) -> None:
    from .build import _kill_tree
    from .procs import own_procs
    for p in own_procs("name"):
        try:
            if any(TMP_DIR in a or "scalpx-courtier-setup" in a for a in (p.cmdline() or [])):
                _kill_tree(p.pid)
        except Exception:
            continue


def add_from_installer(cfg, url: str, server: str, timeout: float = 900.0, poll: float = 5.0, runner=None,
                       downloader=None, clock=time.monotonic, sleep=time.sleep) -> tuple[bool, str]:
    """Installe le MT5 du courtier à part, garde sa liste de serveurs sous la clé de `server`. (ok, message)."""
    from . import wine
    k = key_of(server)
    if not k:
        return False, "nom de serveur vide"
    opts = cfg.wine.options()
    drive = opts.prefix_path() / "drive_c"
    tmp = drive / TMP_DIR
    setup = drive / "scalpx-courtier-setup.exe"
    shutil.rmtree(tmp, ignore_errors=True)
    t0w = time.time()
    try:
        (downloader or _download)(url, setup)
        cmd = [cfg.wine.cmd, str(setup), "/auto", f"/path:C:\\{TMP_DIR}"]
        env = {**os.environ, **wine.env(opts)}
        if runner is None:
            proc = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True)
        else:
            proc = runner(cmd, env)
        roots = [tmp, drive / "users"]
        found: Path | None = None
        t0 = clock()
        while clock() - t0 < timeout:
            sleep(poll)
            for root in roots:
                for f in root.rglob("servers.dat") if root.exists() else []:
                    try:
                        if f.stat().st_mtime >= t0w - 1 and f.stat().st_size > 0:
                            found = f
                            break
                    except OSError:
                        continue
                if found:
                    break
            if found:
                sleep(10)                                    # fin d'écriture
                break
        try:
            proc.terminate()
        except Exception:
            pass
        if runner is None:
            _kill_tmp(drive)
        if not found:
            return False, f"liste de serveurs non obtenue en {int(timeout // 60)} min (installeur bloqué ou mauvais lien)"
        d = folder(cfg.paths.base)
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(found, d / f"{k}.dat")
        return True, f"courtier « {k} » ajouté"
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return False, str(exc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            setup.unlink()
        except OSError:
            pass
