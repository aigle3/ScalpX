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

    def launch(self, inst: Instance, ini: Path | None = None, by_bot: bool = True) -> int:
        """Lance le terminal. Création : .ini avec mot de passe ; sinon .ini de relance SANS secret (rouvre le graphique de l'EA).
        Efface l'ancien statut pour ne pas lire un vieux heartbeat. by_bot : lancement voulu (démarrage, redémarrage,
        déploiement, création) → « EA démarré / reprise » non notifiés (Bot 4.8.5) ; False : relance par le watchdog."""
        if by_bot:
            self.db.meta_set(f"bot_start_{inst.id}", str(int(time.time())))
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
            wait = self.cfg.timing.kill_after_s or self.cfg.timing.stop_wait_s      # Bot 3.12.0 : 10 s (avant 30)
            if await self.wait_exit(inst, wait):
                if hasattr(self.procs, "companions"):          # Bot 3.21.0 : processus annexes restés sous Wine
                    rest = await asyncio.to_thread(self.procs.companions, self.exe(inst))
                    if rest:
                        await asyncio.to_thread(self.procs.kill, self.exe(inst))
                return "propre"
        n = await asyncio.to_thread(self.procs.kill, self.exe(inst))   # attente de fin (jusqu'à 10 s) hors de la boucle du bot
        if n:
            log.warning("terminal %s arrêté de force (%d processus)", inst.id, n)
        else:                                                           # Bot 4.9.0 : déjà fermé, pas une anomalie
            log.info("terminal %s fermé (plus aucun processus)", inst.id)
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


class Gone:
    """Bot 3.11.0 : terminal fermé depuis au moins `grace_s` (contrôlé toutes les 10 s, hors de la boucle du bot)."""

    def __init__(self, ctx_or_mgr, inst: Instance, grace_s: float = 30.0, every_s: float = 10.0) -> None:
        self.mgr = getattr(ctx_or_mgr, "mgr", ctx_or_mgr)
        self.inst, self.grace_s, self.every_s = inst, grace_s, every_s
        self._next, self._down_since = time.monotonic() + every_s, None

    async def check(self) -> bool:
        now = time.monotonic()
        if now < self._next:
            return False
        self._next = now + self.every_s
        if await asyncio.to_thread(self.mgr.is_running, self.inst):
            self._down_since = None
            return False
        self._down_since = self._down_since or now
        return now - self._down_since >= self.grace_s


class Slots:
    """Bot 3.14.1 : lancements « 2 par 2 » en file glissante : une place se libère dès que l'EA du terminal a chargé
    (1er heartbeat, la partie lourde pour le CPU) ou au bout de free_after_s (un terminal très lent ne bloque plus
    toute la file) ; l'attente du vert continue, elle, sans limite."""

    def __init__(self, n: int, free_after_s: float = 600.0) -> None:
        self.sem = asyncio.Semaphore(max(1, int(n)))
        self.free_after_s = free_after_s

    async def take(self) -> "Slot":
        await self.sem.acquire()
        return Slot(self.sem, self.free_after_s)


class Slot:
    def __init__(self, sem: asyncio.Semaphore, free_after_s: float) -> None:
        self.sem, self.free_after_s, self.t0, self.done = sem, free_after_s, time.monotonic(), False

    def free(self) -> None:
        if not self.done:
            self.done = True
            self.sem.release()

    def check(self, loaded: bool) -> None:
        if loaded or time.monotonic() - self.t0 >= self.free_after_s:
            self.free()
