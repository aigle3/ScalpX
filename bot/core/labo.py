"""Séries virtuelles de l'optimiseur (EA 7.7.0) : lecture et statistiques des objectifs virtuels.

Dans le backtest de l'optimiseur, l'EA joue en parallèle les réglages actuels (« miroir »), un témoin et les variantes ;
chaque fin d'objectif virtuel est un événement LABO_SERIE (res : atteint, 500, coupe, annule, sl, liquide, saute),
rangé dans la table « labo » sous l'identifiant bt_<numéro du backtest>.
En réel, l'EA n'envoie plus que LABO_ETAT (slippage et commission mesurés sur les vrais ordres, repris par les backtests).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

CODES = ["atr", "ema", "vol", "lo", "hi", "can", "time", "news", "spread", "ext", "fresh"]
LABELS = ["Volatilité (ATR)", "Prix / EMA", "Volume", "EMA TF inférieur", "EMA TF supérieur", "Bougie TF sup.",
          "Horaire", "News", "Spread", "Extension", "Signal récent"]
LOSS_RES = ("sl", "liquide")
WIN_RES = ("atteint", "500")


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
