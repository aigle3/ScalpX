"""Vue agrégée des comptes (instantané) : alimente tableau de bord, filtres, rapport."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from storage.db import DEMARRE, Database, Instance
from .config import Config
from .health import Health, diagnose
from .models import Statut
from .status import StatusReader


@dataclass
class AccountView:
    inst: Instance
    st: Statut | None
    health: Health
    running: bool
    outdated: bool

    @property
    def etat(self) -> str:
        return self.st.etat if self.st else "—"

    @property
    def to_withdraw(self) -> float:
        return self.st.to_retire() if self.st else 0.0


def snapshot(cfg: Config, db: Database, status: StatusReader, is_running, folder_ok, base_version: str,
             now: float | None = None) -> list[AccountView]:
    """Construit la vue de tous les comptes non archivés. `is_running`/`folder_ok` : callables(Instance)."""
    now = now if now is not None else time.time()
    out: list[AccountView] = []
    for inst in db.list():
        st = status.read(inst.id)
        running = bool(is_running(inst))
        h = diagnose(inst, st, running, cfg, now, folder_ok(inst))
        if not running and db.meta_get("park:" + inst.id) == "1":          # Bot 4.15.0 : garé à 500, pas une anomalie
            h = Health("🅿️", "PARK",
                       ["🅿️ 500 atteint : terminal arrêté (CPU libéré), relancé tout seul après le retrait"])
        ver = (st.version if st and st.version else inst.ea_version)
        outdated = bool(base_version and ver and ver != base_version)
        out.append(AccountView(inst, st, h, running, outdated))
    return out


FILTERS = ("all", "actifs", "retirer", "pause", "alertes", "obsolete")
FILTER_LABELS = {"all": "Tous", "actifs": "Actifs", "retirer": "À retirer", "pause": "En pause",
                 "alertes": "Alertes", "obsolete": "Version obsolète"}
SORTS = ("nom", "solde", "progression", "etat")
SORT_LABELS = {"nom": "Nom", "solde": "Solde", "progression": "Progression", "etat": "État"}


def apply_filter(views: list[AccountView], flt: str) -> list[AccountView]:
    if flt == "actifs":
        return [v for v in views if v.inst.desired_state == DEMARRE and v.etat == "ACTIF"]
    if flt == "retirer":
        return [v for v in views if v.etat == "ARRET_500"]
    if flt == "pause":
        return [v for v in views if v.etat == "PAUSE"]
    if flt == "alertes":
        return [v for v in views if v.health.alert]
    if flt == "obsolete":
        return [v for v in views if v.outdated]
    return list(views)


def apply_sort(views: list[AccountView], key: str) -> list[AccountView]:
    if key == "solde":
        return sorted(views, key=lambda v: -(v.st.solde if v.st else 0))
    if key == "progression":
        return sorted(views, key=lambda v: -(v.st.progress500() if v.st else 0))
    if key == "etat":
        return sorted(views, key=lambda v: (v.health.icon, v.inst.label))
    return sorted(views, key=lambda v: (v.inst.label.lower(), v.inst.id))


@dataclass
class Totals:
    count: int = 0
    by_icon: dict[str, int] = field(default_factory=dict)
    balance: float = 0.0
    equity: float = 0.0
    pl_latent: float = 0.0
    pl_jour: float = 0.0
    to_withdraw: float = 0.0
    currency_mixed: bool = False


def totals(views: list[AccountView]) -> Totals:
    t = Totals(count=len(views))
    curs: set[str] = set()
    for v in views:
        t.by_icon[v.health.icon] = t.by_icon.get(v.health.icon, 0) + 1
        if v.st:
            t.balance += v.st.solde
            t.equity += v.st.equity
            t.pl_latent += v.st.pl_latent
            t.pl_jour += v.st.day_realized()            # « jour » = réalisé ; le latent a sa propre ligne
            t.to_withdraw += v.to_withdraw
            if v.st.devise:
                curs.add(v.st.devise)
    t.currency_mixed = len(curs) > 1
    return t
