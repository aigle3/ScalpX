"""Création d'une instance depuis Telegram (y compris la toute première).

Le mot de passe n'existe qu'en mémoire pendant la création, puis dans le .ini temporaire, supprimé
dans TOUS les cas (succès ou échec). Il n'est jamais journalisé : tous les textes passent par mask().
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from core.config import Config
from core.ids import make_id, valid_login, valid_server
from core.models import Statut
from core.status import StatusReader
from storage.db import ARRETE, DEMARRE, EN_CREATION, Database, Instance
from . import base as basemod
from .ini import IniError, build_ini, mask, remove_ini, write_ini
from .manager import InstanceManager

log = logging.getLogger(__name__)
Progress = Callable[[str], Awaitable[None]]


@dataclass
class CreateResult:
    ok: bool
    message: str
    iid: str = ""
    status: Statut | None = None
    warnings: list[str] = field(default_factory=list)
    refused: bool = False                       # identifiants refusés : terminal arrêté, dossier gardé pour un nouvel essai


async def _noop(_: str) -> None:
    return None


class InstanceCreator:
    def __init__(self, cfg: Config, db: Database, mgr: InstanceManager, status: StatusReader) -> None:
        self.cfg, self.db, self.mgr, self.status = cfg, db, mgr, status
        self._lock = asyncio.Lock()                      # une seule création à la fois

    # ---- contrôles préalables (sans rien modifier)
    def precheck(self, login: str, server: str) -> str | None:
        """Message d'erreur si la création est impossible, sinon None."""
        if not valid_login(login):
            return "Numéro de compte invalide (chiffres uniquement)."
        if not valid_server(server):
            return "Nom de serveur invalide (lettres, chiffres, espace, - . _ ; 2 à 64 caractères)."
        iid = make_id(login, server)
        if self.db.exists_active(iid):
            return f"Ce compte existe déjà ({iid})."
        problems = basemod.check_base(self.cfg)
        if problems:
            return "BASE non conforme : " + " ; ".join(problems)
        from . import prepare                                 # Bot 4.5.0 : MT5 de BASE a fini sa compilation de départ ?
        if not prepare.is_ready(self.cfg.paths.base, self.cfg.mt5.terminal_exe):
            return ("MT5 se prépare (compilation de départ du dossier BASE, 2 à 10 min) : réessaie quand le bot annonce "
                    "« ✅ MT5 prêt ». Sans ça, chaque compte refait cette compilation à 100 % de CPU.")
        if server.lower().startswith("roboforex") and not (self.cfg.paths.base / ".scalpx_serveurs_roboforex").exists():
            return ("Serveurs RoboForex absents du MT5 de BASE (installé depuis l'installeur MetaQuotes) : le compte ne "
                    "pourrait pas se connecter. 🛠 Système → 🔧 Installation système les ajoute, sans toucher aux comptes.")
        need = max(self.cfg.alerts.min_disk_gb, basemod.folder_size_gb(self.cfg.paths.base) * 1.5)
        free = basemod.free_disk_gb(self.cfg.paths.instances)
        if free < need:
            return f"Espace disque insuffisant ({free:.1f} Go libres, {need:.1f} Go requis)."
        dest = self.cfg.paths.instances / iid
        if dest.exists():
            return f"Un dossier existe déjà pour {iid} sans être enregistré : vérifie-le manuellement."
        return None

    async def _wait_heartbeat(self, iid: str, login: int, server: str, timeout: float, since: float,
                              want_version: str | None = None) -> tuple[Statut | None, Statut | None]:
        """Attend un heartbeat récent dont login ET serveur correspondent, connecté = vrai.
        Renvoie (statut valide, dernier statut vu même s'il n'est pas valide)."""
        end = time.monotonic() + timeout
        last: Statut | None = None
        refused_since = 0.0
        while time.monotonic() < end:
            st = self.status.read(iid)
            if st is not None and st.ts >= since - 5:
                last = st
                match = (st.id == iid) or (st.login == login and st.serveur == server)
                if match and st.connecte and (want_version is None or st.version == want_version):
                    return st, last
                # l'EA tourne mais le compte reste « non connecté » : identifiants refusés, inutile d'attendre la fin
                if match and not st.connecte:
                    refused_since = refused_since or time.monotonic()
                    if time.monotonic() - refused_since >= self.cfg.timing.refused_s:
                        log.info("création %s : compte non connecté depuis %d s : identifiants refusés", iid, self.cfg.timing.refused_s)
                        return None, last
                else:
                    refused_since = 0.0
            await asyncio.sleep(1.0)
        return None, last

    async def _attempt(self, inst: Instance, password: str, progress: Progress) -> tuple[Statut | None, Statut | None]:
        """Écrit le .ini, lance avec /config, attend un heartbeat valide ; le .ini est TOUJOURS supprimé."""
        folder = Path(inst.folder)
        try:
            content = build_ini(inst.login, password, inst.server, self.cfg.mt5.ea_name, self.cfg.mt5.set_name,
                                self.cfg.mt5.symbol, self.cfg.mt5.period, self.cfg.mt5.profile,
                                self.cfg.mt5.max_bars)
            ini = write_ini(folder, content)
            del content
            await progress("🚀 Lancement du terminal et connexion au broker…")
            t0 = time.time()
            self.mgr.launch(inst, ini)
            return await self._wait_heartbeat(inst.id, inst.login, inst.server, self.cfg.timing.create_timeout_s, t0)
        finally:
            remove_ini(folder)

    @staticmethod
    def _why(last: Statut | None) -> str:
        if last is not None and not last.connecte:
            return "L'EA s'est chargé mais le compte n'est pas connecté : mot de passe ou serveur incorrect ?"
        if last is None:
            return "Aucun heartbeat de l'EA : terminal non démarré ou EA non chargé (vérifie BASE)."
        return "Heartbeat reçu pour un autre compte."

    async def _finalize(self, inst: Instance, st: Statut, progress: Progress) -> CreateResult:
        """Contrôle : redémarrage avec le .ini de relance SANS identifiants (mémorisation du compte, EA rouvert, un seul graphique)."""
        warnings: list[str] = []
        if st.nb_graphiques_ea > 1:
            warnings.append(f"👯 {st.nb_graphiques_ea} graphiques portent l'EA (doublon) !")
        await progress("🔁 Contrôle : redémarrage avec reconnexion automatique…")
        r = await self.mgr.stop(inst, force=False, set_desired=None)
        if r == "attente_cloture":
            await self.mgr.stop(inst, force=True, set_desired=None)
        t1 = time.time()
        self.mgr.launch(inst)
        st2, _ = await self._wait_heartbeat(inst.id, inst.login, inst.server, self.cfg.timing.create_timeout_s, t1)
        if st2 is None:
            return CreateResult(False, "Le terminal ne se reconnecte pas seul après redémarrage "
                                       "(mot de passe non mémorisé ou profil non rechargé).", inst.id, st)
        warnings = [w for w in warnings if "doublon" not in w]
        if st2.nb_graphiques_ea > 1:
            warnings.append(f"👯 {st2.nb_graphiques_ea} graphiques portent l'EA (doublon) !")
        if self.cfg.alerts.expected_currency and st2.devise and st2.devise != self.cfg.alerts.expected_currency:
            warnings.append(f"💱 Devise {st2.devise} ≠ {self.cfg.alerts.expected_currency} : les seuils sont dans la devise du compte.")
        self.db.set_version(inst.id, st2.version, "connexion")
        self.db.set_currency(inst.id, st2.devise)
        self.db.set_desired(inst.id, DEMARRE)
        return CreateResult(True, "Compte connecté.", inst.id, st2, warnings)

    async def create(self, login: str, password: str, server: str, alias: str = "",
                     progress: Progress = _noop, keep_on_refusal: bool = False, exclusive: bool = True) -> CreateResult:
        """Déroulé complet. Aucune instance fantôme ne reste en cas d'échec.
        keep_on_refusal : identifiants refusés → terminal arrêté, dossier gardé (EN_CREATION) pour retry() ou abandon().
        exclusive=False (Bot 3.20.0, création en lot) : plusieurs créations en même temps (le lot limite lui-même)."""
        async def go() -> CreateResult:
            try:
                return await self._create(login.strip(), password, server, alias.strip(), progress, keep_on_refusal)
            except Exception as exc:                      # le message ne doit JAMAIS contenir le mot de passe
                log.error("création : erreur inattendue : %s", mask(repr(exc), password))
                return CreateResult(False, "Erreur inattendue : " + mask(str(exc), password))
        if not exclusive:
            return await go()
        async with self._lock:
            return await go()

    async def _create(self, login: str, password: str, server: str, alias: str, progress: Progress,
                      keep_on_refusal: bool = False) -> CreateResult:
        err = self.precheck(login, server)
        if err:
            return CreateResult(False, err)
        iid = make_id(login, server)
        dest = self.cfg.paths.instances / iid
        inst = Instance(iid, int(login), server, alias[:32], str(dest), int(time.time()), "", EN_CREATION)
        self.db.add_instance(inst)                        # EN_CREATION : ignoré par le watchdog
        try:
            await progress("📁 Copie de BASE…")
            self.cfg.paths.instances.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(basemod.copy_base, self.cfg.paths.base, dest)
            st, last = await self._attempt(inst, password, progress)
        except IniError as exc:
            return await self._fail(inst, "Données invalides : " + mask(str(exc), password))
        except Exception as exc:
            return await self._fail(inst, "Échec de création : " + mask(str(exc), password))
        finally:
            remove_ini(dest)                              # le .ini disparaît dans TOUS les cas
        if st is None:
            if keep_on_refusal and last is not None and not last.connecte:
                return await self._keep_refused(inst, last)
            return await self._fail(inst, self._why(last))
        res = await self._finalize(inst, st, progress)
        if not res.ok:
            return await self._fail(inst, res.message)
        return res

    async def _keep_refused(self, inst: Instance, last: Statut) -> CreateResult:
        """Identifiants refusés : terminal arrêté, .ini effacé, dossier et instance (EN_CREATION, ignorée par le watchdog)
        gardés pour un nouvel essai sans recopier BASE."""
        await self.mgr.stop(inst, force=True, set_desired=None)
        remove_ini(Path(inst.folder))
        self.status.remove(inst.id)
        self._mark_refused(inst.id, True)
        log.warning("création %s : identifiants refusés, nouvel essai possible", inst.id)
        return CreateResult(False, self._why(last), inst.id, last, refused=True)

    # ---- créations refusées en attente d'un nouvel essai (survivent à un redémarrage du bot → nettoyées au démarrage)
    def _refused_list(self) -> list[str]:
        import json
        try:
            v = json.loads(self.db.meta_get("creations_refusees", "[]") or "[]")
        except ValueError:
            v = []
        return [x for x in v if isinstance(x, str)]

    def _mark_refused(self, iid: str, on: bool) -> None:
        import json
        cur = self._refused_list()
        new = [x for x in cur if x != iid] + ([iid] if on else [])
        if new != cur:
            self.db.meta_set("creations_refusees", json.dumps(new))

    async def cleanup_refused(self) -> int:
        """Démarrage : les créations refusées laissées en suspens (bot redémarré entre-temps) sont abandonnées."""
        n = 0
        for iid in self._refused_list():
            inst = self.db.get(iid)
            if inst is not None and inst.desired_state == EN_CREATION:
                await self.abandon(iid, "création abandonnée (bot redémarré)")
                n += 1
            else:
                self._mark_refused(iid, False)
        return n

    async def retry(self, iid: str, password: str, progress: Progress = _noop) -> CreateResult:
        """Nouveau mot de passe pour une création refusée : même dossier, pas de nouvelle copie de BASE.
        Refusé encore : dossier gardé (refused=True) ; autre échec : nettoyage complet."""
        async with self._lock:
            inst = self.db.get(iid)
            if inst is None or inst.desired_state != EN_CREATION or not Path(inst.folder).is_dir():
                return CreateResult(False, "Création introuvable (abandonnée ?) : recommence avec ➕ Nouveau compte.", iid)
            try:
                st, last = await self._attempt(inst, password, progress)
            except IniError as exc:
                return await self._fail(inst, "Données invalides : " + mask(str(exc), password))
            except Exception as exc:
                log.error("nouvel essai : erreur inattendue : %s", mask(repr(exc), password))
                return await self._fail(inst, "Échec de création : " + mask(str(exc), password))
            finally:
                remove_ini(Path(inst.folder))
            if st is None:
                if last is not None and not last.connecte:
                    return await self._keep_refused(inst, last)
                return await self._fail(inst, self._why(last))
            self._mark_refused(iid, False)
            res = await self._finalize(inst, st, progress)
            if not res.ok:
                return await self._fail(inst, res.message)
            return res

    async def abandon(self, iid: str, why: str = "création abandonnée") -> None:
        """Création refusée qu'on n'essaie plus : nettoyage complet (comme un échec)."""
        async with self._lock:
            inst = self.db.get(iid)
            if inst is not None and inst.desired_state == EN_CREATION:
                await self._fail(inst, why)

    async def reconnect(self, iid: str, password: str, progress: Progress = _noop) -> CreateResult:
        """« Reconnecter » : relance l'assistant sur le dossier existant ; s'il est inutilisable,
        archive l'ancien dossier et recopie BASE. Le registre et l'historique sont conservés."""
        async with self._lock:
            inst = self.db.get(iid)
            if inst is None:
                return CreateResult(False, "Compte inconnu.")
            prev = inst.desired_state
            self.db.set_desired(iid, EN_CREATION)         # le watchdog ignore pendant l'opération
            try:
                return await self._reconnect(inst, prev, password, progress)
            except Exception as exc:
                log.error("reconnexion : erreur inattendue : %s", mask(repr(exc), password))
                self.db.set_desired(iid, ARRETE)
                return CreateResult(False, "Erreur inattendue : " + mask(str(exc), password), iid)
            finally:
                remove_ini(Path(inst.folder))

    async def _reconnect(self, inst: Instance, prev: str, password: str, progress: Progress) -> CreateResult:
        r = await self.mgr.stop(inst, force=False, set_desired=None)
        if r == "attente_cloture":
            await self.mgr.stop(inst, force=True, set_desired=None)
        last: Statut | None = None
        if Path(inst.folder).is_dir():
            try:
                st, last = await self._attempt(inst, password, progress)
            except IniError as exc:
                self.db.set_desired(inst.id, prev)
                return CreateResult(False, "Données invalides : " + mask(str(exc), password), inst.id)
            if st is not None:
                res = await self._finalize(inst, st, progress)
                if not res.ok:
                    await self.mgr.stop(inst, force=True, set_desired=ARRETE)
                return res
            if last is not None and not last.connecte:                 # dossier sain, identifiants refusés
                await self.mgr.stop(inst, force=True, set_desired=None)
                self.db.set_desired(inst.id, prev if prev != EN_CREATION else ARRETE)
                return CreateResult(False, self._why(last), inst.id)
            await progress("🧰 Dossier inutilisable : archivage et nouvelle copie depuis BASE…")
            await self.mgr.stop(inst, force=True, set_desired=None)
            self.mgr.archive_folder(inst, "AVANT_RECONNEXION")
        problems = basemod.check_base(self.cfg)
        if problems:
            self.db.set_desired(inst.id, ARRETE)
            return CreateResult(False, "BASE non conforme : " + " ; ".join(problems), inst.id)
        await progress("📁 Copie de BASE…")
        await asyncio.to_thread(basemod.copy_base, self.cfg.paths.base, Path(inst.folder))
        st, last = await self._attempt(inst, password, progress)
        if st is None:
            await self.mgr.stop(inst, force=True, set_desired=ARRETE)
            return CreateResult(False, self._why(last), inst.id)
        res = await self._finalize(inst, st, progress)
        if not res.ok:
            await self.mgr.stop(inst, force=True, set_desired=ARRETE)
        return res

    async def _fail(self, inst: Instance, why: str) -> CreateResult:
        """Échec : arrêt du terminal, archivage du dossier, suppression du registre (aucun fantôme)."""
        try:
            await self.mgr.stop(inst, force=True, set_desired=None)
        except Exception:
            pass
        remove_ini(Path(inst.folder))
        try:
            self.mgr.archive_folder(inst, "ECHEC")
        except Exception as exc:
            log.error("archivage impossible : %s", exc)
        self.db.delete_instance(inst.id)
        self.status.remove(inst.id)
        self._mark_refused(inst.id, False)
        log.warning("création échouée pour %s : %s", inst.id, why)
        return CreateResult(False, why, inst.id)
