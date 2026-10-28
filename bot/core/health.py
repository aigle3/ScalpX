"""Diagnostic croisé processus × heartbeat × statut EA → icône d'état du tableau de bord."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from storage.db import ARCHIVE, ARRETE, EN_CREATION, MAINTENANCE, Instance
from .config import Config
from .models import Statut

G, O, R, GEAR, LOCK, OFF, NEW = "🟢", "🟠", "🔴", "⚙️", "🔒", "⚪", "🆕"


@dataclass
class Health:
    icon: str
    code: str                       # OK | CAPITAL | MUET | DECONNECTE | ALGO_OFF | DOUBLON | DEVISE | PROCESSUS | MAINT | CLOTURE500 | ARRETE | CREATION | ARCHIVE | DOSSIER
    reasons: list[str] = field(default_factory=list)

    @property
    def alert(self) -> bool:
        return self.code not in ("OK", "MAINT", "CLOTURE500", "ARRETE", "CREATION", "ARCHIVE")


def low_capital(st: Statut) -> bool:
    """Solde sous le seuil de reprise (125 par défaut) : le robot ne peut pas faire tourner son cycle."""
    return st.seuil_reprise > 0 and st.solde < st.seuil_reprise


def diagnose(inst: Instance, st: Statut | None, running: bool, cfg: Config, now: float | None = None,
             folder_ok: bool = True) -> Health:
    """🟢 processus + heartbeat récent ; 🟠 anomalie ; 🔴 pas de processus ; ⚙️ maintenance ; 🔒 clôture 500."""
    now = now if now is not None else time.time()
    if inst.desired_state == ARCHIVE:
        return Health(OFF, "ARCHIVE", ["archivée"])
    if inst.desired_state == EN_CREATION:
        return Health(NEW, "CREATION", ["création en cours"])
    if not folder_ok:
        return Health(R, "DOSSIER", ["dossier de l'instance introuvable"])
    if not running:
        if inst.desired_state == ARRETE:
            return Health(OFF, "ARRETE", ["arrêté volontairement"])
        if inst.desired_state == MAINTENANCE:
            return Health(GEAR, "MAINT", ["maintenance (terminal arrêté)"])
        return Health(R, "PROCESSUS", ["pas de processus terminal"])
    if inst.desired_state == MAINTENANCE or (st is not None and st.maintenance):
        return Health(GEAR, "MAINT", ["maintenance en cours"])
    if st is None or st.age_s(now) > cfg.timing.heartbeat_stale_s:
        age = "aucun heartbeat" if st is None else f"heartbeat vieux de {int(st.age_s(now))} s"
        return Health(O, "MUET", [age])
    if st.phase_cloture500:
        return Health(LOCK, "CLOTURE500", ["clôture à 500 en cours"])
    reasons: list[str] = []
    code = "OK"
    if not st.connecte:
        reasons.append("terminal déconnecté du broker"); code = "DECONNECTE"
    if not (st.algo_terminal and st.algo_ea):
        reasons.append("algo trading désactivé"); code = code if code != "OK" else "ALGO_OFF"
    if st.nb_graphiques_ea > 1:
        reasons.append(f"EA en double ({st.nb_graphiques_ea} graphiques)"); code = code if code != "OK" else "DOUBLON"
    if st.nb_graphiques_ea == 0:
        reasons.append("EA absent du graphique"); code = code if code != "OK" else "DOUBLON"
    if low_capital(st):
        reasons.append(f"capital insuffisant : {st.solde:.2f} {st.devise} (minimum {st.seuil_reprise:.0f} {st.devise})")
        code = code if code != "OK" else "CAPITAL"
    if st.devise and cfg.alerts.expected_currency and st.devise != cfg.alerts.expected_currency:
        reasons.append(f"devise {st.devise} ≠ {cfg.alerts.expected_currency}"); code = code if code != "OK" else "DEVISE"
    if reasons:
        return Health(O, code, reasons)
    return Health(G, "OK", [])
