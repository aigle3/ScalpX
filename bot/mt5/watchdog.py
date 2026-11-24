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


def journal_tail(folder: str, lines: int = 6, width: int = 160) -> str:
    """Bot 4.14.4 : dernières lignes du jour du journal de l'EA et du terminal (UTF-16 ou UTF-8), pour joindre le diagnostic
    à l'alerte sans commande à taper. Vide si introuvable."""
    import html
    from pathlib import Path
    out: list[str] = []
    for title, subs in (("EA", ("MQL5/logs", "MQL5/Logs")), ("Terminal", ("logs", "Logs"))):
        files: set = set()
        for s in subs:
            d = Path(folder) / s
            if d.is_dir():
                files |= {f for f in d.glob("*.log") if f.name[:8].isdigit()}
        if not files:
            continue
        f = max(files, key=lambda x: x.stat().st_mtime)
        try:
            raw = f.read_bytes()[-200_000:]
        except OSError:
            continue
        if raw.count(0) > len(raw) // 4:                 # journal MT5 en UTF-16 LE (BOM) : décodé tel quel (accents gardés)
            raw = raw[len(raw) % 2:]
            txt = raw.decode("utf-16-le", "replace").lstrip("\ufeff")
        else:
            txt = raw.decode("utf-8", "replace")
        tail = [ln.strip()[:width] for ln in txt.splitlines() if ln.strip()][-lines:]
        if tail:
            out.append(f"<b>{title}</b>\n<pre>{html.escape(chr(10).join(tail))}</pre>")
    return "\n".join(out)


