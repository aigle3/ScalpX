"""Watchdog : relance les instances DEMARRE dont le processus a disparu (3 tentatives par heure max)."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from typing import Awaitable, Callable

from core.config import Config
from core.health import diagnose
from core.status import StatusReader
from storage.db import DEMARRE, MAINTENANCE, Database
from .manager import InstanceManager

log = logging.getLogger(__name__)
Alert = Callable[[str, str, str, int], Awaitable[None]]      # (iid, kind, texte, priorité)


class Watchdog:
    def __init__(self, cfg: Config, db: Database, mgr: InstanceManager, status: StatusReader, alert: Alert) -> None:
        self.cfg, self.db, self.mgr, self.status, self.alert = cfg, db, mgr, status, alert
        self._restarts: dict[str, deque[float]] = defaultdict(deque)
        self._flagged: dict[str, str] = {}               # dernière anomalie notifiée (évite le spam)
        self._muet_since: dict[str, float] = {}
        self._muet_limit: set[str] = set()               # comptes dont la limite de relances a déjà été signalée (cas « muet »)

    async def _unfreeze(self, inst, muet_for: float, now: float) -> None:
        """Terminal vivant mais EA muet depuis `muet_restart_s` : arrêt forcé puis relance (même limite de relances par heure
        que le cas « processus disparu »). Les positions restent chez le broker ; l'EA reprend dès son retour."""
        limit = self.cfg.timing.muet_restart_s
        if limit <= 0 or muet_for <= limit:
            return
        if self.can_restart(inst.id, now):
            self._restarts[inst.id].append(now)
            n = len(self._restarts[inst.id])
            await self.mgr.restart(inst, force=True)
            self._muet_since.pop(inst.id, None)          # nouveau départ : l'EA a besoin de temps pour réécrire son statut
            self._flagged.pop(inst.id, None)
            await self.alert(inst.id, "RELANCE", f"🔄 {inst.label} : EA muet depuis {int(muet_for // 60)} min, terminal relancé "
                                                 f"({n}/{self.cfg.timing.restarts_per_hour} cette heure).", 2)
        elif inst.id not in self._muet_limit:
            self._muet_limit.add(inst.id)
            await self.alert(inst.id, "LIMITE", f"🔴 {inst.label} : EA muet, mais {self.cfg.timing.restarts_per_hour} relances en 1 h : "
                                                f"je n'insiste plus. Intervention requise.", 2)

    def can_restart(self, iid: str, now: float | None = None) -> bool:
        """Limite de `restarts_per_hour` relances par heure glissante."""
        now = now if now is not None else time.time()
        q = self._restarts[iid]
        while q and now - q[0] > 3600:
            q.popleft()
        return len(q) < self.cfg.timing.restarts_per_hour

    async def startup_launch(self) -> list[str]:
        """Au démarrage du bot : relance ÉCHELONNÉE des seules instances DEMARRE
        (et MAINTENANCE d'un déploiement interrompu : elles tradaient avant le redémarrage)."""
        launched: list[str] = []
        for inst in self.db.list(states=(DEMARRE, MAINTENANCE)):
            if not self.mgr.folder_ok(inst):
                await self.alert(inst.id, "DOSSIER", f"📁 Dossier introuvable pour {inst.label} ({inst.id}) : non recréé automatiquement.", 2)
                continue
            if self.mgr.is_running(inst):
                continue
            self.mgr.launch(inst, by_bot=False)                  # relance par le watchdog : notifiée (Bot 4.8.5)
            launched.append(inst.id)
            await asyncio.sleep(self.cfg.timing.launch_stagger_s)
        return launched

    async def tick(self) -> None:
        """Un passage : processus disparus, dossiers manquants, terminaux muets, anomalies."""
        now = time.time()
        for inst in self.db.list(states=(DEMARRE,)):
            if not self.mgr.folder_ok(inst):
                if self._flagged.get(inst.id) != "DOSSIER":
                    self._flagged[inst.id] = "DOSSIER"
                    await self.alert(inst.id, "DOSSIER", f"📁 Dossier introuvable pour {inst.label} : alerte, pas de recréation automatique.", 2)
                continue
            running = self.mgr.is_running(inst)
            if not running:
                if self.can_restart(inst.id, now):
                    self._restarts[inst.id].append(now)
                    n = len(self._restarts[inst.id])
                    self.mgr.launch(inst, by_bot=False)                  # relance par le watchdog : notifiée (Bot 4.8.5)
                    await self.alert(inst.id, "RELANCE", f"🔴 Terminal de {inst.label} absent : relancé ({n}/{self.cfg.timing.restarts_per_hour} cette heure).", 2)
                elif self._flagged.get(inst.id) != "LIMITE":
                    self._flagged[inst.id] = "LIMITE"
                    await self.alert(inst.id, "LIMITE", f"🔴 {inst.label} : {self.cfg.timing.restarts_per_hour} relances en 1 h, je n'insiste plus. Intervention requise.", 2)
                continue
            if self._flagged.get(inst.id) in ("LIMITE", "DOSSIER"):
                self._flagged.pop(inst.id, None)
            st = self.status.read(inst.id)
            h = diagnose(inst, st, True, self.cfg, now)
            if h.code == "MUET":
                self._muet_since.setdefault(inst.id, now)
                muet_for = now - self._muet_since[inst.id]
                if muet_for > 120 and self._flagged.get(inst.id) != "MUET":
                    self._flagged[inst.id] = "MUET"
                    await self.alert(inst.id, "MUET", f"🔴 Terminal muet : {inst.label} ({', '.join(h.reasons)}).", 2)
                await self._unfreeze(inst, muet_for, now)
                continue
            self._muet_since.pop(inst.id, None)
            self._muet_limit.discard(inst.id)
            if h.code == "DOUBLON" and self._flagged.get(inst.id) != "DOUBLON":
                self._flagged[inst.id] = "DOUBLON"
                await self.alert(inst.id, "DOUBLON", f"👯 EA en double sur {inst.label} : {', '.join(h.reasons)}.", 2)
            elif h.code in ("DECONNECTE", "ALGO_OFF", "DEVISE") and self._flagged.get(inst.id) != h.code:
                self._flagged[inst.id] = h.code
                await self.alert(inst.id, h.code, f"🟠 {inst.label} : {', '.join(h.reasons)}.", 1)
            elif h.code == "OK" and self._flagged.get(inst.id) in ("MUET", "DOUBLON", "DECONNECTE", "ALGO_OFF", "DEVISE"):
                prev = self._flagged.pop(inst.id)
                await self.alert(inst.id, "RETABLI", f"🟢 {inst.label} : de nouveau normal ({prev}).", 1)
