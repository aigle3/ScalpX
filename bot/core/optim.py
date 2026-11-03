"""Optimiseur autonome (EA 7.6.0+) : chaque nuit, un groupe de réglages est testé autour des valeurs actuelles du compte
de référence, dans UN backtest de 60 jours où chaque variante est un scénario du labo (valeurs absolues).

Choix sur les 30 derniers jours, sécurité sur 60 jours :
  - une variante n'est retenue que si elle ne perd JAMAIS le compte (60 j et 30 j) et reste sous DD_LIMIT de drawdown ;
  - réglages actuels sûrs : la variante doit gagner nettement plus sur 30 j sans perdre sur 60 j ;
  - réglages actuels dangereux (compte perdu ou drawdown ≥ DD_LIMIT sur 60 j) : la variante sûre la plus rentable sur 30 j ;
  - chaque variante bouge d'UN cran (pas) ; les combinaisons de deux réglages sont testées ensemble la même nuit,
    donc la meilleure variante (simple ou combinée) est appliquée telle quelle.
Les groupes tournent d'une nuit à l'autre ; comme les variantes sont toujours centrées sur les valeurs du moment,
la recherche s'affine d'elle-même autour des meilleures valeurs.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import labo

DD_LIMIT = 90.0                     # drawdown (%) au-delà duquel une variante « frôle la perte du compte »
CHOOSE_DAYS = 30                    # période de choix (marché récent)
SAFETY_DAYS = 60                    # période de sécurité (aucun compte perdu)
MIN_GOALS = 10                      # objectifs minimum sur 30 j pour décider

# clé de l'EA → (libellé, pas, minimum, maximum, option du labo, unité affichée)
NUM: dict[str, tuple[str, float, float, float, str, str]] = {
    "gridatr": ("Multiplicateur ATR de la grille", 0.1, 0.5, 2.0, "ga", " × ATR"),
    "gridmin": ("Grille minimum", 50, 100, 800, "gn", " pts"),
    "gridmax": ("Grille maximum", 100, 300, 1500, "gx", " pts"),
    "maxpend": ("Ordres en attente max", 1, 3, 15, "mp", ""),
    "lotmult": ("Multiplicateur de lot", 0.05, 1.0, 1.5, "lm", " ×"),
    "sl_pct": ("Objectif", 0.1, 0.2, 1.5, "o", " %"),
    "freshbars": ("Signal récent : bougies max", 6, 6, 60, "b", " bougies"),
}
# filtre du labo → réglage de l'EA (le spread n'est jamais retiré : il protège l'exécution)
FILTERS: dict[str, str] = {"atr": "f_atr", "ema": "f_ema", "vol": "f_vol", "lo": "f_lo", "hi": "f_hi", "can": "f_can",
                           "time": "f_time", "news": "news_block", "ext": "f_ext", "fresh": "f_fresh"}
GROUPS: list[tuple[str, str, list[str]]] = [
    ("grille", "🧱 Grille", ["gridatr", "gridmin", "gridmax"]),
    ("lots", "📦 Lots", ["maxpend", "lotmult"]),
    ("objectif", "🎯 Objectif et signal récent", ["sl_pct", "freshbars"]),
    ("filtres", "🛡️ Filtres", []),
]


def fnum(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def label_of(key: str, raw: str) -> str:
    """« Multiplicateur ATR de la grille : 1,1 × ATR » / « Filtre volume : désactivé »."""
    if key in NUM:
        name, *_, unit = NUM[key]
        try:
            return f"{name} : {fnum(float(raw))}{unit}"
        except ValueError:
            return f"{name} : {raw}"
    code = next((c for c, k in FILTERS.items() if k == key), None)
    name = labo.LABELS[labo.CODES.index(code)] if code else key
    return f"Filtre {name} : {'activé' if raw == '1' else 'désactivé'}"


def value_text(key: str, raw: str) -> str:
    if key in NUM:
        try:
            return f"{fnum(float(raw))}{NUM[key][5]}"
        except ValueError:
            return raw
    return "activé" if raw == "1" else "désactivé"


SHORT = {"gridatr": "ATR grille", "gridmin": "Grille min", "gridmax": "Grille max", "maxpend": "Ordres max",
         "lotmult": "Mult. lot", "sl_pct": "Objectif", "freshbars": "Signal récent"}


@dataclass
class Variant:
    key: str                      # clé du scénario du labo (p1…)
    name: str
    changes: dict                 # réglage de l'EA → (valeur actuelle, valeur testée) ; plusieurs = combinaison testée ensemble
    toks: list[str] = field(default_factory=lambda: ["="] * len(labo.CODES))
    opts: dict[str, str] = field(default_factory=dict)

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


def group_applies(params: dict[str, str], group: str) -> bool:
    return bool(variants(params, group))


def _raw(key: str, v: float) -> str:
    return str(int(v)) if float(v).is_integer() and key in ("gridmin", "gridmax", "maxpend", "freshbars") else f"{v:g}"


def variants(params: dict[str, str], group: str) -> list[Variant]:
    """Variantes de la nuit. Réglages numériques : chacun à −1 et +1 cran, PUIS les combinaisons de deux réglages
    (±1 cran chacun), testées ensemble dans le même backtest ; filtres : chacun inversé."""
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
            out.append(Variant("", f"{labo.LABELS[i]} {'désactivé' if new == '0' else 'activé'}", {key: (params[key], new)}, toks))
    else:
        moves: dict[str, list[str]] = {}
        for key in g[2]:
            if key not in params or key not in NUM:
                continue
            if key in ("gridatr", "gridmin", "gridmax") and params.get("dyngrid", "1") != "1":
                continue                                            # grille fixe : ces réglages n'agissent pas
            if key == "freshbars" and params.get("f_fresh", "0") != "1":
                continue                                            # signal récent désactivé
            try:
                cur = float(params[key])
            except ValueError:
                continue
            _, step, lo, hi, _, _ = NUM[key]
            m = []
            for d in (-1, 1):
                v = round(min(hi, max(lo, cur + d * step)), 4)
                if abs(v - cur) > 1e-9:
                    m.append(_raw(key, v))
            if m:
                moves[key] = m

        def valid(vals: dict[str, str]) -> bool:
            mn = float(vals.get("gridmin", params.get("gridmin", "0")) or 0)
            mx = float(vals.get("gridmax", params.get("gridmax", "1e9")) or 1e9)
            return mn < mx

        def add(vals: dict[str, str]) -> None:
            if not valid(vals):
                return
            if len(vals) == 1:
                k, r = next(iter(vals.items()))
                name = f"{NUM[k][0]} {fnum(float(r))}{NUM[k][5]}"
            else:
                name = " + ".join(f"{SHORT[k]} {fnum(float(r))}{NUM[k][5]}" for k, r in vals.items())
            out.append(Variant("", name, {k: (params[k], r) for k, r in vals.items()}, ["="] * len(labo.CODES),
                               {NUM[k][4]: r for k, r in vals.items()}))

        keys = list(moves)
        for k in keys:
            for r in moves[k]:
                add({k: r})
        for i, k1 in enumerate(keys):
            for k2 in keys[i + 1:]:
                for r1 in moves[k1]:
                    for r2 in moves[k2]:
                        add({k1: r1, k2: r2})
    for n, v in enumerate(out[:19], 1):
        v.key = f"p{n}"
    return out[:19]


def scenario_file(vs: list[Variant]) -> str:
    """Fichier de scénarios du backtest de l'optimiseur : réglages actuels + variantes, sans les scénarios automatiques."""
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
    return str(int(v)) if float(v).is_integer() and param in ("gridmin", "gridmax", "maxpend", "freshbars") else f"{v:g}"


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
class Decision:
    change: Variant | None = None
    reason: str = ""
    table: list[Row] = field(default_factory=list)
    ref_unsafe: bool = False
    note: str = ""


