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
    """Clés du preset dont la valeur diffère sur le robot : {clé: valeur du preset}.
    Les réglages qui n'existent plus dans l'EA (supprimés d'une version) sont ignorés."""
    return {k: v for k, v in preset.items() if k in BY_KEY and current_raw.get(k) != v}


class PresetStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.presets: dict[str, dict[str, str]] = {}
        self.active = ""                                   # preset appliqué à tous les robots (référence)
        self.load()

    def load(self) -> None:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            # réglages supprimés de l'EA (ex. « autoresume » en 6.0.0) : retirés des presets
            self.presets = {str(k): {str(a): str(b) for a, b in v.items() if str(a) in BY_KEY}
                            for k, v in d.get("presets", {}).items()}
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


# ---------------------------------------------------------------- preset « conseille » (ajouté une fois, jamais imposé)
RECOMMENDED_NAME = "conseille"
# créneaux 2:00, 4:00, 6:00, 11:00, 20:00 (bits de l'heure, minute :00) ; objectif 0,5 % ; grille plus large et plus douce.
# SL d'urgence, drawdown et limite de session : NON modifiés (repris du preset ⭐ actif s'il y en a un).
RECOMMENDED = {"sl_on": "1", "sl_pct": "0.5", "sl_m00": str(sum(1 << h for h in (2, 4, 6, 11, 20))), "sl_m15": "0",
               "sl_m30": "0", "sl_m45": "0", "dyngrid": "1", "gridatr": "0.7", "gridmin": "200", "lotmult": "1.2",
               "maxpend": "12"}


def ensure_recommended(store: "PresetStore", done: bool) -> str:
    """Ajoute le preset « conseille » (copie du preset ⭐ actif + RECOMMENDED) si ce n'est pas déjà fait. Renvoie son nom
    ou "" ; il n'est NI activé NI appliqué (c'est l'utilisateur qui choisit)."""
    if done:
        return ""
    name = RECOMMENDED_NAME
    i = 2
    while name in store.presets:
        name = f"{RECOMMENDED_NAME}{i}"
        i += 1
    base = dict(store.presets.get(store.active, {})) if store.active else {}
    base.update(RECOMMENDED)
    store.put(name, base)
    return name


# ---------------------------------------------------------------- .set de BASE (point de départ des nouveaux comptes)
# réglage du bot → input de l'EA (nom dans le .set). Les réglages sans input (fuseau, alertes, news : délais) ne
# peuvent pas aller dans le .set : ils sont envoyés au nouveau robot juste après sa création.
_TF_PERIOD = ["1", "5", "15", "30", "16385", "16388"]          # M1 M5 M15 M30 H1 H4 → ENUM_TIMEFRAMES
SET_INPUTS = {
    "tf": "TradingTimeframe", "bar": "SignalBar", "fast": "FastEMA", "slow": "SlowEMA", "atrp": "ATRPeriod",
    "lot": "LotSize", "tbm": "TradesBeforeMultiply", "lotmult": "LotMultiplier", "grid": "GridStep",
    "gridmult": "GridStepMultiplier", "dyngrid": "UseDynamicGrid", "gridatr": "GridATRMultiplier",
    "gridmin": "GridMinPoints", "gridmax": "GridMaxPoints", "maxpend": "MaxPendingOrders", "maxspread": "MaxSpread",
    "wavg": "UseWeightedAverage", "dyntp": "UseDynamicTP", "atrmult": "ATRMultiplier", "tpfixed": "AvgTakeProfit",
    "tpmin": "TPMinPoints", "tpmax": "TPMaxPoints", "f_atr": "UseVolatilityFilter", "atrmin": "MinATRForVolatility",
    "atrmax": "MaxATRForVolatility", "f_ema": "UsePriceEMAFilter", "f_vol": "VolumeFilterEnabledAtStart",
    "volmin": "VolumeMinRatio", "volmax": "VolumeMaxRatio", "volbars": "VolumeBars", "f_lo": "UseLowerTFFilter",
    "lotf": "LowerTimeframe", "lofast": "LowerFastEMA", "loslow": "LowerSlowEMA", "lobar": "LowerSignalBar",
    "f_hi": "UseUpperTFFilter", "hitf": "UpperTimeframe", "hifast": "UpperFastEMA", "hislow": "UpperSlowEMA",
    "hibar": "UpperSignalBar", "f_can": "UseUpperCandleFilter", "canbody": "UpperCandleMinBody",
    "f_time": "TradingBlockEnabledAtStart", "t_start": "TradingBlockStartHour", "t_end": "TradingBlockEndHour",
    "t_server": "TimeBlockUseServer", "ddthr": "DrawdownThreshold", "ddstop": "AutoStopOnDrawdown",
    "emsl": "UseEmergencyStopLoss", "emslpct": "EmergencyStopLossPct", "sesslim": "UseSessionLossLimit",
    "sesspct": "MaxSessionLossPct", "early": "UseEarlyGoalClose", "goalmax": "GoalMaxAmount",
    "profnotif": "ProfitNotifyAmount", "dev": "Deviation", "retries": "MaxRetries", "n_filters": "NotifyFilters",
    "n_progress": "NotifyProgress", "n_errors": "NotifyErrors", "ch_style": "ChartModernStyle", "ch_ema": "ChartShowEMA",
    "ch_levels": "ChartShowLevels", "ch_panel": "ChartShowPanel", "news_block": "NewsPauseEnabledAtStart",
    "sl_on": "ObjectifAutoCreneaux", "sl_pct": "ObjectifAutoPct", "sl_win": "ObjectifAutoFenetreMin",
    "seuil_on": "ActiverSeuil500", "seuil_arret": "SeuilArret", "seuil_reprise": "SeuilReprise",
}
SLOTS_INPUT = "ObjectifAutoHeures"     # les 4 masques de créneaux (sl_m00..sl_m45) → un seul input texte « 3,6,9:30 »


