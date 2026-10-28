"""Identifiant d'instance : strictement identique à celui de l'EA (SX_Archi.mqh, NormalizeServer).

id = <login>_<serveur normalisé> ; tout caractère hors [0-9A-Za-z] est remplacé par « _ »
(un par un, sans fusion), exactement comme l'EA, pour que les noms de fichiers concordent.
"""
from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^0-9A-Za-z]")


def normalize_server(server: str) -> str:
    """Normalise le nom du serveur (même règle que l'EA)."""
    return _NON_ALNUM.sub("_", server)


def make_id(login: int | str, server: str) -> str:
    """Construit l'identifiant unique login + serveur."""
    return f"{int(login)}_{normalize_server(server)}"


def valid_login(text: str) -> bool:
    """Un login MT5 est numérique (1 à 12 chiffres)."""
    return bool(re.fullmatch(r"\d{1,12}", text.strip()))


def valid_server(text: str) -> bool:
    """Nom de serveur MT5 : lettres, chiffres, espace, tiret, point, souligné (2 à 64 car.)."""
    return bool(re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z .\-_]{1,63}", text.strip()))
