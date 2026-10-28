"""Presets centraux : un seul jeu de réglages de l'EA, gardé par le bot (data/presets.json), appliqué à tous les robots.

Valeurs « brutes » = format attendu par la commande SET de l'EA : 1/0 (oui/non), index (choix), nombre (arrondi au pas).
Le fichier est dans data\\ : jamais remplacé par les mises à jour.
"""
from __future__ import annotations

import json
import os
import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from . import params as cat

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,30}$")
_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")
BY_KEY = {p[0]: p for p in cat.PARAMS}


def _dec(step: float) -> int:
    s = f"{step:.6f}".rstrip("0")
    return len(s.split(".")[1]) if "." in s else 0


def fmt_num(p: tuple, v: float) -> str:
    """Nombre borné et arrondi au pas, au format attendu par l'EA."""
    _, _, _, typ, mn, mx, step, _, _ = p
    v = max(mn, min(mx, v))
    if step:
        v = float((Decimal(str(v)) / Decimal(str(step))).quantize(Decimal(1), rounding=ROUND_HALF_UP) * Decimal(str(step)))
        v = max(mn, min(mx, v))
    return str(int(round(v))) if typ == "int" else f"{v:.{_dec(step or 0.01)}f}"


def raw_of(p: tuple, display: str) -> str | None:
    """Valeur affichée par l'EA (PARAMS) → valeur brute pour SET ; None si illisible."""
    typ, opts, d = p[3], p[8], (display or "").strip()
    if typ == "bool":
        return "1" if "✅" in d else ("0" if "❌" in d else None)
    if typ == "enum":
        return str(opts.index(d)) if d in opts else None
    m = _NUM.search(d)
    return fmt_num(p, float(m.group(0).replace(",", "."))) if m else None


def display_of(p: tuple, raw: str) -> str:
    """Valeur brute → texte affiché (comme l'EA)."""
    typ, unit, opts = p[3], p[7], p[8]
    if typ == "bool":
        return "✅ Activé" if raw == "1" else "❌ Désactivé"
    if typ == "enum":
        try:
            return opts[int(raw)]
        except (ValueError, IndexError):
            return "?"
    return f"{raw}{unit}"


def raw_values(displayed: dict[str, str]) -> dict[str, str]:
    """Réglages lus sur un robot (PARAMS) → valeurs brutes des clés connues."""
    out = {}
    for k, d in displayed.items():
        p = BY_KEY.get(k)
        r = raw_of(p, d) if p else None
        if r is not None:
            out[k] = r
    return out


def differences(preset: dict[str, str], current_raw: dict[str, str]) -> dict[str, str]:
    """Clés du preset dont la valeur diffère sur le robot : {clé: valeur du preset}."""
    return {k: v for k, v in preset.items() if current_raw.get(k) != v}


class PresetStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.presets: dict[str, dict[str, str]] = {}
        self.active = ""                                   # preset appliqué à tous les robots (référence)
        self.load()

    def load(self) -> None:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            self.presets = {str(k): {str(a): str(b) for a, b in v.items()} for k, v in d.get("presets", {}).items()}
            self.active = d.get("active", "") if d.get("active", "") in self.presets else ""
        except (OSError, ValueError, AttributeError):
            self.presets, self.active = {}, ""

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"active": self.active, "presets": self.presets}, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def names(self) -> list[str]:
        return sorted(self.presets, key=str.lower)

    def name_at(self, i: int) -> str:
        n = self.names()
        return n[i] if 0 <= i < len(n) else ""

    def index(self, name: str) -> int:
        return self.names().index(name) if name in self.presets else -1

    def put(self, name: str, values: dict[str, str]) -> None:
        self.presets[name] = dict(values)
        self.save()

    def set_value(self, name: str, key: str, raw: str) -> None:
        self.presets[name][key] = raw
        self.save()

    def delete(self, name: str) -> None:
        self.presets.pop(name, None)
        if self.active == name:
            self.active = ""
        self.save()

    def set_active(self, name: str) -> None:
        self.active = name if name in self.presets else ""
        self.save()