class Watchdog:
    def __init__(self, cfg: Config, db: Database, mgr: InstanceManager, status: StatusReader, alert: Alert) -> None:
        self.cfg, self.db, self.mgr, self.status, self.alert = cfg, db, mgr, status, alert
        self._restarts: dict[str, deque[float]] = defaultdict(deque)
        self._flagged: dict[str, str] = {}               # dernière anomalie notifiée (évite le spam)
        self._muet_since: dict[str, float] = {}
        self._muet_limit: set[str] = set()               # comptes dont la limite de relances a déjà été signalée (cas « muet »)
        self._np_grace: dict[str, float] = {}            # Bot 4.14.0 : relancé pour « sans prix » : pas de nouveau jugement avant
        self._np_flag: dict[str, str] = {}               # « RELANCE » (attend le retour des prix) ou « LIMITE » (signalée)
        self._live_since: dict[tuple, float] = {}        # Bot 4.14.1 : (symbole, serveur) → depuis quand ce flux reçoit des prix

    def _tick_age(self, st, now: float) -> float | None:
        """Secondes depuis le dernier prix reçu par l'EA (EA ≥ 8.1.1), à l'instant `now` ; None si inconnu.
        Bot 4.14.1 : un statut ancien (terminal planté, EA arrêté) ne compte PAS : sa valeur figée ferait croire à des prix."""
        if st is None:
            return None
        age = getattr(st, "tick_age_s", None)
        if not isinstance(age, (int, float)) or isinstance(age, bool) or age < 0:
            return None
        hb = st.age_s(now) if hasattr(st, "age_s") else 0.0
        if hb > self.cfg.timing.heartbeat_stale_s:
            return None
        return float(age) + max(0.0, hb)

    async def _no_price(self, inst, st, others_ok: bool, now: float, live_since: float | None = None) -> bool:
        """Bot 4.14.0 : EA vivant et connecté mais plus aucun prix (24/11 : OnTick plus jamais appelé après une relance,
        aucun trade possible, positions sans TP). Si d'AUTRES comptes reçoivent des prix (marché ouvert, flux OK), le
        terminal est relancé (même limite par heure que les autres relances), puis le retour des prix est confirmé.
        True = terminal relancé à ce passage."""
        limit = self.cfg.timing.sans_prix_restart_s
        age = self._tick_age(st, now)
        if limit <= 0 or age is None or now < self._np_grace.get(inst.id, 0):
            return False
        if age < 60:
            if self._np_flag.pop(inst.id, None):
                await self.alert(inst.id, "RETABLI", f"🟢 {inst.label} : les prix arrivent de nouveau, problème résolu "
                                                     f"(dernier prix il y a {int(age)} s).", 1)
            return False
        if age <= limit or not others_ok or not getattr(st, "connecte", False):
            return False
        # Bot 4.14.1 : réouverture (lundi, fin de la coupure quotidienne) : les comptes ne reçoivent pas leur 1er prix à la même
        # seconde. On ne juge qu'une fois le marché vivant depuis `limit` (sinon relances inutiles des retardataires).
        if live_since is None or now - live_since < limit:
            return False
        if self._np_flag.get(inst.id) == "LIMITE":       # Bot 4.14.1 : limite atteinte : plus de relance tant que les prix ne
            return False                                 # sont pas revenus (pas de nouvelle salve l'heure suivante)
        if self.can_restart(inst.id, now):
            self._restarts[inst.id].append(now)
            n = len(self._restarts[inst.id])
            await self.mgr.restart(inst, force=True)
            self._np_grace[inst.id] = now + limit         # laisse au nouvel EA le temps de recevoir ses premiers prix
            self._np_flag[inst.id] = "RELANCE"
            await self.alert(inst.id, "SANS_PRIX", f"📡 {inst.label} : aucun prix reçu depuis {int(age // 60)} min alors que les "
                                                   f"autres comptes en reçoivent → terminal relancé automatiquement "
                                                   f"({n}/{self.cfg.timing.restarts_per_hour} cette heure). Je confirme ici dès que "
                                                   f"les prix reviennent." + self._diag(inst), 2)
            return True
        if self._np_flag.get(inst.id) != "LIMITE":
            self._np_flag[inst.id] = "LIMITE"
            await self.alert(inst.id, "LIMITE", f"🔴 {inst.label} : toujours aucun prix après {self.cfg.timing.restarts_per_hour} "
                                                f"relances en 1 h : je n'insiste plus. Intervention requise." + self._diag(inst), 2)
        return False

    @staticmethod
    def _diag(inst) -> str:
        try:
            t = journal_tail(inst.folder)
        except Exception:
            return ""
        return ("\n\n🩺 <b>Journal avant relance</b> (à me transmettre tel quel)\n" + t) if t else ""

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
        insts = self.db.list(states=(DEMARRE,))
        sts = {i.id: self.status.read(i.id) for i in insts}
        ages = {k: self._tick_age(s, now) for k, s in sts.items()}              # flux de prix des comptes (référence)
        sym = {k: (getattr(s, "symbole", "") or "", getattr(s, "serveur", "") or "") for k, s in sts.items() if s is not None}
        live = {sym[k] for k, a in ages.items() if a is not None and a < 60 and k in sym}
        for g in live:
            self._live_since.setdefault(g, now)
        for g in [g for g in self._live_since if g not in live]:
            del self._live_since[g]                      # week-end / coupure : ce flux ne reçoit plus rien, personne n'est jugé
        for inst in insts:
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
            # Bot 4.14.1 : référence = comptes du MÊME symbole chez le MÊME serveur (mêmes horaires de cotation)
            others_ok = any(a is not None and a < 60 for k, a in ages.items()
                            if k != inst.id and k in sym and sym[k] == sym.get(inst.id))
            if await self._no_price(inst, st, others_ok, now, self._live_since.get(sym.get(inst.id))):
                continue
            if h.code == "DOUBLON" and self._flagged.get(inst.id) != "DOUBLON":
                self._flagged[inst.id] = "DOUBLON"
                await self.alert(inst.id, "DOUBLON", f"👯 EA en double sur {inst.label} : {', '.join(h.reasons)}.", 2)
            elif h.code in ("DECONNECTE", "ALGO_OFF", "DEVISE") and self._flagged.get(inst.id) != h.code:
                self._flagged[inst.id] = h.code
                await self.alert(inst.id, h.code, f"🟠 {inst.label} : {', '.join(h.reasons)}.", 1)
            elif h.code == "OK" and self._flagged.get(inst.id) in ("MUET", "DOUBLON", "DECONNECTE", "ALGO_OFF", "DEVISE"):
                prev = self._flagged.pop(inst.id)
                await self.alert(inst.id, "RETABLI", f"🟢 {inst.label} : de nouveau normal ({prev}).", 1)
