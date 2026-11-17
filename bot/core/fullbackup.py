"""Bot 3.21.0 : ☁️ sauvegarde COMPLÈTE sur GitHub (dépôt de sauvegarde, jeton BACKUP_TOKEN) et restauration.

`sauvegarde.tar.gz` dans le dépôt : base du bot (historique des comptes, soldes, cycles, retraits, déploiements, état de
l'optimiseur, journal), presets (dont Martin et la base de l'optimiseur) et manifeste. Jamais de secret : ni .env, ni mot
de passe (MT5 garde les siens ; sur un serveur neuf, les comptes se recréent avec 📄).
- envoyée chaque jour (heure de [backup] hour) et sur demande (☁️ Sauvegarder maintenant) ;
- restauration : archive téléchargée et vérifiée, posée dans data/restauration.tar.gz, puis le bot redémarre ;
  au démarrage, AVANT d'ouvrir la base, apply_pending() remplace la base et les presets (anciens gardés à côté) ;
  les comptes dont le dossier MT5 n'existe pas sur ce serveur sont archivés (historique gardé : 📄 / ➕ les réactivent).
"""
from __future__ import annotations

import io
import json
import logging
import shutil
import socket
import sqlite3
import tarfile
import tempfile
import time
import urllib.error
from pathlib import Path

from core.config import Config

log = logging.getLogger(__name__)
PATH = "sauvegarde.tar.gz"
PATH_ENC = "sauvegarde.enc"
PENDING = "restauration.tar.gz"
MAX_BYTES = 45 * 2**20                      # API GitHub : fichiers de 100 Mo au plus, base64 compris
MEMBERS = ("scalpx.db", "presets.json", "manifeste.json")


def build(db, data_dir: Path, version: str = "") -> bytes:
    """Archive tar.gz en mémoire : copie cohérente de la base (API de sauvegarde SQLite), presets, manifeste."""
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory() as td:
        snap = Path(td) / "scalpx.db"
        db.backup_to(snap)
        man = {"version_bot": version, "date": int(time.time()), "serveur": socket.gethostname()}
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            tar.add(snap, arcname="scalpx.db")
            pres = Path(data_dir) / "presets.json"
            if pres.is_file():
                tar.add(pres, arcname="presets.json")
            raw = json.dumps(man, ensure_ascii=False).encode()
            ti = tarfile.TarInfo("manifeste.json")
            ti.size, ti.mtime = len(raw), int(time.time())
            tar.addfile(ti, io.BytesIO(raw))
    return buf.getvalue()


def check(data: bytes) -> dict:
    """Contrôle d'une archive (avant de la poser) : base SQLite lisible ; renvoie le manifeste. ValueError sinon."""
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            names = tar.getnames()
            if "scalpx.db" not in names or any(n not in MEMBERS for n in names):
                raise ValueError("archive inattendue")
            man = json.loads(tar.extractfile("manifeste.json").read()) if "manifeste.json" in names else {}
            dbb = tar.extractfile("scalpx.db").read()
    except (tarfile.TarError, OSError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError(f"archive illisible : {exc}") from exc
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "x.db"
        p.write_bytes(dbb)
        try:
            con = sqlite3.connect(p)
            ok = con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            n = con.execute("SELECT COUNT(*) FROM instances").fetchone()[0]
            con.close()
        except sqlite3.Error as exc:
            raise ValueError(f"base illisible : {exc}") from exc
    if not ok:
        raise ValueError("base endommagée")
    man["comptes"] = n
    return man


def push(cfg: Config, data: bytes, http=None) -> str:
    """Archive → `sauvegarde.enc` (chiffrée si BACKUP_KEY) ou `sauvegarde.tar.gz` ('' si OK, sinon l'erreur)."""
    from core import ghbackup
    if len(data) > MAX_BYTES:
        return f"sauvegarde trop grosse ({len(data) / 2**20:.0f} Mo)"
    if cfg.backup_key:
        from core import crypt
        return ghbackup.put(cfg, PATH_ENC, crypt.encrypt(data, cfg.backup_key), "Sauvegarde complète (chiffrée)", http)
    return ghbackup.put(cfg, PATH, data, "Sauvegarde complète", http)


def fetch(cfg: Config, http=None) -> tuple[bytes, str]:
    """(archive BRUTE, chiffrée ou non, erreur)."""
    from core import ghbackup
    return ghbackup.get_first(cfg, (PATH_ENC, PATH), http)


def stage(data_dir: Path, data: bytes) -> Path:
    """Archive vérifiée posée pour le prochain démarrage."""
    check(data)
    p = Path(data_dir) / PENDING
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(p)
    return p


def apply_pending(data_dir: Path) -> str:
    """Au démarrage, base FERMÉE : restauration posée → base et presets remplacés (anciens gardés en *.avant_restauration).
    Renvoie un résumé ('' si rien à faire)."""
    data_dir = Path(data_dir)
    p = data_dir / PENDING
    if not p.is_file():
        return ""
    try:
        data = p.read_bytes()
        man = check(data)
        stamp = time.strftime("%Y%m%d_%H%M")
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            for name in ("scalpx.db", "presets.json"):
                if name not in tar.getnames():
                    continue
                dest = data_dir / name
                if dest.exists():
                    shutil.copy2(dest, data_dir / f"{name}.avant_restauration_{stamp}")
                for side in (data_dir / f"{name}-wal", data_dir / f"{name}-shm"):
                    side.unlink(missing_ok=True)
                tmp = dest.with_suffix(dest.suffix + ".tmp")
                tmp.write_bytes(tar.extractfile(name).read())
                tmp.replace(dest)
        p.unlink()
        when = time.strftime("%d/%m/%Y %H:%M", time.localtime(int(man.get("date", 0)))) if man.get("date") else "?"
        msg = f"sauvegarde du {when} ({man.get('serveur', '?')}, bot {man.get('version_bot', '?')}, {man.get('comptes', 0)} compte(s))"
        log.warning("restauration appliquée : %s", msg)
        return msg
    except Exception as exc:
        log.error("restauration impossible : %s", exc)
        p.replace(data_dir / f"{PENDING}.refusee")
        return f"ÉCHEC : {exc}"


def archive_missing(db, instances_dir: Path) -> list[str]:
    """Après restauration : comptes sans dossier MT5 sur ce serveur → archivés (historique gardé, réactivés à la création)."""
    gone = []
    for inst in db.list():
        if not Path(inst.folder).is_dir() and not (Path(instances_dir) / inst.id).is_dir():
            db.archive(inst.id)
            gone.append(inst.id)
    return gone
