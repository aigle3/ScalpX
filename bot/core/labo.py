"""Labo des filtres (EA 7.1.0) : scénarios perso, stockage et analyse.

L'EA envoie (événements silencieux, jamais notifiés) :
  - LABO_REEL  : fin d'un objectif RÉEL, secondes de blocage par filtre (signal présent, aucune série) et
                 secondes où ce filtre était le SEUL à bloquer ; entree_s = -1 si aucune série n'a été ouverte ;
  - LABO_SERIE : fin d'un objectif VIRTUEL d'un scénario (res : atteint, 500, coupe, annule, sl, liquide, saute) ;
  - LABO_ETAT  : état des comptes virtuels (toutes les 30 min).
Le bot écrit les scénarios perso dans Common\\Files\\labo_scenarios.txt (lu par tous les EA).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .fsio import atomic_write, read_text

SCEN_FILE = "labo_scenarios.txt"
CODES = ["atr", "ema", "vol", "lo", "hi", "can", "time", "news", "spread", "ext", "fresh"]
LABELS = ["Volatilité (ATR)", "Prix / EMA", "Volume", "EMA TF inférieur", "EMA TF supérieur", "Bougie TF sup.",
          "Horaire", "News", "Spread", "Extension", "Signal récent"]
TUNABLE = {0, 2, 5, 7, 8, 9, 10}                  # filtres à seuils (assouplir / durcir)
CYCLE_TUNE = ["=", "off", "on", "+10", "+20", "+30", "+50", "-10", "-20", "-30"]
CYCLE_PLAIN = ["=", "off", "on"]
MAX_CUSTOM = 7                                    # + 13 scénarios automatiques = 20 (maximum de l'EA)
LOSS_RES = ("sl", "liquide")
WIN_RES = ("atteint", "500")
MIN_GOALS = 5                                     # en dessous : pas de verdict


def tok_text(tok: str) -> str:
    if tok in ("", "="):
        return "comme le compte"
    if tok == "off":
        return "désactivé"
    if tok == "on":
        return "activé"
    if tok == "~":
        return "inversé"
    if tok.startswith("+"):
        return f"assoupli {tok[1:]} %"
    if tok.startswith("-"):
        return f"durci {tok[1:]} %"
    return tok


def next_tok(i: int, tok: str) -> str:
    cyc = CYCLE_TUNE if i in TUNABLE else CYCLE_PLAIN
    return cyc[(cyc.index(tok) + 1) % len(cyc)] if tok in cyc else cyc[0]


# ---- options d'un scénario (EA 7.3.0) : écart de grille, respirations du signal récent, timeframe de l'extension
OPT_CYCLES = {"g": ["1", "1.3", "1.6", "2"], "n": ["-1", "0", "2", "3", "5"], "x": ["0", "1", "2", "3"],
              "b": ["0", "6", "12", "18", "24", "36"],             # b (EA 7.4.0) : bougies max du signal récent
              "o": ["0", "0.3", "0.5", "0.75", "1", "1.5", "2"]}    # o (EA 7.5.0) : objectif en %
EXT_TF = {"1": "TF du robot", "2": "M15", "3": "H1"}


def opt_text(k: str, v: str) -> str:
    if k == "g":
        return "écart de grille comme le compte" if v in ("", "1") else f"écart de grille × {v}"
    if k == "n":
        if v in ("", "-1"):
            return "signal récent : recroisements comme le compte"
        return "signal récent : tout recroisement compte" if v == "0" else f"signal récent : respirations ≤ {v} bougies ignorées"
    if k == "x":
        return "extension comme le compte" if v in ("", "0") else f"extension mesurée en {EXT_TF.get(v, v)}"
    if k == "b":
        return "signal récent : bougies comme le compte" if v in ("", "0") else f"signal récent : {v} bougies max"
    if k == "o":
        return "objectif comme le compte" if v in ("", "0") else f"objectif {v.replace('.', ',')} %"
    return f"{k}={v}"


def opt_default(k: str, v: str) -> bool:
    return v in ("", "1") if k == "g" else (v in ("", "-1") if k == "n" else v in ("", "0"))   # x et b : "0" = comme le compte


def parse_opts(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (text or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() in OPT_CYCLES:
                out[k.strip()] = v.strip()
    return out


def opts_str(opts: dict[str, str]) -> str:
    return ";".join(f"{k}={v}" for k, v in opts.items() if k in OPT_CYCLES and not opt_default(k, v))


def next_opt(k: str, v: str) -> str:
    cyc = OPT_CYCLES[k]
    return cyc[(cyc.index(v) + 1) % len(cyc)] if v in cyc else cyc[1]


def auto_name(toks: list[str], opts: dict[str, str] | None = None) -> str:
    parts = [f"{LABELS[i]} {tok_text(t)}" for i, t in enumerate(toks) if t not in ("", "=")]
    parts += [opt_text(k, v) for k, v in (opts or {}).items() if k in OPT_CYCLES and not opt_default(k, v)]
    name = ", ".join(parts) if parts else "Identique au compte"
    return name if len(name) <= 70 else name[:69] + "…"


def _toks(**kw: str) -> list[str]:
    t = ["="] * len(CODES)
    for code, v in kw.items():
        t[CODES.index(code)] = v
    return t


# ---- ⭐ scénarios conseillés (à vérifier au labo et au backtest : ce sont des hypothèses, pas des certitudes)
PACK: list[tuple[str, list[str], dict[str, str], str]] = [
    ("🧱 Grille large", _toks(), {"g": "1.6"},
     "Écart de grille × 1,6 : un repli de 20-25 $ remplit 4-5 positions au lieu de 10 (moins de drawdown, TP plus loin)."),
    ("🌬 Signal récent sans respirations", _toks(fresh="on"), {"n": "3"},
     "Signal récent actif, un recroisement de 3 bougies ou moins ne remet pas le compteur à zéro (pas d'entrée tardive au sommet)."),
    ("📏 Extension H1", _toks(ext="+25"), {"x": "3"},
     "Pas d'achat (ou de vente) quand le prix est déjà à plus de 2,5 ATR H1 de l'EMA lente H1 : mouvement épuisé."),
    ("🛡 Prudent", _toks(ext="+25", fresh="on"), {"g": "1.6", "n": "3", "x": "3"},
     "Grille large + extension H1 + signal récent sans respirations : moins d'entrées, surtout moins de grilles profondes."),
    ("⚖️ Équilibré", _toks(ext="+50", fresh="off"), {"g": "1.3", "x": "3"},
     "Grille × 1,3, extension H1 tolérante (3 ATR), sans signal récent : garde les entrées tôt dans le mouvement."),
    ("🚀 Plus d'entrées, grille large", _toks(vol="off", can="off"), {"g": "1.6"},
     "Volume et bougie M15 désactivés (plus d'objectifs tentés), compensé par une grille plus large."),
    ("🧱🧱 Grille très large", _toks(), {"g": "2"},
     "Écart de grille × 2 : le plus résistant aux gros replis, mais séries plus longues."),
]


@dataclass
class Custom:
    key: str
    name: str
    toks: list[str]
    opts: dict = field(default_factory=dict)

    def line(self) -> str:
        name = self.name.replace("|", "/").replace("\n", " ")
        o = opts_str(self.opts)
        return f"{self.key}|{name}|{','.join(self.toks)}" + (f"|{o}" if o else "")


@dataclass
class LabConfig:
    active: bool = True
    reset: str = ""
    customs: list[Custom] = field(default_factory=list)

    def next_key(self) -> str:
        used = {c.key for c in self.customs}
        return next(f"p{i}" for i in range(1, 100) if f"p{i}" not in used)

    def text(self) -> str:
        lines = ["# ScalpX : scénarios perso du labo des filtres (écrit par le bot, lu par tous les EA)",
                 f"actif={1 if self.active else 0}"]
        if self.reset:
            lines.append(f"reset={self.reset}")
        lines += [c.line() for c in self.customs]
        return "\n".join(lines) + "\n"


def load_config(common: Path) -> LabConfig:
    cfg = LabConfig()
    txt = read_text(Path(common) / SCEN_FILE) or ""
    for raw in txt.splitlines():
        line = raw.strip().lstrip("﻿")
        if not line or line.startswith("#"):
            continue
        if line.startswith("actif="):
            cfg.active = line[6:].strip() != "0"
        elif line.startswith("reset="):
            cfg.reset = line[6:].strip()
        else:
            p = line.split("|")
            if len(p) >= 3 and p[0].startswith("p"):
                toks = [t.strip() or "=" for t in p[2].split(",")]
                toks = (toks + ["="] * len(CODES))[:len(CODES)]
                cfg.customs.append(Custom(p[0], p[1], toks, parse_opts(p[3]) if len(p) > 3 else {}))
    return cfg


def save_config(common: Path, cfg: LabConfig) -> None:
    Path(common).mkdir(parents=True, exist_ok=True)
    atomic_write(Path(common) / SCEN_FILE, cfg.text())


def new_reset_token(now: float | None = None) -> str:
    return str(int(now if now is not None else time.time()))


# ------------------------------------------------------------------ analyse
@dataclass
class ScenStat:
    key: str
    name: str = ""
    spec: str = ""
    launched: int = 0            # objectifs virtuels terminés (hors « sauté »)
    won: int = 0
    lost: int = 0                # SL d'urgence ou stop-out
    liquidations: int = 0
    missed: int = 0              # coupé / annulé (pas atteint, pas perdu)
    skipped: int = 0
    no_entry: int = 0            # objectif fini sans aucune série
    net: float = 0.0
    dd_max: float = 0.0
    ml_min: float = 0.0          # niveau de marge le plus bas (%)
    entry_s: list[int] = field(default_factory=list)

    @property
    def rate(self) -> float | None:
        return self.won / self.launched if self.launched else None

    @property
    def avg_entry_min(self) -> float | None:
        return sum(self.entry_s) / len(self.entry_s) / 60 if self.entry_s else None


@dataclass
class FilterBlock:
    code: str
    seconds: int = 0
    sole: int = 0
    goals_missed: int = 0        # objectifs réels sans série où ce filtre bloquait le plus


@dataclass
class RealStat:
    goals: int = 0
    won: int = 0
    no_entry: int = 0            # aucune série ouverte de tout l'objectif
    no_entry_filters: int = 0    # … alors qu'un filtre bloquait
    net: float = 0.0
    wait_s: int = 0
    signal_s: int = 0
    blocked_s: int = 0
    filters: dict[str, FilterBlock] = field(default_factory=dict)


@dataclass
class Verdict:
    icon: str
    text: str


def compute_real(rows: list[dict]) -> RealStat:
    r = RealStat(filters={c: FilterBlock(c) for c in CODES})
    for d in rows:
        r.goals += 1
        r.won += 1 if d.get("atteint") else 0
        r.net += float(d.get("profit", 0) or 0)
        r.wait_s += int(d.get("attente_s", 0) or 0)
        r.signal_s += int(d.get("signal_s", 0) or 0)
        r.blocked_s += int(d.get("bloque_s", 0) or 0)
        fl = d.get("filtres") or {}
        worst, worst_s = "", 0
        for c in CODES:
            v = fl.get(c) or [0, 0]
            sec = int(v[0]) if len(v) > 0 else 0
            sole = int(v[1]) if len(v) > 1 else 0
            r.filters[c].seconds += sec
            r.filters[c].sole += sole
            if sec > worst_s:
                worst, worst_s = c, sec
        if int(d.get("entree_s", -1)) < 0 and not d.get("atteint"):
            r.no_entry += 1
            if worst:
                r.no_entry_filters += 1
                r.filters[worst].goals_missed += 1
    return r


def compute_scen(rows: list[dict]) -> dict[str, ScenStat]:
    out: dict[str, ScenStat] = {}
    for d in rows:
        key = str(d.get("sc", ""))
        if not key:
            continue
        s = out.setdefault(key, ScenStat(key))
        s.name = str(d.get("nom", "") or s.name or key)
        s.spec = str(d.get("spec", "") or s.spec)
        res = str(d.get("res", ""))
        if res == "saute":
            s.skipped += 1
            continue
        s.launched += 1
        if res in WIN_RES:
            s.won += 1
        elif res in LOSS_RES:
            s.lost += 1
            s.liquidations += 1 if res == "liquide" else 0
        else:
            s.missed += 1
        s.net += float(d.get("profit", 0) or 0)
        s.dd_max = max(s.dd_max, float(d.get("dd", 0) or 0))
        ml = float(d.get("ml_min", 0) or 0)
        if ml > 0:
            s.ml_min = ml if s.ml_min == 0 else min(s.ml_min, ml)
        e = int(d.get("entree_s", -1))
        if e >= 0:
            s.entry_s.append(e)
        else:
            s.no_entry += 1
    return out


def verdict(s: ScenStat, ref: ScenStat | None) -> Verdict:
    """Comparaison avec les réglages actuels (même période, mêmes comptes, mêmes prix)."""
    if ref is None or s.key == ref.key:
        return Verdict("", "")
    if min(s.launched, ref.launched) < MIN_GOALS:
        return Verdict("⏳", "pas assez de données")
    more_losses = s.lost > ref.lost
    gain, dgoals, ddd = s.net - ref.net, s.won - ref.won, s.dd_max - ref.dd_max
    sig_net = max(2.0, 0.05 * abs(ref.net))                 # écart significatif : 5 % du gain (2 au moins)…
    sig_goals = max(3, round(0.03 * ref.launched))          # … ou 3 % des objectifs (3 au moins)
    much_deeper = ddd > 10
    every = s.key == "aucun"
    removes = every or (s.key.startswith("t") and (s.name.startswith("Sans ") or "off" in _diff_toks(s.spec, ref.spec)))
    better = gain > 0 and (gain >= sig_net or dgoals >= sig_goals) and not more_losses and not much_deeper
    worse = more_losses or gain <= -sig_net or dgoals <= -sig_goals
    if not more_losses and much_deeper:
        if gain > 0 and (gain >= sig_net or dgoals >= sig_goals):
            return Verdict("⚖️", f"plus de gain mais drawdown + {ddd:.0f} pts : plus risqué")
        return Verdict("🛡️", (f"filtre utile : sans lui, drawdown + {ddd:.0f} pts" if removes else f"plus risqué : drawdown + {ddd:.0f} pts")
                       + " sans gain qui le justifie")
    if not more_losses and not worse and ddd <= -10:
        return Verdict("🟢", f"moins risqué : drawdown {ddd:.0f} pts, gain équivalent")
    if better:
        if removes:
            return Verdict("🟢", "les filtres sont trop restrictifs" if every else "filtre trop restrictif : à assouplir ou désactiver")
        return Verdict("🟢", "meilleur que les réglages actuels")
    if worse:
        if more_losses:
            if removes:
                return Verdict("🛡️", "les filtres évitent des pertes" if every else "filtre utile : il évite des pertes")
            return Verdict("🛡️", "plus risqué : les réglages actuels évitent des pertes")
        return Verdict("🔴", "moins bon")
    return Verdict("⚪", "équivalent : différence trop faible pour conclure")


def _diff_toks(a: str, b: str) -> list[str]:
    ta, tb = a.split(","), b.split(",")
    return [x for x, y in zip(ta, tb) if x != y]


def spec_diff_text(spec: str, ref_spec: str) -> str:
    """« Volume désactivé, ATR assoupli 20 % » : ce que le scénario change par rapport aux réglages actuels."""
    ta, tb = spec.split(","), ref_spec.split(",")
    out = []
    for i, (x, y) in enumerate(zip(ta, tb)):
        if x != y and i < len(LABELS):
            out.append(f"{LABELS[i]} {tok_text(x)}")
    return ", ".join(out)


def rows_of(db_rows, kind: str) -> list[dict]:
    out = []
    for r in db_rows:
        if r["kind"] != kind:
            continue
        try:
            out.append(json.loads(r["data"]))
        except (TypeError, ValueError):
            continue
    return out


# ---- 🧪 tests rapides : un thème = jusqu'à 7 scénarios perso (remplacent les actuels) + un backtest lancé d'un bouton
def _q(name: str, opts: dict[str, str], **toks: str) -> tuple[str, list[str], dict[str, str]]:
    return name, _toks(**toks), opts


THEMES: dict[str, tuple[str, str, list]] = {
    "bougies": ("⏱ Bougies après le croisement",
                "Filtre « signal récent » : pas de nouvelle série si les EMA du robot se sont croisées il y a plus de N bougies "
                "(M5 : 6 = 30 min, 12 = 1 h, 24 = 2 h). Avec ou sans les respirations ignorées (petit recroisement de 3 bougies "
                "ou moins qui ne remet pas le compteur à zéro, cas du 02/11).",
                [_q("6 bougies (30 min)", {"b": "6"}, fresh="on"),
                 _q("12 bougies (1 h)", {"b": "12"}, fresh="on"),
                 _q("24 bougies (2 h)", {"b": "24"}, fresh="on"),
                 _q("6 bougies, respirations ignorées", {"b": "6", "n": "3"}, fresh="on"),
                 _q("12 bougies, respirations ignorées", {"b": "12", "n": "3"}, fresh="on"),
                 _q("24 bougies, respirations ignorées", {"b": "24", "n": "3"}, fresh="on"),
                 _q("36 bougies, respirations ignorées", {"b": "36", "n": "3"}, fresh="on")]),
    "grille": ("🧱 Écart de grille",
               "Même stratégie, ordres de grille plus serrés ou plus espacés : plus large = moins de positions dans un repli "
               "(drawdown plus faible) mais TP moyen plus loin.",
               [_q(f"Grille × {g}", {"g": g}) for g in ("0.8", "1.2", "1.4", "1.6", "1.8", "2", "2.5")]),
    "extension": ("📏 Extension (mouvement déjà épuisé)",
                  "Pas d'entrée quand le prix est déjà loin de l'EMA lente (en ATR) : mesuré sur le TF du robot, en M15 ou en H1, "
                  "avec le seuil du compte (2 ATR par défaut), + 25 % ou + 50 %.",
                  [_q("Extension TF du robot, seuil du compte", {"x": "1"}, ext="on"),
                   _q("Extension TF du robot, seuil + 50 %", {"x": "1"}, ext="+50"),
                   _q("Extension M15, seuil du compte", {"x": "2"}, ext="on"),
                   _q("Extension M15, seuil + 50 %", {"x": "2"}, ext="+50"),
                   _q("Extension H1, seuil du compte", {"x": "3"}, ext="on"),
                   _q("Extension H1, seuil + 25 %", {"x": "3"}, ext="+25"),
                   _q("Extension H1, seuil + 50 %", {"x": "3"}, ext="+50")]),
    "conseilles": ("🛡 Combinaisons conseillées",
                   "Les 7 scénarios ⭐ (grille large, signal récent sans respirations, extension H1, prudent, équilibré…).",
                   [(n, t, o) for n, t, o, _ in PACK]),
    "objectif": ("🎯 Objectif (% par créneau)",
                 "Même stratégie, objectif différent à chaque créneau : 0,3 % à 2 % du solde. Plus haut = plus de gain par "
                 "objectif mais séries plus longues (plus de coupures et de risque). Les autres réglages restent ceux du compte.",
                 [_q(f"Objectif {o.replace('.', ',')} %", {"o": o}) for o in ("0.3", "0.5", "0.75", "1", "1.25", "1.5", "2")]),
    "filtres": ("🔎 Chaque filtre seul",
                "Aucun scénario perso : seulement les 13 scénarios automatiques (réglages actuels, chaque filtre inversé "
                "— « Sans X » / « Avec X » —, aucun filtre). C'est le test « toutes les possibilités des filtres » un par un.",
                []),
}


def apply_theme(cfg: LabConfig, theme: str) -> list[str]:
    """Remplace les scénarios perso par ceux du thème ; renvoie leurs noms."""
    _, _, items = THEMES[theme]
    cfg.customs = []
    for name, toks, opts in items[:MAX_CUSTOM]:
        cfg.customs.append(Custom(cfg.next_key(), name, list(toks), dict(opts)))
    return [c.name for c in cfg.customs]


# ---- 🎯 En bref : conclusions lisibles, avec un test de fiabilité (même verdict sur les deux moitiés de la période)
SURE = ("🟢", "🛡️", "🔴", "⚖️")


def _action(s: ScenStat) -> str:
    n = s.name or s.key
    if s.key.startswith("t") and n.startswith("Sans "):
        return "désactiver " + n[5:]
    if s.key.startswith("t") and n.startswith("Avec "):
        return "activer " + n[5:]
    if s.key == "aucun":
        return "tout désactiver"
    return n


def _keep(s: ScenStat) -> str:
    n = s.name or s.key
    return ("garder " + n[5:]) if n.startswith("Sans ") else ("ne pas " + _action(s))


def brief(rows: list[dict], slots: dict | None = None) -> list[str]:
    """rows : données LABO_SERIE (avec « fin », horodatage) de la période ; slots : créneau → [lancés, atteints].
    Renvoie quelques lignes : ce qui est fiable, ce qui ne l'est pas encore, les créneaux faibles, le risque."""
    rows = [d for d in rows if d.get("res") != "saute"]
    all_s = compute_scen(rows)
    ref = all_s.get("miroir")
    if ref is None or ref.launched < MIN_GOALS:
        return ["🎯 <b>En bref</b> : pas encore assez d'objectifs pour conclure (5 au moins par scénario)."]
    ts = sorted(int(d.get("fin", 0) or 0) for d in rows)
    mid = ts[len(ts) // 2] if ts else 0
    halves = [compute_scen([d for d in rows if int(d.get("fin", 0) or 0) < mid]),
              compute_scen([d for d in rows if int(d.get("fin", 0) or 0) >= mid])]
    saves, sure_change, sure_keep, unsure, neutral = [], [], [], [], []
    for s in all_s.values():
        if s.key == "miroir":
            continue
        v = verdict(s, ref)
        hv = [verdict(h[s.key], h["miroir"]).icon if s.key in h and "miroir" in h else "⏳" for h in halves]
        stable = v.icon in SURE and all(i == v.icon for i in hv)
        gain = f"{s.net - ref.net:+.2f}"
        if s.lost == 0 < ref.lost and s.net >= ref.net:       # aucun compte perdu là où les réglages actuels en perdent
            saves.append((ref.lost - s.lost, s.net - ref.net,
                          f"{_action(s)} (💀 {s.lost} au lieu de {ref.lost}, {gain}, DD {s.dd_max:.0f} %)"))
        elif s.lost > 0 and s.lost < ref.lost:                 # moins de pertes, mais en perd encore : pas une solution
            unsure.append(f"{s.name or s.key} (💀 {s.lost} au lieu de {ref.lost} : perd encore le compte)")
        elif v.icon == "🟢" and stable and s.lost == 0:
            sure_change.append((s.net - ref.net, f"{_action(s)} ({gain}, ✅ {s.won - ref.won:+d}, DD {s.dd_max:.0f} %)"))
        elif v.icon == "🛡️":                                 # pertes / drawdown : événements rares, pas de test des moitiés
            sure_keep.append(f"{_keep(s)} (sans : 💀 {s.lost}, DD {s.dd_max:.0f} %)")
        elif s.lost > 0 and v.icon in ("🟢", "⚖️"):
            unsure.append(f"{s.name or s.key} (gagne plus mais 💀 {s.lost})")
        elif v.icon in ("🟢", "⚖️"):
            unsure.append(f"{s.name or s.key} {v.icon}")
        elif v.icon in ("⚪", "🔴"):
            neutral.append(s.name or s.key)
    out = [f"🎯 <b>En bref</b> <i>({ref.launched} objectifs ; « fiable » = même résultat sur les deux moitiés de la période)</i>"]
    if ref.lost:
        out.append(f"🚨 <b>Tes réglages actuels perdent le compte {ref.lost} fois</b> sur la période (stop-out ou SL d'urgence).")
    if saves:
        out.append("🛟 <b>Évitent les pertes</b> : " + " · ".join(t for *_, t in sorted(saves, reverse=True)[:4]))
    if sure_change:
        out.append("✅ <b>Changements fiables</b> : " + " · ".join(t for _, t in sorted(sure_change, reverse=True)[:4]))
    elif not saves:
        out.append("✅ Aucun changement fiable : tes réglages actuels tiennent.")
    if sure_keep:
        out.append("🛡 <b>À garder</b> : " + " · ".join(sure_keep[:3]))
    if unsure:
        out.append("❓ <b>Pas encore sûr</b> : " + ", ".join(unsure[:5]))
    if neutral:
        out.append(f"⚪ Peu ou pas d'effet : {len(neutral)} scénario(s)")
    weak = [f"{k} ({w}/{n})" for k, (n, w) in sorted((slots or {}).items()) if n >= 5 and w / n < 0.6]
    if weak:
        out.append("🕐 <b>Créneaux faibles</b> : " + ", ".join(weak) + " (souvent coupés ou ratés)")
    out.append(f"⚠️ Risque des réglages actuels : drawdown max {ref.dd_max:.0f} %" + (" (élevé)" if ref.dd_max >= 30 else ""))
    return out


def real_slots(rows_reel: list[dict]) -> dict[str, list[int]]:
    """Créneau → [objectifs réels lancés, atteints] (journal LABO_REEL)."""
    out: dict[str, list[int]] = {}
    for d in rows_reel:
        k = str(d.get("creneau", "") or "")
        if not k:
            continue
        e = out.setdefault(k, [0, 0])
        e[0] += 1
        e[1] += 1 if d.get("atteint") else 0
    return out
