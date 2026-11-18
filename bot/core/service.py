"""Surveillance périodique (sans Telegram) : statuts → historique, événements → notifications.

Le Monitor lit les fichiers de l'EA, alimente SQLite et produit des `Notice` que la couche Telegram
regroupe et envoie. Aucune connexion aux comptes MT5.
"""
from __future__ import annotations

import asyncio
import json
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
SILENT_TYPES = {"DEMARRAGE_EA", "ARRET_EA", "ETAT"}            # CASHFLOW (dépôt/retrait) : avec son
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


# OnDeinit de l'EA (constantes REASON_* de MQL5)
DEINIT_REASONS = {
    0: "l'EA s'est arrêté de lui-même",
    1: "EA retiré du graphique (à la main)",
    2: "EA recompilé : il redémarre avec la nouvelle version",
    3: "symbole ou période du graphique changé : l'EA redémarre",
    4: "graphique fermé",
    5: "paramètres modifiés dans MT5 : l'EA redémarre",
    6: "changement de compte ou reconnexion au serveur : l'EA redémarre",
    7: "modèle appliqué au graphique : l'EA redémarre",
    8: "échec de démarrage de l'EA (voir le journal Experts de MT5)",
    9: "terminal MT5 fermé",
}


def explain_stop(reason: int, expected: bool) -> str:
    """Texte lisible pour l'événement ARRET_EA."""
    if reason == 9 and expected:
        return "🔌 Terminal fermé à la demande du bot (arrêt, redémarrage ou mise à jour de l'EA). Normal."
    why = DEINIT_REASONS.get(reason, f"code {reason}")
    if reason == 9:
        why += " sans demande du bot (fermé à la main, redémarrage du VPS ou plantage) ; le watchdog le relance s'il doit tourner"
    return f"🔌 EA arrêté : {why}."


