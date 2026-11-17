"""Bot 3.17.0 : ☁️ sauvegarde automatique des presets sur GitHub.

Dépôt privé DÉDIÉ (par défaut « <propriétaire du dépôt du code>/scalpx-sauvegarde »), jeton BACKUP_TOKEN dans .env avec
le droit d'écriture sur CE dépôt seulement (le dépôt du code reste en lecture seule pour les VPS).
- Toutes les 5 min : si les presets ont changé (toi, l'optimiseur, un import), `presets.txt` est réécrit dans le dépôt
  (format de 📤 Exporter : se réimporte tel quel). Chaque version reste dans l'historique de GitHub.
- 💾 Presets → ☁️ Restaurer depuis GitHub : dernière version importée (presets du même nom remplacés après confirmation).
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
DEFAULT_NAME = "scalpx-sauvegarde"


def repo(cfg: Config) -> str:
    """Dépôt de sauvegarde ('' = sauvegarde désactivée : pas de jeton)."""
    if not cfg.backup_token:
        return ""
    if cfg.backup.github:
        return cfg.backup.github.strip()
    owner = (cfg.update.github or "").split("/")[0].strip()
    return f"{owner}/{DEFAULT_NAME}" if owner else ""


def _req(cfg: Config, method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {cfg.backup_token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "scalpx-bot"})
    from core.github import _ssl_context
    try:
        with urllib.request.urlopen(r, timeout=30, context=_ssl_context()) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def explain(code: int) -> str:
    return {401: "jeton BACKUP_TOKEN refusé (expiré ou faux)",
            403: "jeton sans droit d'écriture sur le dépôt de sauvegarde (Contents : Read and write)",
            404: "dépôt de sauvegarde introuvable (crée-le, privé, et donne l'accès au jeton)"}.get(code, f"erreur GitHub {code}")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def push(cfg: Config, text: str, http=None) -> str:
    """Écrit presets.txt dans le dépôt. Renvoie '' si OK, sinon le message d'erreur."""
    rp = repo(cfg)
    if not rp:
        return "sauvegarde GitHub non configurée"
    call = http or (lambda m, u, b=None: _req(cfg, m, u, b))
    url = f"{API}/repos/{rp}/contents/{PATH}"
    try:
        code, cur = call("GET", url)
        if code not in (200, 404):
            return explain(code)
        body = {"message": f"Presets : sauvegarde automatique ({socket.gethostname()})",
                "content": base64.b64encode(text.encode("utf-8")).decode("ascii")}
        if code == 200 and cur.get("sha"):
            body["sha"] = cur["sha"]
        code, _ = call("PUT", url, body)
        return "" if code in (200, 201) else explain(code)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return f"GitHub injoignable : {exc}"


def fetch(cfg: Config, http=None) -> tuple[str, str]:
    """(texte de presets.txt, erreur)."""
    rp = repo(cfg)
    if not rp:
        return "", "sauvegarde GitHub non configurée (BACKUP_TOKEN dans .env)"
    call = http or (lambda m, u, b=None: _req(cfg, m, u, b))
    try:
        code, cur = call("GET", f"{API}/repos/{rp}/contents/{PATH}")
        if code == 404:
            return "", "aucune sauvegarde dans le dépôt (ou dépôt introuvable)"
        if code != 200:
            return "", explain(code)
        return base64.b64decode(cur.get("content", "")).decode("utf-8"), ""
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return "", f"GitHub injoignable : {exc}"
