"""Bot 3.18.0 : écriture d'une clé dans .env depuis Telegram (ex. BACKUP_TOKEN), fins de ligne et autres lignes conservées."""
from __future__ import annotations

import os
import re
from pathlib import Path


def set_key(path: Path, key: str, value: str) -> None:
    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key) or any(c in value for c in "\r\n\x00"):
        raise ValueError("clé ou valeur invalide")
    path = Path(path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raw = b""
    bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
    text = raw[len(bom):].decode("utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    out, done = [], False
    for line in lines:
        if re.match(rf"\s*#?\s*{key}\s*=", line):
            if not done:
                out.append(f"{key}={value}")
                done = True
            continue
        out.append(line)
    if not done:
        out.append(f"{key}={value}")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(bom + (nl.join(out) + nl).encode("utf-8"))
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)
