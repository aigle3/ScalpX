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
MAX_CUSTOM = 6
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


def auto_name(toks: list[str]) -> str:
    parts = [f"{LABELS[i]} {tok_text(t)}" for i, t in enumerate(toks) if t not in ("", "=")]
    name = ", ".join(parts) if parts else "Identique au compte"
    return name if len(name) <= 70 else name[:69] + "…"


@dataclass
class Custom:
    key: str
    name: str
    toks: list[str]

    def line(self) -> str:
        name = self.name.replace("|", "/").replace("\n", " ")
        return f"{self.key}|{name}|{','.join(self.toks)}"


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
                cfg.customs.append(Custom(p[0], p[1], toks))
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
    much_deeper = s.dd_max > ref.dd_max + 10
    better = s.won >= ref.won and s.net > ref.net and not more_losses and not much_deeper
    worse = more_losses or (s.net < ref.net and s.won <= ref.won)
    every = s.key == "aucun"
    removes = every or (s.key.startswith("t") and (s.name.startswith("Sans ") or "off" in _diff_toks(s.spec, ref.spec)))
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
    return Verdict("⚪", "équivalent")


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
