"""Génération du .ini de démarrage (clés vérifiées dans la doc MetaQuotes « Platform Start »).

Le mot de passe n'existe que dans ce fichier, supprimé dès la fin de la tentative de création.
"""
from __future__ import annotations

from pathlib import Path

from core.fsio import atomic_write, safe_remove

INI_NAME = "scalpx_start.ini"          # création : contient le mot de passe, supprimé aussitôt
RUN_INI_NAME = "scalpx_run.ini"         # lancements courants : AUCUN secret, conservé dans le dossier de l'instance


class IniError(ValueError):
    pass


def _clean(value: str, what: str) -> str:
    """Refuse les retours à la ligne (injection de clés dans le .ini)."""
    if any(c in value for c in "\r\n\x00"):
        raise IniError(f"{what} invalide")
    return value


def mask(text: str, secret: str) -> str:
    """Masque systématiquement le mot de passe dans un texte (logs, exceptions, messages)."""
    if secret and secret in text:
        return text.replace(secret, "***")
    return text


def _charts(profile: str, max_bars: int = 0) -> list[str]:
    """[Charts] : profil de démarrage et (Bot 3.10.0) nombre maximum de barres par graphique (« MaxBars » de la doc
    MetaQuotes) : moins de barres = moins de calcul pour les indicateurs et moins de RAM, sur chaque terminal."""
    keys = ([f"ProfileLast={_clean(profile, 'profil')}"] if profile else []) + ([f"MaxBars={int(max_bars)}"] if max_bars > 0 else [])
    return ["[Charts]", *keys] if keys else []


def build_ini(login: int | str, password: str, server: str, ea_name: str, set_name: str,
              symbol: str, period: str, profile: str = "", max_bars: int = 0) -> str:
    """Contenu du .ini : [Common] connexion + mémorisation, [Experts] autorisations, [StartUp] EA + .set."""
    _clean(password, "mot de passe")
    _clean(server, "serveur")
    lines = [
        "; ScalpX - configuration de démarrage temporaire (supprimée après la création)",
        "[Common]",
        f"Login={int(login)}",
        f"Password={password}",
        f"Server={server}",
        "KeepPrivate=1",               # 1 = le mot de passe est mémorisé par MT5 (pas par le bot)
        "NewsEnable=0",
        "[Experts]",
        "AllowLiveTrading=1",
        "AllowDllImport=0",
        "Enabled=1",
        "Account=0",
        "Profile=0",
        *_charts(profile, max_bars),
        "[StartUp]",
        f"Expert={ea_name}",
        f"ExpertParameters={set_name}",  # relatif à MQL5\Presets
        f"Symbol={symbol}",
        f"Period={period}",
    ]
    return "\r\n".join(lines) + "\r\n"


def build_run_ini(login: int | str, server: str, ea_name: str, set_name: str, symbol: str, period: str,
                  profile: str = "", max_bars: int = 0) -> str:
    """.ini de relance SANS mot de passe : Login + Server désignent le compte (MT5 utilise le mot de passe qu'il a mémorisé)
    et le [StartUp] rouvre le graphique de l'EA
    (un graphique ouvert par [StartUp] n'est pas conservé dans le profil : sans /config l'EA ne revient pas)."""
    lines = [
        "; ScalpX - relance (aucun mot de passe ici)",
        "[Common]",
        f"Login={int(login)}",
        f"Server={_clean(server, 'serveur')}",
        "KeepPrivate=1",
        "NewsEnable=0",
        "[Experts]",
        "AllowLiveTrading=1",
        "AllowDllImport=0",
        "Enabled=1",
        "Account=0",
        "Profile=0",
        *_charts(profile, max_bars),
        "[StartUp]",
        f"Expert={ea_name}",
        f"ExpertParameters={set_name}",
        f"Symbol={symbol}",
        f"Period={period}",
    ]
    return "\r\n".join(lines) + "\r\n"


def write_run_ini(folder: Path, content: str) -> Path:
    p = Path(folder) / RUN_INI_NAME
    try:
        if p.is_file() and p.read_text(encoding="utf-8") == content:
            return p
    except OSError:
        pass
    atomic_write(p, content)
    return p


def write_ini(folder: Path, content: str) -> Path:
    p = Path(folder) / INI_NAME
    atomic_write(p, content)
    return p


def remove_ini(folder: Path) -> bool:
    """Supprime le .ini dans tous les cas (succès ou échec)."""
    return safe_remove(Path(folder) / INI_NAME)
