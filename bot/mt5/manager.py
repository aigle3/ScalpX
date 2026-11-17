"""Gestion des terminaux : lancer, arrêter proprement, redémarrer. Chaque action met à jour l'état souhaité."""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path

from core.commands import CommandQueue
from core.config import Config
from .ini import build_run_ini, write_run_ini
from core.status import StatusReader
from storage.db import ARRETE, DEMARRE, Database, Instance
from .procs import ProcessAPI

log = logging.getLogger(__name__)


class InstanceManager:
    def __init__(self, cfg: Config, db: Database, procs: ProcessAPI, status: StatusReader, cmdq: CommandQueue) -> None:
        self.cfg, self.db, self.procs, self.status, self.cmdq = cfg, db, procs, status, cmdq

    # ---- chemins
    def folder(self, inst: Instance) -> Path:
        return Path(inst.folder)

    def exe(self, inst: Instance) -> Path:
        return self.folder(inst) / self.cfg.mt5.terminal_exe

    def folder_ok(self, inst: Instance) -> bool:
        return self.exe(inst).is_file()

    def is_running(self, inst: Instance) -> bool:
        return self.procs.is_running(self.exe(inst))

    # ---- lancement
    def ensure_profile(self, inst: Instance) -> None:
        """Profil de graphiques VIDE dans le terminal de l'instance (jamais dans BASE) : seul le graphique ouvert par
        [StartUp] (XAUUSD + EA) apparaît, au lieu des graphiques du profil Default. Appelé terminal arrêté."""
        name = self.cfg.mt5.profile
        if not name:
            return
        try:
            d = Path(inst.folder) / "Profiles" / "Charts" / name
            d.mkdir(parents=True, exist_ok=True)
            for f in d.glob("*.chr"):                        # un graphique sauvegardé ferait un doublon avec [StartUp]
                f.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("profil %s : %s", name, exc)

    def launch(self, inst: Instance, ini: Path | None = None) -> int:
        """Lance le terminal. Création : .ini avec mot de passe ; sinon .ini de relance SANS secret (rouvre le graphique de l'EA).
        Efface l'ancien statut pour ne pas lire un vieux heartbeat."""
        self.status.remove(inst.id)
        self.ensure_profile(inst)
        if ini is None:
            ini = write_run_ini(Path(inst.folder), build_run_ini(inst.login, inst.server, self.cfg.mt5.ea_name, self.cfg.mt5.set_name,
                                                                 self.cfg.mt5.symbol, self.cfg.mt5.period, self.cfg.mt5.profile,
                                                                 self.cfg.mt5.max_bars))
        return self.procs.launch(self.exe(inst), ini)

    def start(self, inst: Instance, set_desired: bool = True) -> bool:
        """Lance une instance ; état souhaité → DEMARRE."""
        if not self.folder_ok(inst):
            return False
        if set_desired:
            self.db.set_desired(inst.id, DEMARRE)
        if self.is_running(inst):
            return True
        self.launch(inst)
        return True

    async def wait_exit(self, inst: Instance, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not self.is_running(inst):
                return True
            await asyncio.sleep(0.5)
        return not self.is_running(inst)

    async def wait_close500(self, inst: Instance) -> bool:
        """Attend la fin d'une clôture à 500 (délai max configuré). True si terminée/absente."""
        end = time.monotonic() + self.cfg.timing.close500_wait_max_s
        while time.monotonic() < end:
            st = self.status.read(inst.id)
            if st is None or not st.phase_cloture500:
                return True
            await asyncio.sleep(1.0)
        return False

    async def stop(self, inst: Instance, force: bool = False, set_desired: str | None = ARRETE) -> str:
        """Arrêt propre : (attente fin clôture 500) → FERMER_TERMINAL → attente 30 s → kill.
        Renvoie 'propre', 'kill', 'deja_arrete' ou 'attente_cloture' (clôture 500 non terminée, rien d'arrêté)."""
        if set_desired:
            self.db.set_desired(inst.id, set_desired)         # d'abord : le watchdog ne doit pas relancer
        if not self.is_running(inst):
            return "deja_arrete"
        if not force and not await self.wait_close500(inst):
            return "attente_cloture"
        self.db.meta_set(f"closing_{inst.id}", str(int(time.time())))   # fermeture voulue : « 🔌 arrêté » expliqué ainsi
        if not force:
            self.cmdq.send(inst.id, "FERMER_TERMINAL")
            if await self.wait_exit(inst, self.cfg.timing.stop_wait_s):
                return "propre"
        n = await asyncio.to_thread(self.procs.kill, self.exe(inst))   # attente de fin (jusqu'à 10 s) hors de la boucle du bot
        log.warning("terminal %s arrêté de force (%d processus)", inst.id, n or 0)
        await self.wait_exit(inst, 10)
        return "kill"

    async def restart(self, inst: Instance, force: bool = False) -> str:
        r = await self.stop(inst, force=force, set_desired=DEMARRE)
        if r == "attente_cloture":
            return r
        self.launch(inst)
        return "relance"

    # ---- sauvegarde / archivage
    def archive_folder(self, inst: Instance, suffix: str = "") -> Path | None:
        """Déplace le dossier de l'instance vers archives\\<id>_<horodatage>[_suffixe] (jamais de suppression brute)."""
        src = self.folder(inst)
        if not src.exists():
            return None
        self.cfg.paths.archives.mkdir(parents=True, exist_ok=True)
        name = f"{inst.id}_{time.strftime('%Y%m%d_%H%M%S')}" + (f"_{suffix}" if suffix else "")
        dst = self.cfg.paths.archives / name
        shutil.move(str(src), str(dst))
        return dst