def decide(rows: list[dict], vs: list[Variant], d_to: int) -> Decision:
    recent = [d for d in rows if int(d.get("fin", 0) or 0) >= d_to - CHOOSE_DAYS * 86400]
    s60 = labo.compute_scen([d for d in rows if d.get("res") != "saute"])
    s30 = labo.compute_scen([d for d in recent if d.get("res") != "saute"])
    ref60, ref30 = s60.get("miroir"), s30.get("miroir")
    dec = Decision()
    if ref60 is None or ref30 is None or ref30.launched < MIN_GOALS:
        dec.note = f"pas assez d'objectifs sur {CHOOSE_DAYS} jours pour décider ({ref30.launched if ref30 else 0} < {MIN_GOALS})"
        return dec

    def row(name: str, a30, a60) -> Row:
        safe = a60 is not None and a30 is not None and a60.lost == 0 and a30.lost == 0 and a60.dd_max < DD_LIMIT
        return Row(name, a30.won if a30 else 0, a30.launched if a30 else 0, a30.net if a30 else 0.0,
                   a60.lost if a60 else 0, a60.dd_max if a60 else 0.0, a60.net if a60 else 0.0, safe)

    ref = row("Réglages actuels", ref30, ref60)
    dec.table.append(ref)
    dec.ref_unsafe = not ref.safe
    sig = max(2.0, 0.05 * abs(ref.net30))
    best: tuple[float, Variant, Row] | None = None
    for v in vs:
        r = row(v.name, s30.get(v.key), s60.get(v.key))
        dec.table.append(r)
        if not r.safe or r.n30 == 0:
            continue
        if not dec.ref_unsafe and not (r.net30 >= ref.net30 + sig and r.net60 >= ref.net60 - sig):
            continue
        if best is None or r.net30 > best[0]:
            best = (r.net30, v, r)
    if best is None:
        dec.note = ("⚠️ tes réglages actuels perdent le compte ou frôlent la perte sur 60 jours, et aucune variante testée "
                    "cette nuit n'est sûre" if dec.ref_unsafe else "aucune variante nettement meilleure et sûre : rien n'est changé")
        return dec
    _, v, r = best
    dec.change = v
    why = (f"{r.net30 - ref.net30:+.2f} € sur {CHOOSE_DAYS} j (✅ {r.won30 - ref.won30:+d} objectifs), "
           f"aucun compte perdu sur {SAFETY_DAYS} j, drawdown max {r.dd60:.0f} %")
    if dec.ref_unsafe:
        why = (f"tes réglages actuels {'perdent le compte' if ref.lost60 else 'frôlent la perte du compte'} sur {SAFETY_DAYS} j "
               f"(💀 {ref.lost60}, DD {ref.dd60:.0f} %) ; cette variante est sûre : " + why)
    dec.reason = why
    return dec
