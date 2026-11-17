"""Bot 4.2.0 : chiffrement des sauvegardes (AES-256-GCM, clé tirée d'un mot de passe par scrypt).

Fichier chiffré = MAGIC + sel (16 octets) + nonce (12 octets) + données chiffrées (authentifiées).
Le bot garde dans .env la clé déjà dérivée (BACKUP_KEY = base64(sel + clé)) pour chiffrer seul chaque nuit ; le mot de
passe lui-même n'est jamais enregistré. Sur un nouveau serveur, le mot de passe (et le sel lu dans le fichier) suffit.
Sans le mot de passe, une sauvegarde volée (GitHub piraté, fichier transféré) est illisible.
"""
from __future__ import annotations

import base64
import hashlib
import os

MAGIC = b"SCALPX-CHIFFRE-1\n"
_SALT, _NONCE = 16, 12


def derive(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 2**20, dklen=32)


def new_key(password: str) -> str:
    """Mot de passe → valeur de BACKUP_KEY (sel + clé, base64)."""
    salt = os.urandom(_SALT)
    return base64.b64encode(salt + derive(password, salt)).decode("ascii")


def _split(key_b64: str) -> tuple[bytes, bytes]:
    raw = base64.b64decode(key_b64)
    if len(raw) != _SALT + 32:
        raise ValueError("clé de chiffrement invalide")
    return raw[:_SALT], raw[_SALT:]


def is_encrypted(blob: bytes) -> bool:
    return blob.startswith(MAGIC)


def encrypt(data: bytes, key_b64: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, key = _split(key_b64)
    nonce = os.urandom(_NONCE)
    return MAGIC + salt + nonce + AESGCM(key).encrypt(nonce, data, MAGIC)


def decrypt(blob: bytes, key_b64: str = "", password: str = "") -> bytes:
    """Clé de ce serveur (même sel) ou mot de passe. ValueError : « mot de passe requis » / « mot de passe incorrect »."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not is_encrypted(blob):
        return blob
    body = blob[len(MAGIC):]
    salt, nonce, ct = body[:_SALT], body[_SALT:_SALT + _NONCE], body[_SALT + _NONCE:]
    keys = []
    if key_b64:
        try:
            ks, kk = _split(key_b64)
            if ks == salt:
                keys.append(kk)
        except ValueError:
            pass
    if password:
        keys.append(derive(password, salt))
    if not keys:
        raise ValueError("mot de passe requis")
    for k in keys:
        try:
            return AESGCM(k).decrypt(nonce, ct, MAGIC)
        except InvalidTag:
            continue
    raise ValueError("mot de passe incorrect")
