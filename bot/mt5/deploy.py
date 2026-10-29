"""Déploiement d'une nouvelle version de l'EA : instance par instance (jamais deux à la fois).

Seul le .ex5 est copié depuis BASE : jamais le profil, le graphique, les paramètres ni le compte mémorisé.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from core.commands import CommandQueue
from core.config import Config
from core.status import StatusReader
from storage.db import DEMARRE, MAINTENANCE, Database, Instance
from . import base as basemod
from .manager import InstanceManager

log = logging.getLogger(__name__)

# états d'affichage de la progression
OK, RUNNING, WAITING, FAILED, PENDING = "✅", "⏳", "⏸", "❌", "·"


@dataclass
class DeployPlan:
    base_version: str
    targets: list[str]                          # identifiants à mettre à jour
    up_to_date: list[str] = field(default_factory=list)
    inputs_changed: bool = False                # l'EA a de NOUVEAUX inputs (.set de BASE) par rapport à une instance
    inputs_diff: str = ""                       # noms des nouveaux inputs

    @property
    def inputs_note(self) -> str:
        """Texte pour Telegram (vide si rien à signaler : des inputs supprimés ne changent rien pour toi)."""
        if not self.inputs_changed:
            return ""
        return (f"ℹ️ Nouveaux réglages d'entrée dans cette version de l'EA : {self.inputs_diff}. "
                "Ils prennent leur valeur par défaut ; tes réglages actuels ne changent pas.")


@dataclass
class DeployOutcome:
    iid: str
    status: str                                 # ok | waiting | failed
    message: str = ""


ProgressCb = Callable[[dict[str, tuple[str, str]]], Awaitable[None]]    # iid → (icône, texte)


def _inst_ex5(cfg: Config, inst: Instance) -> Path:
    return Path(inst.folder) / "MQL5" / "Experts" / cfg.mt5.ea_file


def base_ex5(cfg: Config) -> Path:
    return cfg.paths.base / "MQL5" / "Experts" / cfg.mt5.ea_file


def plan_deploy(cfg: Config, db: Database, selection: list[str] | None = None) -> DeployPlan:
    """Synthèse : un compte est à mettre à jour si son .ex5 diffère de celui de BASE (même sans changement de version)."""
    bv = basemod.read_version(cfg.paths.base)
    bh = basemod.cached_hash(base_ex5(cfg))
    insts = [i for i in db.list() if selection is None or i.id in selection]
    targets = [i.id for i in insts if bh and basemod.cached_hash(_inst_ex5(cfg, i)) != bh]
    ok = [i.id for i in insts if i.id not in targets]
    plan = DeployPlan(bv, targets, ok)
    base_set = cfg.paths.base / "MQL5" / "Presets" / cfg.mt5.set_name
    bk = basemod.set_keys(base_set)
    for i in insts:
        ik = basemod.set_keys(Path(i.folder) / "MQL5" / "Presets" / cfg.mt5.set_name)
        added = [k for k in bk if k not in ik] if ik and bk else []
        if added:                                   # inputs supprimés ou réordonnés : rien à signaler
            plan.inputs_changed = True
            plan.inputs_diff = ", ".join(added[:6])
            break
    return plan


class DeployManager:
    def __init__(self, cfg: Config, db: Database, mgr: InstanceManager, status: StatusReader, cmdq: CommandQueue) -> None:
        self.cfg, self.db, self.mgr, self.status, self.cmdq = cfg, db, mgr, status, cmdq
        self._cancel = False
        self.running = False
        self.last_version = ""

    def cancel(self) -> None:
        self._cancel = True

    # ---- chemins
    def _ex5(self, inst: Instance) -> Path:
        return Path(inst.folder) / "MQL5" / "Experts" / self.cfg.mt5.ea_file

    def _base_ex5(self) -> Path:
        return self.cfg.paths.base / "MQL5" / "Experts" / self.cfg.mt5.ea_file

    def _backup_dir(self, inst: Instance) -> Path:
        return Path(inst.folder) / "ex5_backup"           # hors de MQL5 : n'apparaît pas dans le Navigateur

    def backup_ex5(self, inst: Instance) -> Path | None:
        """Sauvegarde l'ancien .ex5 (sauf s'il est déjà identique à celui de BASE : reprise d'un déploiement)."""
        src = self._ex5(inst)
        if not src.is_file():
            return None
        if self._base_ex5().is_file() and basemod.file_hash(src) == basemod.file_hash(self._base_ex5()):
            return self.latest_backup(inst)
        d = self._backup_dir(inst)
        d.mkdir(parents=True, exist_ok=True)
        dst = d / f"{self.cfg.mt5.ea_file}.{time.strftime('%Y%m%d_%H%M%S')}.bak"
        shutil.copy2(src, dst)
        return dst

    def latest_backup(self, inst: Instance) -> Path | None:
        d = self._backup_dir(inst)
        if not d.is_dir():
            return None
        files = sorted(d.glob(f"{self.cfg.mt5.ea_file}.*.bak"))
        return files[-1] if files else None

    def sync_set(self, inst: Instance) -> None:
        """Recopie le .set de BASE dans l'instance (modèle des inputs ; ne change pas les réglages de l'EA qui tourne)."""
        src = self.cfg.paths.base / "MQL5" / "Presets" / self.cfg.mt5.set_name
        dst = Path(inst.folder) / "MQL5" / "Presets" / self.cfg.mt5.set_name
        try:
            if src.is_file() and dst.parent.is_dir():
                shutil.copy2(src, dst)
        except OSError as exc:
            log.warning(".set non recopié pour %s : %s", inst.id, exc)

    def install_ex5(self, inst: Instance, source: Path) -> None:
        """Copie UNIQUEMENT le .ex5 (fichier temporaire puis remplacement)."""
        dst = self._ex5(inst)
        tmp = dst.with_name(dst.name + ".new")
        shutil.copy2(source, tmp)
        os.replace(tmp, dst)

    # ---- attentes
    async def _wait_flat(self, inst: Instance) -> bool:
        """Attend 0 position de l'EA et aucune clôture 500 en cours (délai configurable)."""
        end = time.monotonic() + self.cfg.timing.maintenance_wait_s
        while time.monotonic() < end:
            st = self.status.read(inst.id)
            if st is not None and st.positions == 0 and not st.phase_cloture500:
                return True
            await asyncio.sleep(2.0)
        return False

    async def _wait_new_version(self, inst: Instance, version: str, pre_etat: str | None, pre_cycle: int | None, since: float) -> tuple[bool, str]:
        """Le nouvel .ex5 tourne si un heartbeat postérieur à la relance arrive, connecté, avec état et cycle inchangés.
        La version affichée n'est pas exigée : une recompilation sans changement de version doit aussi passer."""
        end = time.monotonic() + self.cfg.timing.verify_deploy_s
        last = "aucun heartbeat"
        while time.monotonic() < end:
            st = self.status.read(inst.id)
            if st is not None and st.ts > getattr(self, "_old_ts", 0) and st.ts >= since - 2:
                last = f"version {st.version or '?'}"
                if st.connecte:
                    if pre_etat is None or (st.etat == pre_etat and st.cycle == pre_cycle):
                        self.last_version = st.version or version
                        return True, ""
                    return False, f"état/cycle modifiés ({pre_etat}/{pre_cycle} → {st.etat}/{st.cycle})"
            await asyncio.sleep(1.0)
        return False, f"heartbeat du nouvel EA non reçu ({last})"

    # ---- une instance
    async def deploy_one(self, inst: Instance, version: str) -> DeployOutcome:
        pre = self.status.read(inst.id)
        pre_etat = pre.etat if pre else None            # None : état inconnu avant le déploiement → pas de comparaison
        pre_cycle = pre.cycle if pre else None
        was_running = self.mgr.is_running(inst)
        self.db.set_desired(inst.id, MAINTENANCE)                         # 1) le watchdog ignore
        if was_running:
            r = await self.cmdq.send_and_wait(inst.id, "MAINTENANCE_ON", timeout=self.cfg.timing.ack_timeout_s)   # 2)
            if not r.ok:
                self.db.set_desired(inst.id, DEMARRE)
                return DeployOutcome(inst.id, "failed", "MAINTENANCE_ON refusé : " + r.message)
            if not await self._wait_flat(inst):                           # 3)
                await self.cmdq.send_and_wait(inst.id, "MAINTENANCE_OFF", timeout=self.cfg.timing.ack_timeout_s)
                self.db.set_desired(inst.id, DEMARRE)
                return DeployOutcome(inst.id, "waiting", "positions ouvertes ou clôture 500 en cours")
        backup = self.backup_ex5(inst)                                    # 4)
        stop = await self.mgr.stop(inst, force=False, set_desired=None)  # 5) FERMER_TERMINAL puis kill après 30 s
        if stop == "attente_cloture":
            await self.cmdq.send_and_wait(inst.id, "MAINTENANCE_OFF", timeout=self.cfg.timing.ack_timeout_s)
            self.db.set_desired(inst.id, DEMARRE)
            return DeployOutcome(inst.id, "waiting", "clôture 500 en cours")
        try:
            self.install_ex5(inst, self._base_ex5())                      # 6) uniquement le .ex5
            t0 = time.time()
            self.last_version = ""
            old = self.status.read(inst.id)
            self._old_ts = old.ts if old is not None else 0          # dernier heartbeat de l'ancien EA (ne doit pas compter)
            self.mgr.launch(inst)                                         # 7) sans /config (maintenance non persistée)
            ok, why = await self._wait_new_version(inst, version, pre_etat, pre_cycle, t0)   # 8)
        except Exception as exc:
            ok, why = False, f"erreur : {exc}"
        if ok:
            self.sync_set(inst)                                           # l'avertissement « inputs » ne revient plus
            self.db.set_desired(inst.id, DEMARRE)
            self.db.set_version(inst.id, self.last_version or version, "déploiement")
            return DeployOutcome(inst.id, "ok")
        # 9) échec : restauration de l'ancien .ex5, relance, alerte
        await self.mgr.stop(inst, force=True, set_desired=None)
        if backup and backup.is_file():
            try:
                self.install_ex5(inst, backup)
            except Exception as exc:
                log.error("restauration impossible pour %s : %s", inst.id, exc)
        self.mgr.launch(inst)
        self.db.set_desired(inst.id, DEMARRE)
        return DeployOutcome(inst.id, "failed", why + " — ancien .ex5 restauré")

    # ---- série
    async def run(self, targets: list[str], progress: ProgressCb | None = None, dep_id: int | None = None) -> list[DeployOutcome]:
        """Déploie séquentiellement ; au premier échec les suivants ne sont PAS déployés."""
        self.running, self._cancel = True, False
        version = basemod.read_version(self.cfg.paths.base)
        insts = [i for i in (self.db.get(t) for t in targets) if i]
        view: dict[str, tuple[str, str]] = {i.id: (PENDING, "en attente") for i in insts}
        outcomes: list[DeployOutcome] = []
        if dep_id is None:
            dep_id = self.db.start_deployment(",".join(sorted({i.ea_version or "?" for i in insts})), version, [i.id for i in insts])
        pending = [i.id for i in insts]
        try:
            for inst in insts:
                if self._cancel:
                    view[inst.id] = (WAITING, "annulé")
                    continue
                view[inst.id] = (RUNNING, "mise à jour…")
                if progress:
                    await progress(view)
                out = await self.deploy_one(inst, version)
                outcomes.append(out)
                if out.status == "ok":
                    view[inst.id] = (OK, f"v{self.db.get(inst.id).ea_version or version}")
                    pending.remove(inst.id)
                elif out.status == "waiting":
                    view[inst.id] = (WAITING, out.message)
                else:
                    view[inst.id] = (FAILED, out.message)
                    self.db.update_deployment(dep_id, pending=",".join(pending))
                    if progress:
                        await progress(view)
                    for rest in insts[insts.index(inst) + 1:]:
                        view[rest.id] = (WAITING, "non déployé (échec précédent)")
                    break
                self.db.update_deployment(dep_id, pending=",".join(pending))
                if progress:
                    await progress(view)
            if progress:
                await progress(view)
        finally:
            failed = any(o.status == "failed" for o in outcomes)
            waiting = [o.iid for o in outcomes if o.status == "waiting"]
            status = "ECHEC" if failed else ("PARTIEL" if waiting or self._cancel else "OK")
            report = "; ".join(f"{o.iid}={o.status}{(':' + o.message) if o.message else ''}" for o in outcomes)
            self.db.update_deployment(dep_id, ended_ts=int(time.time()), status=status, report=report,
                                      pending=",".join(waiting))
            self.running = False
        return outcomes

    # ---- reprise / annulation après interruption (redémarrage du VPS en plein déploiement)
    def interrupted(self):
        return self.db.open_deployment()

    async def resume(self, progress: ProgressCb | None = None) -> list[DeployOutcome]:
        """Reprend : refait les étapes (idempotentes) pour les instances restantes."""
        dep = self.db.open_deployment()
        if not dep:
            return []
        targets = [t for t in (dep["pending"] or "").split(",") if t]
        return await self.run(targets, progress, dep_id=int(dep["id"]))

    def cancel_interrupted(self) -> None:
        """Annule proprement : instances en MAINTENANCE → DEMARRE, déploiement marqué ANNULE."""
        dep = self.db.open_deployment()
        for inst in self.db.list(states=(MAINTENANCE,)):
            self.db.set_desired(inst.id, DEMARRE)
        if dep:
            self.db.update_deployment(int(dep["id"]), status="ANNULE", ended_ts=int(time.time()))


# ---- détection d'une nouvelle compilation dans BASE
@dataclass
class NewBuild:
    version: str
    compiled_at: float
    plan: DeployPlan


def detect_new_build(cfg: Config, db: Database, settle_s: float = 15.0, now: float | None = None) -> NewBuild | None:
    """Renvoie la nouvelle compilation à proposer, une seule fois par .ex5 :
    le .ex5 de BASE est stable depuis settle_s, n'a pas encore été proposé et au moins un compte a un autre .ex5."""
    p = base_ex5(cfg)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return None
    if (now or time.time()) - mtime < settle_s:                       # MetaEditor est peut-être encore en train d'écrire
        return None
    h = basemod.cached_hash(p)
    if not h or db.meta_get("build_notified") == h:
        return None
    plan = plan_deploy(cfg, db)
    db.meta_set("build_notified", h)
    if not plan.targets:                                                # déjà partout (ex. compilé puis déployé à la main)
        return None
    return NewBuild(plan.base_version, mtime, plan)
