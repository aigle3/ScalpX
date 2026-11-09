"""Optimiseur autonome (EA 7.7.0+) : chaque nuit, une catégorie de réglages est testée autour des valeurs actuelles du
compte de référence, dans UN backtest de 60 jours où chaque variante est un scénario virtuel (valeurs absolues).

Cycle de 4 nuits consécutives : 🧱 Grille et lots → 📐 Signal et TP → 🛡️ Filtres → 🎯 Objectif et créneaux.

Choix sur les 30 derniers jours, sécurité sur 60 jours :
  - une variante n'est retenue que si elle ne perd JAMAIS le compte (60 j et 30 j) et reste sous DD_LIMIT de drawdown ;
  - réglages actuels sûrs : la variante doit gagner nettement plus sur 30 j sans perdre sur 60 j ;
  - réglages actuels dangereux (compte perdu ou drawdown ≥ DD_LIMIT sur 60 j) : la variante sûre la plus rentable ;
  - chaque variante bouge d'UN cran ; les combinaisons de deux réglages d'un même sous-groupe sont testées ensemble ;
  - à résultat presque égal, la variante « entourée de bons résultats » (plateau) est préférée.
Portée : 3 passages sans changement d'un réglage → recherche 2 fois plus loin (1 → 2 → 4 crans, puis retour à 1).
Créneaux (nuit 🎯) : un créneau est retiré s'il dépasse 80 % de drawdown sur 60 j ; une heure pile libre, testée seule,
est ajoutée si elle reste sous 80 % de drawdown, ne perd jamais le compte et gagne de l'argent. Coupures jamais touchées.
Le mode continu (sl_cont) n'est jamais modifié par l'optimiseur (Bot 3.2.1).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import labo
from .params import CUT_KEYS, SLOT_KEYS, SLOT_MINUTES, masks_of, slot_text, slots_of

DD_LIMIT = 90.0                     # drawdown (%) au-delà duquel une variante « frôle la perte du compte »
CHOOSE_DAYS = 30                    # période de choix (marché récent)
SAFETY_DAYS = 60                    # période de sécurité (aucun compte perdu)
MIN_GOALS = 10                      # objectifs minimum sur 30 j pour décider
PROFIT_FLOOR = 0.8                  # réglages dangereux : une variante sûre doit garder au moins 80 % du gain sur 30 j
MAX_VARIANTS = 38                   # EA : 40 scénarios au plus (réglages actuels + témoin + 38 variantes)
SLOT_DD = 80.0                      # créneaux : drawdown (%) au-delà duquel un créneau est retiré / refusé
SLOT_MIN_GOALS = 5                  # créneau candidat : objectifs minimum sur 60 j pour être ajouté
IDLE_PASSES = 3                     # passages sans changement avant d'élargir la recherche
MAX_SCALE = 4                       # portée de recherche maximum : 4 crans
TEMOIN = "pt"                       # scénario témoin : réglages actuels recalculés comme les variantes « calcul propre »

# clé de l'EA → (libellé, pas (cran), minimum, maximum, option du scénario, unité affichée)
NUM: dict[str, tuple[str, float, float, float, str, str]] = {
    "gridatr": ("Multiplicateur ATR de la grille", 0.1, 0.5, 2.0, "ga", " × ATR"),
    "gridmin": ("Grille minimum", 50, 100, 800, "gn", " pts"),
    "gridmax": ("Grille maximum", 100, 300, 1500, "gx", " pts"),
    "maxpend": ("Ordres en attente max", 1, 3, 15, "mp", ""),
    "lotmult": ("Multiplicateur de lot", 0.05, 1.0, 1.5, "lm", " ×"),
    "tbm": ("Trades avant multiplication", 1, 1, 10, "tb", ""),
    "fast": ("EMA rapide", 1, 2, 50, "ef", ""),
    "slow": ("EMA lente", 2, 5, 200, "es", ""),
    "atrp": ("Période ATR", 2, 5, 50, "ap", ""),
    "atrmult": ("Multiplicateur ATR du TP", 0.1, 0.3, 5.0, "am", " × ATR"),
    "tpmin": ("TP minimum", 10, 10, 1000, "tn", " pts"),
    "tpmax": ("TP maximum", 50, 50, 3000, "tx", " pts"),
    "atrmin": ("ATR minimum", 10, 10, 2000, "an", " pts"),
    "atrmax": ("ATR maximum", 50, 50, 5000, "ax", " pts"),
    "volmin": ("Volume minimum", 0.05, 0.1, 1.5, "vn", " × moy."),
    "volmax": ("Volume maximum", 0.1, 1.2, 10.0, "vx", " × moy."),
    "extatr": ("Extension maximum", 0.2, 0.5, 10.0, "ea", " × ATR"),
    "canbody": ("Corps de bougie minimum", 0.05, 0.0, 3.0, "cb", " × ATR"),
    "freshbars": ("Signal récent : bougies max", 6, 6, 60, "b", " bougies"),
    "sl_pct": ("Objectif", 0.1, 0.2, 1.5, "o", " %"),
}
INTS = frozenset({"gridmin", "gridmax", "maxpend", "tbm", "fast", "slow", "atrp", "tpmin", "tpmax", "freshbars"})
# réglages qui changent la lecture du marché ou un seuil de filtre : variantes recalculées entièrement (« calcul propre »),
# comparées au témoin (réglages actuels recalculés de la même façon) et non aux réglages actuels (antirebond du compte)
RAW = frozenset({"fast", "slow", "atrp", "atrmin", "atrmax", "volmin", "volmax", "extatr", "canbody"})
# réglage → (autre réglage, condition) : testé seulement si la condition vaut "1"
NEEDS: dict[str, str] = {"gridatr": "dyngrid", "gridmin": "dyngrid", "gridmax": "dyngrid", "atrmult": "dyntp",
                         "tpmin": "dyntp", "tpmax": "dyntp", "atrmin": "f_atr", "atrmax": "f_atr", "volmin": "f_vol",
                         "volmax": "f_vol", "extatr": "f_ext", "canbody": "f_can", "freshbars": "f_fresh"}
DEFAULT_ON = {"dyngrid": "1", "dyntp": "1"}
ORDERED = (("gridmin", "gridmax"), ("fast", "slow"), ("tpmin", "tpmax"), ("atrmin", "atrmax"), ("volmin", "volmax"))
# filtre du labo → réglage de l'EA (le spread n'est jamais retiré : il protège l'exécution)
FILTERS: dict[str, str] = {"atr": "f_atr", "ema": "f_ema", "vol": "f_vol", "lo": "f_lo", "hi": "f_hi", "can": "f_can",
                           "time": "f_time", "news": "news_block", "ext": "f_ext", "fresh": "f_fresh"}
# catégories (une par nuit, à tour de rôle) : sous-groupes de réglages combinés entre eux deux à deux
GROUPS: list[tuple[str, str, list[list[str]]]] = [
    ("grille_lots", "🧱 Grille et lots", [["gridatr", "gridmin", "gridmax"], ["maxpend", "lotmult", "tbm"]]),
    ("signal_tp", "📐 Signal et TP", [["fast", "slow", "atrp"], ["atrmult", "tpmin", "tpmax"]]),
    ("filtres", "🛡️ Filtres", [["atrmin", "atrmax"], ["volmin", "volmax"], ["extatr"], ["canbody"], ["freshbars"]]),
    ("objectif", "🎯 Objectif et créneaux", [["sl_pct"]]),
]
MODE_KEY = "sl_cont"
# nuit 🛟 Sécurité : hors rotation, lancée après une nuit où les réglages actuels perdent le compte
SAFETY = ("securite", "🛟 Sécurité", ["lotmult", "maxpend", "gridatr"])
SHORT = {"gridatr": "ATR grille", "gridmin": "Grille min", "gridmax": "Grille max", "maxpend": "Ordres max",
         "lotmult": "Mult. lot", "tbm": "Trades/mult.", "fast": "EMA rapide", "slow": "EMA lente", "atrp": "ATR",
         "atrmult": "ATR TP", "tpmin": "TP min", "tpmax": "TP max", "atrmin": "ATR min", "atrmax": "ATR max",
         "volmin": "Vol. min", "volmax": "Vol. max", "extatr": "Extension", "canbody": "Corps", "freshbars": "Signal récent",
         "sl_pct": "Objectif", "sl_cont": "Mode"}


def group_title(key: str) -> str:
    return SAFETY[1] if key == SAFETY[0] else next((t for k, t, _ in GROUPS if k == key), key)


def group_params(key: str) -> list[str]:
    """Réglages numériques d'une catégorie (portée de recherche, journal)."""
    if key == SAFETY[0]:
        return list(SAFETY[2])
    g = next((x for x in GROUPS if x[0] == key), None)
    return [k for sub in g[2] for k in sub] if g else []


