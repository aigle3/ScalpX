"""Statistiques par créneau d'objectif automatique, reconstituées à partir des événements de l'EA déjà enregistrés.

Un essai = un événement OBJECTIF_AUTO (« 🎯 Créneau 9:30 : objectif +1.00 % lancé. »), puis sa fin sur le même compte :
  - OBJECTIF_ATTEINT          → gagné (profit, durée, drawdown max lus dans le texte) ;
  - NOTIF « STOP LOSS D'URGENCE » → perdu (perte lue dans le texte) ;
  - arrêt à 500 (CLOTURE_500_OK / ARRET_500) → gagné (l'objectif est absorbé par la clôture à 500) ;
  - OBJECTIF_ANNULE (EA 6.6.0 : aucune série ouverte dans la fenêtre d'entrée) → annulé (hors taux, compté à part) ;
  - nouvel OBJECTIF_AUTO sans fin connue, ou rien encore → interrompu / en cours (exclus des taux).
« ⛔ LIMITE DE PERTE DE SESSION » pendant l'essai est compté à part (le robot se met en pause, l'objectif continue).
Les heures sont celles du fuseau réglé dans l'EA (celles des créneaux).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_SLOT = re.compile(r"Créneau (\d{1,2}:\d{2})")
_PROFIT = re.compile(r"Profit : ([+-]?\d+(?:\.\d+)?)")
_DD = re.compile(r"Drawdown max : (\d+(?:\.\d+)?) %")
_LOSS = re.compile(r"Perte à l'instant : (\d+(?:\.\d+)?)")


@dataclass
class SlotStat:
    slot: str
    launched: int = 0
    won: int = 0
    lost: int = 0                  # stop loss d'urgence
    open: int = 0                  # en cours ou interrompu (hors taux)
    skipped: int = 0               # créneau sauté (objectif précédent pas fini, solde…)
    cancelled: int = 0             # annulé : aucune série ouverte dans la fenêtre d'entrée
    session_stops: int = 0         # limite de perte de session atteinte pendant l'essai
    gains: list[float] = field(default_factory=list)
    losses: list[float] = field(default_factory=list)
    durations: list[int] = field(default_factory=list)
    dds: list[float] = field(default_factory=list)

    @property
    def done(self) -> int:
        return self.won + self.lost

    @property
    def rate(self) -> float | None:
        return self.won / self.done if self.done else None

    @property
    def net(self) -> float:
        return sum(self.gains) - sum(self.losses)

    @property
    def avg_gain(self) -> float:
        return sum(self.gains) / len(self.gains) if self.gains else 0.0

    @property
    def avg_loss(self) -> float:
        return sum(self.losses) / len(self.losses) if self.losses else 0.0

    @property
    def avg_minutes(self) -> float:
        return sum(self.durations) / len(self.durations) / 60 if self.durations else 0.0

    @property
    def worst_dd(self) -> float:
        return max(self.dds) if self.dds else 0.0


def _num(rx: re.Pattern, text: str) -> float | None:
    m = rx.search(text or "")
    return float(m.group(1)) if m else None


def slot_key(slot: str) -> tuple[int, int]:
    h, _, m = slot.partition(":")
    return int(h), int(m or 0)


def compute(events) -> dict[str, SlotStat]:
    """events : lignes (iid, ts, type, text) dans l'ordre chronologique → {« H:MM »: SlotStat}."""
    stats: dict[str, SlotStat] = {}
    running: dict[str, dict] = {}                      # compte → essai en cours

    def get(slot: str) -> SlotStat:
        return stats.setdefault(slot, SlotStat(slot))

    def close(iid: str, won: bool, ts: int, gain: float | None = None, loss: float | None = None, dd: float | None = None):
        r = running.pop(iid, None)
        if r is None:
            return
        s = get(r["slot"])
        s.open -= 1
        if won:
            s.won += 1
            if gain is not None:
                s.gains.append(gain)
        else:
            s.lost += 1
            if loss is not None:
                s.losses.append(loss)
        s.durations.append(max(0, int(ts) - r["ts"]))
        if dd is not None:
            s.dds.append(dd)

    for e in events:
        iid, ts, typ, text = e["iid"], int(e["ts"]), e["type"], e["text"] or ""
        if typ == "OBJECTIF_AUTO":
            m = _SLOT.search(text)
            if not m:
                continue
            running.pop(iid, None)                     # le précédent n'a pas de fin connue : reste « interrompu »
            s = get(m.group(1))
            s.launched += 1
            s.open += 1
            running[iid] = {"slot": m.group(1), "ts": ts}
        elif typ == "CRENEAU_SAUTE":
            m = _SLOT.search(text)
            if m:
                get(m.group(1)).skipped += 1
        elif iid not in running:
            continue
        elif typ == "OBJECTIF_ATTEINT":
            close(iid, True, ts, gain=_num(_PROFIT, text), dd=_num(_DD, text))
        elif typ == "OBJECTIF_ANNULE":
            r = running.pop(iid)
            s = get(r["slot"])
            s.open -= 1
            s.cancelled += 1
        elif typ in ("CLOTURE_500_OK", "ARRET_500"):
            close(iid, True, ts)
        elif typ == "NOTIF" and "STOP LOSS D'URGENCE" in text:
            close(iid, False, ts, loss=_num(_LOSS, text))
        elif typ == "NOTIF" and "LIMITE DE PERTE DE SESSION" in text:
            get(running[iid]["slot"]).session_stops += 1
    return stats


def total(stats: dict[str, SlotStat]) -> SlotStat:
    t = SlotStat("Total")
    for s in stats.values():
        t.launched += s.launched; t.won += s.won; t.lost += s.lost; t.open += s.open
        t.skipped += s.skipped; t.session_stops += s.session_stops; t.cancelled += s.cancelled
        t.gains += s.gains; t.losses += s.losses; t.durations += s.durations; t.dds += s.dds
    return t
