"""Téléchargement du paquet depuis un dépôt GitHub privé (lecture seule).

Toutes les check_s secondes : dernier commit de la branche (API GitHub, jeton GITHUB_TOKEN de .env).
S'il a changé : archive zip de ce commit téléchargée et extraite dans [update] source (remplacement atomique),
avec .commit.json (sha, message, date). core.update compare ensuite ce paquet à l'installation.
"""
from __future__ import annotations

import io
import json
import logging
import shutil
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import urllib.error
import urllib.request

from core.config import Config

log = logging.getLogger(__name__)
API = "https://api.github.com"


@dataclass
class FetchResult:
    ok: bool
    changed: bool = False
    sha: str = ""
    message: str = ""
    error: str = ""


def _headers(cfg: Config) -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "scalpx-bot"}
    if cfg.github_token:
        h["Authorization"] = f"Bearer {cfg.github_token}"
    return h


def local_sha(dest: Path) -> str:
    try:
        return json.loads((dest / ".commit.json").read_text(encoding="utf-8")).get("sha", "")
    except (OSError, ValueError):
        return ""


def _explain(code: int) -> str:
    return {401: "jeton GitHub refusé (expiré ou faux : GITHUB_TOKEN dans .env)",
            403: "accès GitHub refusé (droits du jeton ou limite de requêtes)",
            404: "dépôt ou branche introuvable (ou jeton sans accès à ce dépôt)"}.get(code, f"erreur GitHub {code}")


def extract_zip(data: bytes, dest: Path, info: dict) -> None:
    """Extrait l'archive GitHub (dossier racine « owner-repo-sha/ » retiré) puis remplace dest d'un coup."""
    dest = Path(dest)
    tmp = dest.with_name(dest.name + ".new")
    old = dest.with_name(dest.name + ".old")
    for d in (tmp, old):
        shutil.rmtree(d, ignore_errors=True)
    tmp.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for m in z.infolist():
            parts = m.filename.split("/", 1)
            if len(parts) < 2 or not parts[1] or m.is_dir():
                continue
            target = (tmp / parts[1]).resolve()
            if not str(target).startswith(str(tmp.resolve())):        # chemin hors du dossier : ignoré
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(m) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
    (tmp / ".commit.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    if dest.exists():
        dest.rename(old)
    tmp.rename(dest)
    shutil.rmtree(old, ignore_errors=True)


Http = Callable[[str, dict], tuple[int, bytes]]


def _ssl_context():
    """Certificats de certifi (installé avec python-telegram-bot) : le magasin de Windows Server 2016 peut être incomplet."""
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def http_get(url: str, headers: dict) -> tuple[int, bytes]:
    """GET (redirections suivies). Renvoie (code HTTP, contenu)."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


def fetch(cfg: Config, http: Http | None = None) -> FetchResult:
    """Met à jour la copie locale du paquet si la branche a un nouveau commit."""
    get = http or http_get
    repo, branch = cfg.update.github.strip(), cfg.update.branch or "main"
    if not repo:
        return FetchResult(False, error="aucun dépôt GitHub configuré")
    dest = Path(cfg.update.source)
    try:
        code, body = get(f"{API}/repos/{repo}/commits/{branch}", _headers(cfg))
        if code != 200:
            return FetchResult(False, error=_explain(code))
        j = json.loads(body.decode("utf-8"))
        sha = j.get("sha", "")
        msg = (j.get("commit", {}).get("message") or "").strip()
        if sha and sha == local_sha(dest):
            return FetchResult(True, False, sha, msg)
        code, data = get(f"{API}/repos/{repo}/zipball/{sha}", _headers(cfg))
        if code != 200:
            return FetchResult(False, error=_explain(code))
        info = {"sha": sha, "message": msg, "date": j.get("commit", {}).get("committer", {}).get("date", ""),
                "fetched": int(time.time())}
        extract_zip(data, dest, info)
        log.info("paquet GitHub %s@%s téléchargé : %s", repo, sha[:7], msg.splitlines()[0] if msg else "")
        return FetchResult(True, True, sha, msg)
    except (urllib.error.URLError, OSError, zipfile.BadZipFile, ValueError) as exc:
        return FetchResult(False, error=f"GitHub injoignable : {exc}")