def slots_input(values: dict[str, str]) -> str | None:
    """Masques du preset → valeur de ObjectifAutoHeures (« H » pour :00, « H:MM » sinon) ; None si le preset n'en a pas."""
    if not any(k in values for k in cat.SLOT_KEYS):
        return None
    masks = []
    for k in cat.SLOT_KEYS:
        try:
            masks.append(int(float(values.get(k, cat.DEFAULTS.get(k, "0")))))   # absent : défaut de l'EA (comme à l'écran)
        except ValueError:
            masks.append(0)
    return ",".join(str(x // 60) if x % 60 == 0 else f"{x // 60}:{x % 60:02d}" for x in cat.slots_of(masks))


def human(p: tuple, raw: str) -> str:
    """Valeur brute → texte pour un humain (les masques de créneaux deviennent des heures)."""
    return cat.mask_text(p[0], raw) if p[0] in cat.SLOT_KEYS else display_of(p, raw)


def set_value(key: str, raw: str) -> str:
    """Valeur brute du bot → valeur d'input dans le .set."""
    p = BY_KEY.get(key)
    if p is None:
        return raw
    if p[3] == "bool":
        return "true" if raw == "1" else "false"
    if key in ("tf", "lotf", "hitf"):
        try:
            return _TF_PERIOD[int(raw)]
        except (ValueError, IndexError):
            return raw
    return raw


def overlay_set(data: bytes, values: dict[str, str]) -> bytes:
    """Contenu d'un .set (UTF-16 LE + BOM) avec les valeurs du preset ; commentaires et autres inputs conservés."""
    text = data[2:].decode("utf-16-le") if data[:2] == b"\xff\xfe" else data.decode("utf-8-sig", errors="replace")
    want = {SET_INPUTS[k]: set_value(k, v) for k, v in values.items() if k in SET_INPUTS}
    hours = slots_input(values)
    if hours is not None:
        want[SLOTS_INPUT] = hours
    out, seen = [], set()
    for line in text.split("\r\n"):
        name = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith(";") else ""
        if name in want:
            rest = line.split("=", 1)[1]
            extra = rest[rest.index("||"):] if "||" in rest else ""          # champs d'optimisation éventuels
            line = f"{name}={want[name]}{extra}"
            seen.add(name)
        out.append(line)
    missing = [f"{n}={v}" for n, v in want.items() if n not in seen]
    if missing:
        while out and out[-1] == "":
            out.pop()
        out += ["; ══ Preset du bot ══"] + missing + [""]
    return b"\xff\xfe" + "\r\n".join(out).encode("utf-16-le")


def active_values(data_dir: Path) -> tuple[str, dict[str, str]] | None:
    """(nom, valeurs) du preset ⭐ actif, ou None."""
    st = PresetStore(Path(data_dir) / "presets.json")
    return (st.active, st.presets[st.active]) if st.active else None


def write_base_set(set_path: Path, values: dict[str, str]) -> bool:
    """Écrit le preset dans le .set de BASE (les nouveaux comptes partent de ces valeurs). False si absent."""
    p = Path(set_path)
    if not p.is_file():
        return False
    new = overlay_set(p.read_bytes(), values)
    if new != p.read_bytes():
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(new)
        os.replace(tmp, p)
    return True
