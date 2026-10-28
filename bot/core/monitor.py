"""Surveillance externe : le bot « pingue » une URL (healthchecks.io ou équivalent) toutes les minutes.
Si les pings s'arrêtent (VPS éteint, bot planté, réseau coupé), le service t'alerte (e-mail, Telegram, application…)."""
from __future__ import annotations

import logging

from core.config import Config
from core.github import Http, http_get

log = logging.getLogger(__name__)


def ping(cfg: Config, http: Http | None = None, fail: bool = False) -> bool:
    """Envoie un ping (ou un signal d'échec). False si l'URL n'est pas configurée ou injoignable."""
    url = cfg.healthcheck_url
    if not url.startswith("https://"):
        return False
    try:
        code = (http or http_get)(url.rstrip("/") + ("/fail" if fail else ""), {"User-Agent": "scalpx-bot"})[0]
        return 200 <= code < 300
    except OSError as exc:
        log.debug("ping de surveillance impossible : %s", exc)
        return False
