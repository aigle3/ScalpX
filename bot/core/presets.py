"""Presets centraux : un seul jeu de réglages de l'EA, gardé par le bot (data/presets.json), appliqué à tous les robots.

Valeurs « brutes » = format attendu par la commande SET de l'EA : 1/0 (oui/non), index (choix), nombre (arrondi au pas).
Le fichier est dans data/ : jamais remplacé par les mises à jour.
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


EXPORT_NAME = "scalpx_preset.cfg"


class PresetStore:
    def __init__(self, path: Path, export_dir: Path | None = None) -> None:
        self.path = Path(path)
        self.export_dir = Path(export_dir) if export_dir else None   # Bot 3.14.0 : Common\Files (testeur MT5 à la main)
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
        self.export()

    def export(self) -> Path | None:
        """Bot 3.14.0 : preset ⭐ actif (ex. Martin) copié dans Common\\Files\\scalpx_preset.cfg à chaque modification (toi,
        l'optimiseur, ⭐) : l'EA lancé à la main dans le testeur MT5 de n'importe quel terminal du VPS le charge tout seul.
        Format « clé=valeur » des backtests du bot (TOUS les réglages, news et créneaux compris)."""
        if self.export_dir is None or not self.active or self.active not in self.presets:
            return None
        try:
            dst = self.export_dir / EXPORT_NAME
            body = f"# preset={self.active}\n" + "".join(f"{k}={v}\n" for k, v in self.presets[self.active].items()
                                                       if k and v not in ("", None))
            if dst.is_file() and dst.read_text(encoding="ascii", errors="replace") == body.encode("ascii", "replace").decode("ascii"):
                return dst
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_suffix(".tmp")
            tmp.write_bytes(body.encode("ascii", "replace"))         # l'EA lit en ANSI ; nom du preset : « ? » si accent
            os.replace(tmp, dst)
            return dst
        except OSError:
            return None

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


# ---------------------------------------------------------------- preset « conseille » (Bot 3.18.1 : retiré)
RECOMMENDED_NAME = "conseille"


def remove_recommended(store: "PresetStore") -> list[str]:
    """Supprime les presets « conseille », « conseille2 »… ajoutés automatiquement par les versions 2.29 à 3.18.0.
    Jamais le preset ⭐ actif (s'il a été choisi, il est gardé). Renvoie les noms supprimés."""
    gone = [n for n in store.names() if re.fullmatch(rf"{RECOMMENDED_NAME}\d*", n) and n != store.active]
    for n in gone:
        store.delete(n)
    return gone


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
    "f_ext": "UseExtensionFilter", "extatr": "ExtensionMaxATR", "f_fresh": "UseFreshSignalFilter",
    "freshbars": "FreshSignalMaxBars", "sl_entry": "ObjectifAutoEntreeMin", "ct_on": "ObjectifCoupureActive",
    "freshnoise": "FreshSignalIgnoreBars", "exttf": "ExtensionTimeframe", "sl_cont": "ObjectifContinu",
}
SLOTS_INPUT = "ObjectifAutoHeures"     # les 4 masques de créneaux (sl_m00..sl_m45) → un seul input texte « 3,6,9:30 »
CUTS_INPUT = "ObjectifCoupureHeures"   # EA 7.0.0 : les 4 masques de coupure (ct_m00..ct_m45) → « 13:45,21 »


def slots_input(values: dict[str, str], keys: tuple = cat.SLOT_KEYS) -> str | None:
    """Masques du preset → valeur de ObjectifAutoHeures (« H » pour :00, « H:MM » sinon) ; None si le preset n'en a pas.
    keys = cat.CUT_KEYS : même chose pour ObjectifCoupureHeures."""
    if not any(k in values for k in keys):
        return None
    masks = []
    for k in keys:
        try:
            masks.append(int(float(values.get(k, cat.DEFAULTS.get(k, "0")))))   # absent : défaut de l'EA (comme à l'écran)
        except ValueError:
            masks.append(0)
    return ",".join(str(x // 60) if x % 60 == 0 else f"{x // 60}:{x % 60:02d}" for x in cat.slots_of(masks))


def human(p: tuple, raw: str) -> str:
    """Valeur brute → texte pour un humain (les masques de créneaux deviennent des heures)."""
    return cat.mask_text(p[0], raw) if p[0] in cat.SLOT_KEYS + cat.CUT_KEYS else display_of(p, raw)


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
    cuts = slots_input(values, cat.CUT_KEYS)
    if cuts is not None:
        want[CUTS_INPUT] = cuts
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


# ---- Bot 3.16.0 : export / import de presets (fichier texte transféré d'un bot à l'autre dans Telegram)
EXPORT_HEADER = "# ScalpX presets"
_MAX_IMPORT = 200_000                                  # octets : un preset fait ~2 Ko


def export_text(store: "PresetStore", names: list[str]) -> str:
    """Presets → texte : une section « [preset=Nom] » puis « clé=valeur » par réglage (valeurs brutes de l'EA)."""
    out = [EXPORT_HEADER, "# A importer dans l'autre bot : Presets > Importer, puis envoyer ce fichier (ou coller ce texte).", ""]
    for n in names:
        vals = store.presets.get(n)
        if vals is None:
            continue
        out.append(f"[preset={n}]")
        out += [f"{k}={v}" for k, v in vals.items() if k]
        out.append("")
    return "\n".join(out)


def parse_import(text: str) -> tuple[dict[str, dict[str, str]], int]:
    """Texte exporté → ({nom: réglages}, nombre de réglages inconnus ignorés). Accepte aussi une seule section sans
    en-tête (« # preset=Nom » du testeur MT5, ou rien : nom « Importé »). ValueError si aucun réglage reconnu."""
    if len(text.encode("utf-8", "replace")) > _MAX_IMPORT:
        raise ValueError("fichier trop gros")
    presets: dict[str, dict[str, str]] = {}
    name, unknown = "", 0
    for raw in text.replace("\r", "").split("\n"):
        line = raw.strip().lstrip("\ufeff")
        if not line:
            continue
        m = re.fullmatch(r"\[preset=(.{1,60})\]", line) or re.fullmatch(r"#\s*preset=(.{1,60})", line)
        if m:
            name = m.group(1).strip()
            presets.setdefault(name, {})
            continue
        if line.startswith(("#", ";")) or "=" not in line:
            continue
        k, v = (x.strip() for x in line.split("=", 1))
        if k not in BY_KEY:
            unknown += 1
            continue
        if any(c in v for c in "\x00"):
            continue
        presets.setdefault(name or "Importé", {})[k] = v
    presets = {n: v for n, v in presets.items() if v}
    if not presets:
        raise ValueError("aucun réglage ScalpX reconnu")
    return presets, unknown


# ---- Bot 4.2.0 : 📥 Importer accepte TOUS les fichiers de sauvegarde
def import_text_from(blob: bytes, key_b64: str = "", password: str = "") -> str:
    """Fichier reçu (📥 Importer, ☁️ Restaurer) → texte au format de 📤 Exporter. Formats acceptés :
    - texte exporté (`scalpx_*.txt`, `presets.txt` de la sauvegarde GitHub) ou fichier `# preset=` du testeur ;
    - `presets.json` du dossier data du bot ;
    - sauvegarde complète `sauvegarde.tar.gz` (presets.json qu'elle contient) ;
    - les mêmes chiffrés (`presets.enc`, `sauvegarde.enc`) : clé de ce serveur ou mot de passe.
    ValueError lisible sinon (« mot de passe requis » : redemander le mot de passe)."""
    from core import crypt
    import io
    import tarfile
    if not blob:
        raise ValueError("fichier vide")
    if len(blob) > 60 * 2**20:
        raise ValueError("fichier trop gros")
    if crypt.is_encrypted(blob):
        blob = crypt.decrypt(blob, key_b64, password)
    if blob[:2] == b"\x1f\x8b":                                     # sauvegarde complète (tar.gz)
        try:
            with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
                if "presets.json" not in tar.getnames():
                    raise ValueError("cette sauvegarde ne contient pas de presets")
                blob = tar.extractfile("presets.json").read()
        except (tarfile.TarError, OSError, KeyError) as exc:
            raise ValueError(f"archive illisible : {exc}") from exc
    try:
        text = blob.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = blob.decode("utf-16")                             # .set de MT5 (UTF-16)
        except UnicodeDecodeError as exc:
            raise ValueError("fichier ni texte ni sauvegarde ScalpX") from exc
    if text.lstrip().startswith("{"):                               # presets.json
        try:
            d = json.loads(text)
            pres = d.get("presets") if isinstance(d, dict) else None
            if not isinstance(pres, dict):
                raise ValueError
        except ValueError as exc:
            raise ValueError("JSON qui n'est pas un fichier de presets") from exc
        out = [EXPORT_HEADER, ""]
        for n, vals in pres.items():
            if isinstance(vals, dict):
                out.append(f"[preset={str(n)[:60]}]")
                out += [f"{k}={v}" for k, v in vals.items() if k]
                out.append("")
        return "\n".join(out)
    return text
