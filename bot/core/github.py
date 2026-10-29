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
    token_expires: str = ""                     # date d'expiration du jeton (AAAA-MM-JJ), si GitHub l'indique
    waiting: bool = False                       # nouveau commit vu, en attente du délai de stabilité
    remaining: int = 0                          # publications suivantes, proposées ensuite une par une


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


def token_expiry(headers: dict) -> str:
    """En-tête « github-authentication-token-expiration: 2027-10-28 12:00:00 UTC » → « 2027-10-28 »."""
    v = (headers or {}).get("github-authentication-token-expiration", "")
    return v[:10] if len(v) >= 10 and v[4] == "-" else ""


def days_left(expires: str, now: float | None = None) -> int | None:
    import datetime as dt
    try:
        d = dt.date.fromisoformat(expires)
    except ValueError:
        return None
    today = dt.date.fromtimestamp(now or time.time())
    return (d - today).days


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
    root = tmp.resolve()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for m in z.infolist():
            parts = m.filename.split("/", 1)
            if len(parts) < 2 or not parts[1] or m.is_dir():
                continue
            target = (tmp / parts[1]).resolve()
            if root not in target.parents:                            # chemin hors du dossier (même « release.new2\… ») : ignoré
                log.warning("paquet GitHub : chemin refusé %r", m.filename)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(m) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
    (tmp / ".commit.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    if dest.exists():
        dest.rename(old)
    tmp.rename(dest)
    shutil.rmtree(old, ignore_errors=True)


Http = Callable[[str, dict], tuple]           # (code, contenu) ou (code, contenu, en-têtes)


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
            return r.status, r.read(), {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        return e.code, b"", {k.lower(): v for k, v in (e.headers or {}).items()}


_SEEN = {"sha": "", "since": 0.0}             # dernier commit vu sur la branche et depuis quand


def fetch(cfg: Config, http: Http | None = None, force: bool = False, now: float | None = None,
          installed: str = "", latest: bool = False) -> FetchResult:
    """Met à jour la copie locale du paquet si la branche a un nouveau commit, resté inchangé depuis
    [update] settle_min minutes (évite de proposer une publication en cours). force=True : sans attente.

    Plusieurs publications d'affilée : elles sont proposées DANS L'ORDRE, une par une. `installed` = commit installé ;
    le paquet téléchargé est la publication qui le suit, et rien de plus n'est téléchargé tant qu'il n'est pas installé.
    latest=True (bouton « ⏩ Tout installer ») : la DERNIÈRE publication directement (elle contient toutes les précédentes)."""
    get = http or http_get
    repo = cfg.update.github.strip()
    branch = "stable" if cfg.update.validated_only else (cfg.update.branch or "main")
    if not repo:
        return FetchResult(False, error="aucun dépôt GitHub configuré")
    dest = Path(cfg.update.source)
    try:
        res = get(f"{API}/repos/{repo}/commits/{branch}", _headers(cfg))
        code, body = res[0], res[1]
        expires = token_expiry(res[2] if len(res) > 2 else {})
        if code != 200:
            return FetchResult(False, error=_explain(code), token_expires=expires)
        j = json.loads(body.decode("utf-8"))
        sha = j.get("sha", "")
        msg = (j.get("commit", {}).get("message") or "").strip()
        local = local_sha(dest)
        if sha and sha == local:
            return FetchResult(True, False, sha, msg, token_expires=expires)
        if installed and local and not local.startswith(installed) and not latest:   # paquet précédent pas encore installé : on attend
            return FetchResult(True, False, local, "", token_expires=expires)
        t = now if now is not None else time.time()
        if sha != _SEEN["sha"]:
            _SEEN["sha"], _SEEN["since"] = sha, t
        if not force and t - _SEEN["since"] < cfg.update.settle_min * 60:
            return FetchResult(True, False, sha, msg, token_expires=expires, waiting=True)
        remaining = 0
        if installed and not latest:                                       # publication suivante seulement
            rc = get(f"{API}/repos/{repo}/compare/{installed}...{sha}", _headers(cfg))
            if rc[0] == 200:
                cj = json.loads(rc[1].decode("utf-8"))
                commits = cj.get("commits") or []
                if cj.get("status") == "ahead" and commits:
                    nxt = commits[0]
                    remaining = len(commits) - 1
                    sha = nxt.get("sha", sha)
                    msg = (nxt.get("commit", {}).get("message") or "").strip()
                    j = nxt
        res = get(f"{API}/repos/{repo}/zipball/{sha}", _headers(cfg))
        code, data = res[0], res[1]
        if code != 200:
            return FetchResult(False, error=_explain(code), token_expires=expires)
        info = {"sha": sha, "message": msg, "date": j.get("commit", {}).get("committer", {}).get("date", ""),
                "fetched": int(time.time()), "remaining": remaining}
        extract_zip(data, dest, info)
        log.info("paquet GitHub %s@%s téléchargé : %s", repo, sha[:7], msg.splitlines()[0] if msg else "")
        return FetchResult(True, True, sha, msg, token_expires=expires, remaining=remaining)
    except (urllib.error.URLError, OSError, zipfile.BadZipFile, ValueError) as exc:
        return FetchResult(False, error=f"GitHub injoignable : {exc}")
