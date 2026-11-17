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

import base64
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
    """Écrit sauvegarde.tar.gz dans le dépôt de sauvegarde ('' si OK, sinon l'erreur)."""
    from core import ghbackup
    rp = ghbackup.repo(cfg)
    if not rp:
        return "sauvegarde GitHub non configurée (BACKUP_TOKEN)"
    if len(data) > MAX_BYTES:
        return f"sauvegarde trop grosse ({len(data) / 2**20:.0f} Mo)"
    call = http or (lambda m, u, b=None: ghbackup._req(cfg, m, u, b))
    url = f"{ghbackup.API}/repos/{rp}/contents/{PATH}"
    try:
        code, cur = call("GET", url)
        if code not in (200, 404):
            return ghbackup.explain(code)
        body = {"message": f"Sauvegarde complète ({socket.gethostname()})", "content": base64.b64encode(data).decode("ascii")}
        if code == 200 and cur.get("sha"):
            body["sha"] = cur["sha"]
        code, _ = call("PUT", url, body)
        return "" if code in (200, 201) else ghbackup.explain(code)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return f"GitHub injoignable : {exc}"


def fetch(cfg: Config, http=None) -> tuple[bytes, str]:
    """(archive, erreur) : contenu brut (Accept raw : marche au-delà de 1 Mo)."""
    from core import ghbackup
    rp = ghbackup.repo(cfg)
    if not rp:
        return b"", "sauvegarde GitHub non configurée (BACKUP_TOKEN)"
    url = f"{ghbackup.API}/repos/{rp}/contents/{PATH}"
    if http is not None:
        code, cur = http("GET", url)
        return (base64.b64decode(cur.get("content", "")), "") if code == 200 else (b"", ghbackup.explain(code))
    import urllib.request
    from core.github import _ssl_context
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {cfg.backup_token}", "User-Agent": "scalpx-bot",
                                               "Accept": "application/vnd.github.raw", "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with urllib.request.urlopen(req, timeout=120, context=_ssl_context()) as r:
            return r.read(), ""
    except urllib.error.HTTPError as e:
        return b"", ("aucune sauvegarde complète dans le dépôt" if e.code == 404 else ghbackup.explain(e.code))
    except (urllib.error.URLError, OSError) as exc:
        return b"", f"GitHub injoignable : {exc}"


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
