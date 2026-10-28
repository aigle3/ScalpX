"""Surveillance périodique (sans Telegram) : statuts → historique, événements → notifications.

Le Monitor lit les fichiers de l'EA, alimente SQLite et produit des `Notice` que la couche Telegram
regroupe et envoie. Aucune connexion aux comptes MT5.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from storage.db import Database
from .config import Config
from .events import EventReader
from .models import Evenement
from .status import StatusReader

log = logging.getLogger(__name__)

# priorités : 0 info (silencieux), 1 important, 2 critique (sonne toujours, même en mode silencieux)
CRITICAL_TYPES = {"CLOTURE_500_LANCEE", "CLOTURE_500_OK", "CLOTURE_500_SOUS_SEUIL", "ARRET_500",
                  "REPRISE_RETRAIT", "CAPITAL_INSUFFISANT", "CONNEXION_PERDUE"}
SILENT_TYPES = {"DEMARRAGE_EA", "ARRET_EA", "CASHFLOW", "ETAT"}
GROUP_ICON = {"CLOTURE_500_LANCEE": "🔒", "CLOTURE_500_OK": "🏁", "ARRET_500": "🏁", "CLOTURE_500_SOUS_SEUIL": "↩️",
              "REPRISE_RETRAIT": "🟢", "CAPITAL_INSUFFISANT": "⚠️"}


@dataclass
class Notice:
    iid: str
    label: str
    kind: str
    text: str
    prio: int = 1
    photo: Path | None = None
    ts: int = field(default_factory=lambda: int(time.time()))


Emit = Callable[[Notice], Awaitable[None]]


def event_to_notice(ev: Evenement, label: str, common: Path) -> Notice | None:
    """Transforme un événement de l'EA en notification (None = stocké mais non notifié)."""
    if ev.type == "CASHFLOW" and not ev.texte:
        return None
    prio = 2 if ev.type in CRITICAL_TYPES else (0 if ev.type in SILENT_TYPES else max(0, min(2, ev.prio)))
    if ev.type == "ETAT":
        prio = 0
    photo = None
    if ev.type in ("CAPTURE", "COURBE"):
        f = ev.data.get("fichier") if isinstance(ev.data, dict) else None
        if f:
            p = Path(common) / str(f)
            photo = p if p.exists() else None
    text = ev.texte or ev.type
    return Notice(ev.id, label, ev.type, text, prio, photo, ev.ts or int(time.time()))


class Monitor:
    def __init__(self, cfg: Config, db: Database, status: StatusReader, events: EventReader, emit: Emit) -> None:
        self.cfg, self.db, self.status, self.events, self.emit = cfg, db, status, events, emit
        self._last_sample: dict[str, float] = {}
        self._last_arret_balance: dict[str, float] = {}
        self._last_state: dict[str, str] = {}
        self._seen_version: dict[str, str] = {}

    # ---- statuts → historique
    def poll_status(self, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        for inst in self.db.list():
            st = self.status.read(inst.id)
            if st is None:
                continue
            if st.etat == "ARRET_500":
                self._last_arret_balance[inst.id] = st.solde
            self._last_state[inst.id] = st.etat
            if st.devise and st.devise != inst.currency:
                self.db.set_currency(inst.id, st.devise)
            if st.version and st.version != inst.ea_version:
                self.db.set_version(inst.id, st.version, "heartbeat")
            if now - self._last_sample.get(inst.id, 0) >= 60 and st.age_s(now) < self.cfg.timing.heartbeat_stale_s * 4:
                self._last_sample[inst.id] = now
                self.db.add_balance(inst.id, st.solde, st.equity, st.flottant_ea, st.cycle, st.etat, int(now))

    # ---- événements → historique + notifications
    async def poll_events(self) -> int:
        n = 0
        for inst in self.db.list():
            for ev in self.events.drain(inst.id):
                n += 1
                self.db.add_event(inst.id, ev.ts or int(time.time()), ev.type, ev.prio, ev.texte)
                self._track(inst.id, ev)
                notice = event_to_notice(ev, inst.label, self.cfg.paths.common())
                if notice is not None:
                    await self.emit(notice)
        return n

    def _track(self, iid: str, ev: Evenement) -> None:
        d = ev.data if isinstance(ev.data, dict) else {}
        ts = ev.ts or int(time.time())
        if ev.type == "CLOTURE_500_OK":
            self.db.add_closure500(iid, "OK", float(d.get("solde", 0)), float(d.get("ecart", 0)), ts)
        elif ev.type == "CLOTURE_500_SOUS_SEUIL":
            self.db.add_closure500(iid, "SOUS_SEUIL", float(d.get("solde", 0)), -float(d.get("manque", 0)), ts)
        elif ev.type == "REPRISE_RETRAIT":
            after = float(d.get("solde", 0))
            before = self._last_arret_balance.get(iid, 0.0)
            amount = round(before - after, 2) if before > after else 0.0
            if amount > 0:
                self.db.add_withdrawal(iid, amount, before, after, ts)
            started = int(self.db.meta_get(f"cycle_start_{iid}", "0") or 0)
            cyc = int(d.get("cycle", 1)) - 1
            self.db.add_cycle(iid, cyc, started, ts, before, amount)
            self.db.meta_set(f"cycle_start_{iid}", str(ts))
            self._last_arret_balance.pop(iid, None)
        elif ev.type == "DEMARRAGE_EA" and not self.db.meta_get(f"cycle_start_{iid}"):
            self.db.meta_set(f"cycle_start_{iid}", str(ts))