def event_to_notice(ev: Evenement, label: str, common: Path) -> Notice | None:
    """Transforme un événement de l'EA en notification (None = stocké mais non notifié)."""
    if ev.type.startswith("LABO_"):                   # labo des filtres : mesures, jamais notifiées
        return None
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
        self._pending: list[Notice] = []                # notifications calculées sur les statuts (capital)

    # ---- statuts → historique
    def poll_status(self, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        for inst in self.db.list():
            try:
                self._poll_one_status(inst, now)
            except Exception:                            # un compte en erreur ne bloque jamais les autres
                log.exception("statut de %s : erreur de traitement", inst.id)

    def _poll_one_status(self, inst, now: float) -> None:
        st = self.status.read(inst.id)
        if st is None:
            return
        if st.etat == "ARRET_500":
            self._last_arret_balance[inst.id] = st.solde
            if not self.db.meta_get(f"arret500_since_{inst.id}"):
                self.db.meta_set(f"arret500_since_{inst.id}", str(int(now)))
        elif self._last_state.get(inst.id) == "ARRET_500" or self.db.meta_get(f"arret500_since_{inst.id}"):
            self.db.meta_set(f"arret500_since_{inst.id}", "")
        self._last_state[inst.id] = st.etat
        if st.devise and st.devise != inst.currency:
            self.db.set_currency(inst.id, st.devise)
        if st.age_s(now) < self.cfg.timing.heartbeat_stale_s:
            self._check_capital(inst, st)
        if st.version and st.version != inst.ea_version:
            self.db.set_version(inst.id, st.version, "heartbeat")
        if now - self._last_sample.get(inst.id, 0) >= 60 and st.age_s(now) < self.cfg.timing.heartbeat_stale_s * 4:
            self._last_sample[inst.id] = now
            self.db.add_balance(inst.id, st.solde, st.equity, st.flottant_ea, st.cycle, st.etat, int(now))

    def _check_capital(self, inst, st) -> None:
        """Solde sous le seuil de reprise : une alerte sonore à l'entrée, une à la sortie (état mémorisé en base)."""
        from .health import low_capital
        key = f"capital_low_{inst.id}"
        low, was = low_capital(st), self.db.meta_get(key) == "1"
        if low == was:
            return
        self.db.meta_set(key, "1" if low else "")
        c = st.devise
        if low:
            text = (f"⚠️ CAPITAL INSUFFISANT\n💰 Solde {st.solde:.2f} {c} : sous le minimum de {st.seuil_reprise:.0f} {c}.\n"
                    "Le robot ne peut pas trader normalement sur ce compte : dépose des fonds.")
        else:
            text = f"✅ CAPITAL SUFFISANT\n💰 Solde {st.solde:.2f} {c} (minimum {st.seuil_reprise:.0f} {c})."
        if st.etat == "ARRET_500" and low:                 # l'EA envoie déjà CAPITAL_INSUFFISANT dans ce cas
            return
        self._pending.append(Notice(inst.id, inst.label, "CAPITAL", text, 2 if low else 1, None, int(time.time())))

    # ---- événements → historique + notifications
    async def poll_events(self) -> int:
        n = 0
        while self._pending:
            await self.emit(self._pending.pop(0))
        insts = self.db.list()

        def drain_all() -> list[tuple]:
            # Bot 3.10.0 : tous les comptes dans UN seul fil (avant : un fil par compte, toutes les 3 s) ; hors de la boucle :
            # un fichier verrouillé par l'EA (nouvelles tentatives espacées) ne fige pas le bot
            out = []
            for inst in insts:
                try:
                    out.append((inst, self.events.drain(inst.id)))
                except Exception:
                    log.exception("événements de %s : lecture impossible", inst.id)
            return out
        for inst, evs in await asyncio.to_thread(drain_all):
            for ev in evs:
                n += 1
                try:
                    await self._handle_event(inst, ev)
                except Exception:                        # un événement mal formé n'empêche pas de traiter les suivants
                    log.exception("événement %s de %s : erreur de traitement (%r)", ev.type, inst.id, (ev.texte or "")[:200])
        return n

    async def _handle_event(self, inst, ev: Evenement) -> None:
        if ev.type.startswith("LABO_"):                # labo des filtres : table dédiée, ni journal ni notification
            self._labo(inst.id, ev)
            return
        self.db.add_event(inst.id, ev.ts or int(time.time()), ev.type, ev.prio, ev.texte)
        self._track(inst.id, ev)
        notice = event_to_notice(ev, inst.label, self.cfg.paths.common())
        if notice is not None and ev.type in self.QUIET_WHEN_BOT and self._bot_initiated(inst.id, ev):
            # Bot 4.8.5 : arrêt / démarrage / reprise que le bot vient de demander (redémarrage, déploiement, création…) :
            # gardés dans l'historique, mais pas de message en plus de celui de progression.
            if ev.type != "ARRET_EA" or int((ev.data or {}).get("raison", -1) if isinstance(ev.data, dict) else -1) == 9:
                return
        if notice is not None and ev.type == "ARRET_EA":
            reason = ev.data.get("raison", -1) if isinstance(ev.data, dict) else -1
            try:
                closing = int(self.db.meta_get(f"closing_{inst.id}") or 0)
            except ValueError:
                closing = 0
            when = ev.ts or int(time.time())
            notice.text = explain_stop(int(reason), abs(when - closing) < 180)
        if notice is not None:
            await self.emit(notice)

    QUIET_WHEN_BOT = ("ARRET_EA", "DEMARRAGE_EA", "REPRISE_IMMEDIATE")
    QUIET_WINDOW_S = 300

    def _bot_initiated(self, iid: str, ev: Evenement) -> bool:
        when = ev.ts or int(time.time())
        for k in (f"closing_{iid}", f"bot_start_{iid}"):
            try:
                t = int(self.db.meta_get(k) or 0)
            except ValueError:
                t = 0
            if t and -30 <= when - t <= self.QUIET_WINDOW_S:
                return True
        return False

    def _labo(self, iid: str, ev: Evenement) -> None:
        d = ev.data if isinstance(ev.data, dict) else {}
        ts = ev.ts or int(time.time())
        if ev.type == "LABO_ETAT":                     # dernier état seulement
            self.db.meta_set(f"labo_etat_{iid}", json.dumps({"ts": ts, **d}, ensure_ascii=False))
        elif ev.type in ("LABO_SERIE", "LABO_REEL"):
            kind = ev.type[5:]
            self.db.add_labo(iid, ts, kind, str(d.get("sc", "")), json.dumps(d, ensure_ascii=False))

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
