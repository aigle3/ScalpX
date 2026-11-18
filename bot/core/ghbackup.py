"""☁️ Sauvegarde sur GitHub, propre à chaque utilisateur (Bot 4.2.0).

Le code de ScalpX est public ; la sauvegarde va dans un dépôt PRIVÉ du compte GitHub de l'utilisateur
(`<son login>/scalpx-sauvegarde`), avec SON jeton (BACKUP_TOKEN) et, s'il le veut, chiffrée par son mot de passe
(BACKUP_KEY, voir core.crypt). Activée depuis Telegram : le bot crée lui-même le dépôt si le jeton le permet.
- presets (format de 📤 Exporter) : `presets.txt`, ou `presets.enc` chiffré ; réécrits dès qu'ils changent ;
- sauvegarde complète (core.fullbackup) : `sauvegarde.tar.gz`, ou `sauvegarde.enc` chiffrée ; chaque nuit.
Chaque version reste dans l'historique du dépôt.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import socket
import urllib.error
import urllib.request

from core.config import Config

log = logging.getLogger(__name__)
API = "https://api.github.com"
PATH = "presets.txt"
PATH_ENC = "presets.enc"
DEFAULT_NAME = "scalpx-sauvegarde"


def repo(cfg: Config) -> str:
    """Dépôt de sauvegarde ('' = sauvegarde non activée)."""
    if not cfg.backup_token:
        return ""
    return (cfg.backup.github or cfg.backup_repo or "").strip()


def _req(cfg: Config, method: str, url: str, body: dict | None = None, raw: bool = False) -> tuple[int, dict | bytes]:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {cfg.backup_token}", "User-Agent": "scalpx-bot", "X-GitHub-Api-Version": "2022-11-28",
        "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json"})
    from core.github import _ssl_context
    try:
        with urllib.request.urlopen(r, timeout=120, context=_ssl_context()) as resp:
            content = resp.read()
            return resp.status, (content if raw else json.loads(content or b"{}"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, (b"" if raw else json.loads(e.read() or b"{}"))
        except ValueError:
            return e.code, (b"" if raw else {})


def explain(code: int) -> str:
    return {401: "jeton BACKUP_TOKEN refusé (expiré ou faux)",
            403: "jeton sans droit d'écriture sur le dépôt de sauvegarde (Contents : Read and write)",
            404: "dépôt de sauvegarde introuvable (crée-le, privé, et donne l'accès au jeton)"}.get(code, f"erreur GitHub {code}")


def digest(data: str | bytes) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()[:16]


def ensure_repo(cfg: Config, http=None) -> tuple[str, str]:
    """(dépôt, erreur) : dépôt PRIVÉ de sauvegarde du compte du jeton. Essaie `scalpx-sauvegarde`, puis
    `scalpx-sauvegarde-2` … `-6` : un dépôt PRIVÉ existant est repris (sauvegarde d'une installation précédente), un nom
    déjà pris par un dépôt public ou refusé par GitHub (nom récemment supprimé, réservé…) passe au suivant, sinon il est
    créé. Jamais d'exception : toute impossibilité revient en message d'erreur."""
    call = http or (lambda m, u, b=None: _req(cfg, m, u, b))
    try:
        if cfg.backup.github:                                   # dépôt imposé dans config.toml
            name = cfg.backup.github.strip()
            code, info = call("GET", f"{API}/repos/{name}")
            if code != 200:
                return "", explain(code)
            return (name, "") if info.get("private", True) else ("", f"le dépôt {name} est public : passe-le en privé")
        code, me = call("GET", f"{API}/user")
        if code != 200 or not isinstance(me, dict) or not me.get("login"):
            return "", explain(code if code != 200 else 401)
        login = me["login"]
        cant_create = False
        for i in range(1, 7):
            name = f"{login}/{DEFAULT_NAME}" + (f"-{i}" if i > 1 else "")
            code, info = call("GET", f"{API}/repos/{name}")
            if code == 200:
                if isinstance(info, dict) and info.get("private", False):
                    return name, ""
                continue                                        # nom pris par un dépôt public : jamais utilisé
            if code != 404:
                return "", explain(code)
            if cant_create:
                continue
            code, _ = call("POST", f"{API}/user/repos", {"name": name.split("/", 1)[1], "private": True, "auto_init": True,
                                                        "description": "Sauvegarde privée de ScalpX (créée par le bot)"})
            if code in (200, 201):
                log.info("dépôt de sauvegarde %s créé (privé)", name)
                return name, ""
            if code in (403, 404):                              # jeton limité à un dépôt : pas le droit d'en créer
                cant_create = True
                continue
            # 422 et autres : nom indisponible (récemment supprimé, réservé…) → nom suivant
        if cant_create:
            return "", (f"le jeton ne peut pas créer de dépôt : crée toi-même un dépôt PRIVÉ « {DEFAULT_NAME} » sur GitHub, "
                        "donne-lui l'accès au jeton (Contents : Read and write), puis renvoie le jeton")
        return "", f"aucun nom de dépôt disponible ({DEFAULT_NAME} à {DEFAULT_NAME}-6)"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return "", f"GitHub injoignable : {exc}"


def put(cfg: Config, path: str, data: bytes, message: str, http=None) -> str:
    """Écrit un fichier du dépôt de sauvegarde ('' si OK, sinon l'erreur)."""
    rp = repo(cfg)
    if not rp:
        return "sauvegarde GitHub non activée (🛠 Système → ☁️ Sauvegarde)"
    call = http or (lambda m, u, b=None: _req(cfg, m, u, b))
    url = f"{API}/repos/{rp}/contents/{path}"
    try:
        code, cur = call("GET", url)
        if code not in (200, 404):
            return explain(code)
        body = {"message": f"{message} ({socket.gethostname()})", "content": base64.b64encode(data).decode("ascii")}
        if code == 200 and isinstance(cur, dict) and cur.get("sha"):
            body["sha"] = cur["sha"]
        code, _ = call("PUT", url, body)
        return "" if code in (200, 201) else explain(code)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return f"GitHub injoignable : {exc}"


def get(cfg: Config, path: str, http=None) -> tuple[bytes, str, int]:
    """(contenu, erreur, code HTTP) d'un fichier du dépôt de sauvegarde (contenu brut : marche au-delà de 1 Mo)."""
    rp = repo(cfg)
    if not rp:
        return b"", "sauvegarde GitHub non activée (🛠 Système → ☁️ Sauvegarde)", 0
    url = f"{API}/repos/{rp}/contents/{path}"
    try:
        if http is not None:
            code, cur = http("GET", url)
            return ((base64.b64decode(cur.get("content", "")), "", 200) if code == 200 else (b"", explain(code), code))
        code, content = _req(cfg, "GET", url, raw=True)
        return (content, "", 200) if code == 200 else (b"", explain(code), code)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return b"", f"GitHub injoignable : {exc}", 0


BACKUP_FILES = ("sauvegarde.enc", "sauvegarde.tar.gz", "presets.enc", "presets.txt")


def existing(cfg: Config, http=None) -> tuple[set[str], str]:
    """Bot 4.4.0 : ({fichiers de sauvegarde déjà présents}, erreur). Dépôt vide (aucun commit) → ensemble vide."""
    rp = repo(cfg)
    if not rp:
        return set(), "sauvegarde GitHub non activée (🛠 Système → ☁️ Sauvegarde)"
    call = http or (lambda m, u, b=None: _req(cfg, m, u, b))
    try:
        code, cur = call("GET", f"{API}/repos/{rp}/contents/")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return set(), f"GitHub injoignable : {exc}"
    if code == 404:
        return set(), ""
    if code != 200:
        return set(), explain(code)
    names = {str(x.get("name", "")) for x in cur} if isinstance(cur, list) else set()
    return names & set(BACKUP_FILES), ""


def get_first(cfg: Config, paths: tuple[str, ...], http=None) -> tuple[bytes, str]:
    """Premier fichier présent parmi `paths` (version chiffrée d'abord)."""
    last = ""
    for p in paths:
        data, err, code = get(cfg, p, http)
        if not err:
            return data, ""
        if code != 404:
            return b"", err
        last = err
    return b"", ("aucune sauvegarde dans le dépôt" if last.startswith("dépôt de sauvegarde introuvable") else last)


def push(cfg: Config, text: str, http=None) -> str:
    """Presets → `presets.enc` (chiffrés si BACKUP_KEY) ou `presets.txt`."""
    data = text.encode("utf-8")
    if cfg.backup_key:
        from core import crypt
        return put(cfg, PATH_ENC, crypt.encrypt(data, cfg.backup_key), "Presets (chiffrés)", http)
    return put(cfg, PATH, data, "Presets", http)


def fetch(cfg: Config, http=None) -> tuple[bytes, str]:
    """Dernière sauvegarde des presets, BRUTE (chiffrée ou non : 📥 Importer s'en charge)."""
    return get_first(cfg, (PATH_ENC, PATH), http)