def fnum(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def _masks(params: dict[str, str], keys) -> list[int]:
    out = []
    for k in keys:
        try:
            out.append(int(float(params.get(k, "0") or 0)))
        except ValueError:
            out.append(0)
    return out


def slots_label(raw_masks: list[int]) -> str:
    s = slots_of(raw_masks)
    return ", ".join(slot_text(x) for x in s) if s else "aucun"


def label_of(key: str, raw: str) -> str:
    """« Multiplicateur ATR de la grille : 1,1 × ATR » / « Filtre volume : désactivé » / « Créneaux à :00 : 03:00, 06:00 »."""
    if key == MODE_KEY:
        return f"Lancement des objectifs : {value_text(key, raw)}"
    if key in NUM:
        name, *_, unit = NUM[key]
        try:
            return f"{name} : {fnum(float(raw))}{unit}"
        except ValueError:
            return f"{name} : {raw}"
    if key in SLOT_KEYS:
        return f"Créneaux à :{SLOT_MINUTES[SLOT_KEYS.index(key)]:02d} : {value_text(key, raw)}"
    code = next((c for c, k in FILTERS.items() if k == key), None)
    name = labo.LABELS[labo.CODES.index(code)] if code else key
    return f"Filtre {name} : {'activé' if raw == '1' else 'désactivé'}"


def value_text(key: str, raw: str) -> str:
    if key == MODE_KEY:
        return "mode continu" if raw == "1" else "créneaux"
    if key in NUM:
        try:
            return f"{fnum(float(raw))}{NUM[key][5]}"
        except ValueError:
            return raw
    if key in SLOT_KEYS:
        try:
            m = int(float(raw))
        except ValueError:
            return raw
        masks = [0, 0, 0, 0]
        masks[SLOT_KEYS.index(key)] = m
        return slots_label(masks)
    return "activé" if raw == "1" else "désactivé"


@dataclass
class Variant:
    key: str                      # clé du scénario virtuel (p1…, « pt » pour le témoin)
    name: str
    changes: dict                 # réglage de l'EA → (valeur actuelle, valeur testée) ; plusieurs = combinaison testée ensemble
    toks: list[str] = field(default_factory=lambda: ["="] * len(labo.CODES))
    opts: dict[str, str] = field(default_factory=dict)
    kind: str = "num"             # num (réglage) · flt (filtre inversé) · slot (heure testée seule) · temoin
    ref: str = "miroir"           # référence de comparaison : « miroir » (réglages actuels) ou TEMOIN
    hour: int = -1                # créneau candidat (heure pile)

    @property
    def param(self) -> str:
        return next(iter(self.changes))

    @property
    def old(self) -> str:
        return self.changes[self.param][0]

    @property
    def new(self) -> str:
        return self.changes[self.param][1]

    def line(self) -> str:
        name = self.name.replace("|", "/")
        opts = ";".join(f"{k}={v}" for k, v in self.opts.items())
        return f"{self.key}|{name}|{','.join(self.toks)}" + (f"|{opts}" if opts else "")


def _raw(key: str, v: float) -> str:
    return str(int(round(v))) if key in INTS else f"{round(v, 4):g}"


def applies(params: dict[str, str], key: str) -> bool:
    need = NEEDS.get(key)
    return key in params and (need is None or params.get(need, DEFAULT_ON.get(need, "0")) == "1")


def _valid(params: dict[str, str], vals: dict[str, str]) -> bool:
    """Les paires min / max (et EMA rapide / lente) restent dans l'ordre, même combinées."""
    for lo, hi in ORDERED:
        if lo not in vals and hi not in vals:
            continue
        try:
            a = float(vals.get(lo, params.get(lo, "0")) or 0)
            b = float(vals.get(hi, params.get(hi, "1e9")) or 1e9)
        except ValueError:
            return False
        if not a < b:
            return False
    return True


def _moves(params: dict[str, str], key: str, scale: dict[str, int] | None) -> list[str]:
    if not applies(params, key) or key not in NUM:
        return []
    try:
        cur = float(params[key])
    except ValueError:
        return []
    _, step, lo, hi, _, _ = NUM[key]
    step *= max(1, min(MAX_SCALE, int((scale or {}).get(key, 1))))   # portée élargie après des passages sans changement
    out = []
    for d in (-1, 1):
        v = round(min(hi, max(lo, cur + d * step)), 4)
        if abs(v - cur) > 1e-9:
            out.append(_raw(key, v))
    return out


def _num_variant(params: dict[str, str], vals: dict[str, str]) -> Variant:
    if len(vals) == 1:
        k, r = next(iter(vals.items()))
        name = f"{NUM[k][0]} {fnum(float(r))}{NUM[k][5]}"
    else:
        name = " + ".join(f"{SHORT[k]} {fnum(float(r))}{NUM[k][5]}" for k, r in vals.items())
    v = Variant("", name, {k: (params[k], r) for k, r in vals.items()}, opts={NUM[k][4]: r for k, r in vals.items()})
    if any(k in RAW for k in vals):
        v.opts["r"] = "1"
        v.ref = TEMOIN
    return v


def slot_candidates(params: dict[str, str]) -> list[int]:
    """Heures piles libres où un créneau pourrait être ajouté (pas déjà un créneau à cette heure, pas de coupure dans l'heure)."""
    if params.get("sl_on", "1") != "1":
        return []
    cur = slots_of(_masks(params, SLOT_KEYS))
    cuts = slots_of(_masks(params, CUT_KEYS)) if params.get("ct_on", "1") == "1" else []
    out = []
    for h in range(24):
        if any(h * 60 <= s < h * 60 + 60 for s in cur):
            continue
        if any(h * 60 <= c < h * 60 + 60 for c in cuts):
            continue
        out.append(h)
    return out


def variants(params: dict[str, str], group: str, scale: dict[str, int] | None = None) -> list[Variant]:
    """Variantes de la nuit. Réglages numériques : chacun à −1 et +1 cran, PUIS les combinaisons de deux réglages d'un
    même sous-groupe (±1 cran chacun) ; filtres : chacun inversé ; créneaux : chaque heure pile libre testée seule.
    Un témoin « calcul propre » est ajouté en tête dès qu'une variante se compare à lui."""
    if group == SAFETY[0]:
        return safety_variants(params)
    g = next((x for x in GROUPS if x[0] == group), None)
    if g is None:
        return []
    out: list[Variant] = []
    if group == "filtres":
        for code, key in FILTERS.items():
            if key not in params:
                continue
            i = labo.CODES.index(code)
            toks = ["="] * len(labo.CODES)
            toks[i] = "~"
            new = "0" if params[key] == "1" else "1"
            out.append(Variant("", f"{labo.LABELS[i]} {'désactivé' if new == '0' else 'activé'}", {key: (params[key], new)},
                               toks, kind="flt"))
    for sub in g[2]:
        moves = {k: m for k in sub if (m := _moves(params, k, scale))}
        keys = list(moves)
        for k in keys:
            for r in moves[k]:
                if _valid(params, {k: r}):
                    out.append(_num_variant(params, {k: r}))
        for i, k1 in enumerate(keys):
            for k2 in keys[i + 1:]:
                for r1 in moves[k1]:
                    for r2 in moves[k2]:
                        if _valid(params, {k1: r1, k2: r2}):
                            out.append(_num_variant(params, {k1: r1, k2: r2}))
    if group == "objectif":
        for h in slot_candidates(params):
            m = 1 << h
            out.append(Variant("", f"Créneau {h:02d}:00 seul", {}, opts={"sm": f"{m},0,0,0"}, kind="slot", hour=h))
    if any(v.ref == TEMOIN for v in out):
        out = [Variant(TEMOIN, "Réglages actuels (calcul propre)", {}, opts={"r": "1"}, kind="temoin")] + out
    n = 0
    kept: list[Variant] = []
    for v in out:
        if v.kind == "temoin":
            kept.append(v)
            continue
        if n >= MAX_VARIANTS:
            break
        n += 1
        v.key = f"p{n}"
        kept.append(v)
    return kept


NEW_OPTS = frozenset({"tb", "ef", "es", "ap", "am", "tn", "tx", "an", "ax", "vn", "vx", "ea", "cb", "r", "sm", "ct"})



def ea_too_old(rows: list[dict], vs: list[Variant]) -> bool:
    """L'EA du testeur ignore les options 7.7.0 (ses objectifs virtuels ne les renvoient pas) : variantes invalides."""
    for v in vs:
        need = [k for k in v.opts if k in NEW_OPTS]
        if not need:
            continue
        row = next((d for d in rows if d.get("sc") == v.key and "opts" in d), None)
        if row is not None and not all(f"{k}=" in str(row.get("opts", "")) for k in need):
            return True
    return False


def tested(vs: list[Variant]) -> list[Variant]:
    """Variantes comptées (sans le témoin)."""
    return [v for v in vs if v.kind != "temoin"]


def safety_variants(params: dict[str, str]) -> list[Variant]:
    """Échelle de prudence (du plus léger au plus fort) : lot moins multiplié, moins d'ordres, grille plus large,
    et leurs combinaisons légères. L'optimiseur prendra la plus RENTABLE des variantes sûres."""
    vals: dict[str, list[str]] = {}
    try:
        lm = float(params.get("lotmult", ""))
        vals["lotmult"] = [_raw("lotmult", round(v, 2)) for v in (lm - 0.05, lm - 0.1, lm - 0.15) if v >= 1.0 - 1e-9]
    except ValueError:
        pass
    try:
        mp = int(float(params.get("maxpend", "")))
        vals["maxpend"] = [str(v) for v in (mp - 1, mp - 2, mp - 3) if v >= NUM["maxpend"][2]]
    except ValueError:
        pass
    if params.get("dyngrid", "1") == "1":
        try:
            ga = float(params.get("gridatr", ""))
            vals["gridatr"] = [_raw("gridatr", v) for v in sorted({round(min(NUM["gridatr"][3], ga * f), 1) for f in (1.25, 1.5)}) if v > ga]
        except ValueError:
            pass
    vals = {k: v for k, v in vals.items() if v}
    out: list[Variant] = []

    def add(ch: dict[str, str]) -> None:
        out.append(_num_variant(params, ch))
    for k, lst in vals.items():
        for r in lst:
            add({k: r})
    keys = list(vals)
    for i, k1 in enumerate(keys):                          # combinaisons légères : premier cran de chaque
        for k2 in keys[i + 1:]:
            add({k1: vals[k1][0], k2: vals[k2][0]})
    if len(keys) >= 2 and len(vals[keys[0]]) > 1 and len(vals[keys[1]]) > 1:
        add({keys[0]: vals[keys[0]][1], keys[1]: vals[keys[1]][1]})
    for n, v in enumerate(out[:19], 1):
        v.key = f"p{n}"
    return out[:19]


def scenario_file(vs: list[Variant]) -> str:
    """Fichier de scénarios du backtest de l'optimiseur : réglages actuels + témoin + variantes, sans scénario automatique."""
    return "# ScalpX : scénarios de l'optimiseur (backtest)\nauto=0\nactif=1\n" + "".join(v.line() + "\n" for v in vs)


def one_notch(param: str, old: str, best: str) -> str:
    """Un seul pas depuis la valeur actuelle, dans la direction de la meilleure valeur testée."""
    if param not in NUM:
        return best
    try:
        o, b = float(old), float(best)
    except ValueError:
        return best
    _, step, lo, hi, *_ = NUM[param]
    if abs(b - o) <= step + 1e-9:
        return best
    v = round(min(hi, max(lo, o + (step if b > o else -step))), 4)
    return _raw(param, v)


@dataclass
class Row:
    name: str
    won30: int
    n30: int
    net30: float
    lost60: int
    dd60: float
    net60: float
    safe: bool


@dataclass
class SlotPlan:
    """Créneaux : retraits (drawdown > 80 % sur 60 j) et ajouts (heure testée seule, sûre et gagnante)."""
    removed: list[int] = field(default_factory=list)      # minutes depuis minuit
    added: list[int] = field(default_factory=list)
    kept_last: int = -1                                   # dernier créneau gardé malgré son drawdown (jamais zéro créneau)
    lines: list[str] = field(default_factory=list)        # détail pour le message
    changes: dict = field(default_factory=dict)           # masque → (ancien, nouveau)


def real_check(res) -> tuple[bool, str]:
    """Bot 3.2.0 : robot RÉEL du testeur (marge et stop-out du broker simulés) sur 60 j : sûr si aucun stop-out, aucun
    compte perdu (rechargé sous le seuil de reprise) et drawdown < DD_LIMIT. Contrôle des réglages actuels (pas de rejeu).
    """
    if res is None:
        return False, "pas de résultat du testeur"
    bad = []
    if res.stopouts > 0:
        bad.append(f"{res.stopouts} stop-out(s)")
    if res.reloads > 0:
        bad.append(f"compte perdu {res.reloads} fois")
    if res.dd_max >= DD_LIMIT:
        bad.append(f"drawdown {res.dd_max:.0f} %")
    if bad:
        return False, " · ".join(bad) + f" sur {SAFETY_DAYS} j"
    return True, (f"aucun stop-out, aucun compte perdu, drawdown max {res.dd_max:.0f} %, "
                  f"{res.net:+.2f} € sur {SAFETY_DAYS} j")


@dataclass
class Decision:
    change: Variant | None = None
    reason: str = ""
    table: list[Row] = field(default_factory=list)
    ref_unsafe: bool = False
    note: str = ""
    plateau: bool = False                                 # choix départagé par les résultats voisins
    slots: SlotPlan | None = None


def _stats(rows: list[dict], d_to: int):
    recent = [d for d in rows if int(d.get("fin", 0) or 0) >= d_to - CHOOSE_DAYS * 86400]
    s60 = labo.compute_scen([d for d in rows if d.get("res") != "saute"])
    s30 = labo.compute_scen([d for d in recent if d.get("res") != "saute"])
    return s30, s60


def _row(name: str, a30, a60) -> Row:
    safe = a60 is not None and a30 is not None and a60.lost == 0 and a30.lost == 0 and a60.dd_max < DD_LIMIT
    return Row(name, a30.won if a30 else 0, a30.launched if a30 else 0, a30.net if a30 else 0.0,
               a60.lost if a60 else 0, a60.dd_max if a60 else 0.0, a60.net if a60 else 0.0, safe)


def decide(rows: list[dict], vs: list[Variant], d_to: int, params: dict[str, str] | None = None) -> Decision:
    s30, s60 = _stats(rows, d_to)
    ref60, ref30 = s60.get("miroir"), s30.get("miroir")
    dec = Decision()
    if any(v.kind == "slot" for v in vs) and params is not None:
        dec.slots = slot_plan(rows, vs, params, d_to)
    if ref60 is None or ref30 is None or ref30.launched < MIN_GOALS:
        dec.note = f"pas assez d'objectifs sur {CHOOSE_DAYS} jours pour décider ({ref30.launched if ref30 else 0} < {MIN_GOALS})"
        return dec
    ref = _row("Réglages actuels", ref30, ref60)
    dec.table.append(ref)
    dec.ref_unsafe = not ref.safe
    tem: Row | None = None
    if any(v.kind == "temoin" for v in vs):
        tem = _row("Réglages actuels (calcul propre)", s30.get(TEMOIN), s60.get(TEMOIN))
        dec.table.append(tem)

    def base_of(v: Variant) -> Row:
        return tem if v.ref == TEMOIN and tem is not None and tem.n30 else ref

    cands: list[tuple[float, Variant, Row, Row]] = []
    rows_by_key: dict[str, tuple[Variant, Row, Row]] = {}
    safe_but_poor = 0
    for v in vs:
        if v.kind in ("temoin", "slot"):
            continue
        r = _row(v.name, s30.get(v.key), s60.get(v.key))
        dec.table.append(r)
        b = base_of(v)
        rows_by_key[v.key] = (v, r, b)
        if not r.safe or r.n30 == 0:
            continue
        sig = max(2.0, 0.05 * abs(b.net30))
        if dec.ref_unsafe:
            # réglages dangereux : la plus rentable des variantes SÛRES sur 60 j (pertes de compte comprises dans le net),
            # à condition de garder au moins 80 % du gain sur 30 j (pas de « trop prudent »)
            floor = PROFIT_FLOOR * b.net30 if b.net30 > 0 else b.net30 - sig
            if r.net30 < floor:
                safe_but_poor += 1
                continue
            score = r.net60 - b.net60
        else:
            if not (r.net30 >= b.net30 + sig and r.net60 >= b.net60 - sig):
                continue
            score = r.net30 - b.net30
        cands.append((score, v, r, b))
    if not cands:
        if dec.ref_unsafe and safe_but_poor:
            dec.note = (f"⚠️ tes réglages actuels perdent le compte ou frôlent la perte sur {SAFETY_DAYS} jours ; {safe_but_poor} "
                        f"variante(s) sûre(s) trouvée(s), mais elles gagnent moins de {PROFIT_FLOOR * 100:.0f} % de ton gain sur {CHOOSE_DAYS} jours : "
                        "rien n'est changé (trop prudent)")
        elif dec.ref_unsafe:
            dec.note = (f"⚠️ tes réglages actuels perdent le compte ou frôlent la perte sur {SAFETY_DAYS} jours, et aucune variante "
                        "testée cette nuit n'est sûre")
        else:
            dec.note = "aucune variante nettement meilleure et sûre : rien n'est changé"
        return dec
    # plateau : parmi les variantes presque aussi bonnes que la meilleure, celle entourée de bons résultats
    good = [(v, r) for v, r, b in rows_by_key.values() if r.safe and r.n30 and r.net30 >= b.net30]

    def support(v: Variant) -> int:
        mine = set(v.changes.items())
        return sum(1 for w, _ in good if w is not v and mine & set(w.changes.items()))
    top = max(c[0] for c in cands)
    tie = max(2.0, 0.05 * abs(ref.net30))
    near = [c for c in cands if c[0] >= top - tie]
    pick = max(near, key=lambda c: (support(c[1]), c[0]))
    dec.plateau = pick[0] < top and len(near) > 1
    _, v, r, b = pick
    dec.change = v
    vs_txt = " (par rapport aux réglages actuels recalculés de la même façon)" if b is tem else ""
    if dec.ref_unsafe:
        keep = f"{r.net30 / b.net30 * 100:.0f} % du gain gardé sur {CHOOSE_DAYS} j" if b.net30 > 0 else f"{r.net30 - b.net30:+.2f} € sur {CHOOSE_DAYS} j"
        dec.reason = (f"tes réglages actuels {'perdent le compte' if ref.lost60 else 'frôlent la perte du compte'} sur {SAFETY_DAYS} j "
                      f"(💀 {ref.lost60}, DD {ref.dd60:.0f} %) ; cette variante ne le perd jamais (DD {r.dd60:.0f} %), "
                      f"{keep}, {r.net60 - b.net60:+.2f} € sur {SAFETY_DAYS} j pertes comprises{vs_txt}")
    else:
        dec.reason = (f"{r.net30 - b.net30:+.2f} € sur {CHOOSE_DAYS} j (✅ {r.won30 - b.won30:+d} objectifs), "
                      f"aucun compte perdu sur {SAFETY_DAYS} j, drawdown max {r.dd60:.0f} %{vs_txt}")
    if dec.plateau:
        dec.reason += " ; préférée à une variante à peine meilleure car ses voisines gagnent aussi (valeur plus robuste)"
    return dec


def slot_plan(rows: list[dict], vs: list[Variant], params: dict[str, str], d_to: int) -> SlotPlan:
    """Retraits : créneaux actuels dont un objectif dépasse 80 % de drawdown (ou perd le compte) sur 60 j.
    Ajouts : heures testées seules, sous 80 % de drawdown, jamais de compte perdu, gain positif sur 60 j."""
    plan = SlotPlan()
    cur = slots_of(_masks(params, SLOT_KEYS))
    by_slot: dict[str, list[dict]] = {}
    for d in rows:
        if d.get("sc") == "miroir" and d.get("res") != "saute":
            by_slot.setdefault(str(d.get("creneau", "")), []).append(d)
    worst: dict[int, float] = {}
    for s in cur:
        lst = by_slot.get(slot_text(s), [])
        if not lst:
            continue
        dd = max(float(d.get("dd", 0) or 0) for d in lst)
        lost = any(d.get("res") in labo.LOSS_RES for d in lst)
        worst[s] = 100.0 if lost else dd
        if lost or dd > SLOT_DD:
            plan.removed.append(s)
            plan.lines.append(f"➖ {slot_text(s)} retiré : drawdown {dd:.0f} % sur {SAFETY_DAYS} j" + (" (compte perdu)" if lost else ""))
    _, s60 = _stats(rows, d_to)
    for v in vs:
        if v.kind != "slot":
            continue
        a = s60.get(v.key)
        if a is None or a.launched < SLOT_MIN_GOALS:
            continue
        if a.lost == 0 and a.dd_max <= SLOT_DD and a.net > 0:
            plan.added.append(v.hour * 60)
            plan.lines.append(f"➕ {v.hour:02d}:00 ajouté : testé seul, {a.won}/{a.launched} objectifs, {a.net:+.2f} €, "
                              f"drawdown max {a.dd_max:.0f} %")
    left = sorted(set([s for s in cur if s not in plan.removed] + plan.added))
    if cur and not left and plan.removed:                  # jamais zéro créneau : le moins risqué reste
        keep = min(plan.removed, key=lambda s: worst.get(s, 100.0))
        plan.removed.remove(keep)
        plan.kept_last = keep
        plan.lines = [ln for ln in plan.lines if not ln.startswith(f"➖ {slot_text(keep)} ")]
        plan.lines.append(f"⚠️ {slot_text(keep)} gardé malgré son drawdown ({worst.get(keep, 0):.0f} %) : c'est le dernier créneau")
        left = [keep]
    new = left
    old_m = _masks(params, SLOT_KEYS)
    new_m = masks_of(new)
    for k, o, n in zip(SLOT_KEYS, old_m, new_m):
        if o != n:
            plan.changes[k] = (str(o), str(n))
    return plan


def next_scale(scale: dict[str, int], idle: dict[str, int], keys: list[str], changed: set[str]) -> tuple[dict[str, int], dict[str, int]]:
    """Portée et compteurs après un passage de la catégorie : un réglage changé revient à 1 cran (on affine autour de la
    nouvelle valeur) ; un réglage testé sans changement compte un passage de plus ; au 3e, sa portée double
    (1 → 2 → 4 crans), puis revient à 1 cran après 4 crans."""
    sc, idl = dict(scale), dict(idle)
    for k in keys:
        if k in changed:
            sc[k], idl[k] = 1, 0
            continue
        idl[k] = int(idl.get(k, 0)) + 1
        if idl[k] >= IDLE_PASSES:
            cur = max(1, int(sc.get(k, 1)))
            sc[k] = 1 if cur >= MAX_SCALE else cur * 2
            idl[k] = 0
    return sc, idl
