"""Actions métier (sans Telegram) : commandes EA, lancer/arrêter/redémarrer, suppression, rapports."""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable
from dataclasses import dataclass, field

from core.commands import CmdResult, CommandQueue
from core.config import Config
from core.status import StatusReader
from core.summary import AccountView, snapshot
from mt5 import base as basemod
from mt5.creator import InstanceCreator
from mt5.deploy import DeployManager
from mt5.manager import InstanceManager
from mt5.watchdog import Watchdog
from storage.db import ARRETE, DEMARRE, Database, Instance

# Menu « commandes EA » de la fiche compte
CARD_CMDS = [("📌 Positions", "POSITIONS"), ("⚙️ Params", "PARAMS"), ("🧪 Filtres", "FILTRES"), ("🛡 Risque", "RISQUE"),
             ("📰 News", "NEWS"), ("📒 Journal", "JOURNAL"), ("📝 Rapport", "RAPPORT"), ("🎯 Objectif", "OBJECTIF"),
             ("📸 Capture", "CAPTURE"), ("📈 Courbe", "COURBE"), ("📡 Statut", "STATUT"), ("🏓 Ping", "PING")]

# Consultations : jamais écrites dans le journal des actions (qui ne garde que ce qui change quelque chose)
READ_ONLY_CMDS = {"POSITIONS", "PARAMS", "FILTRES", "RISQUE", "NEWS", "JOURNAL", "RAPPORT", "CAPTURE", "COURBE", "STATUT",
                  "PING", "PRESETS"}


@dataclass
class Ctx:
    cfg: Config
    db: Database
    status: StatusReader
    cmdq: CommandQueue
    mgr: InstanceManager
    creator: InstanceCreator
    deploy: DeployManager
    watchdog: Watchdog
    started_at: float = field(default_factory=time.time)

    def views(self) -> list[AccountView]:
        return snapshot(self.cfg, self.db, self.status, self.mgr.is_running, self.mgr.folder_ok,
                        basemod.read_version(self.cfg.paths.base))

    def view_of(self, iid: str) -> AccountView | None:
        return next((v for v in self.views() if v.inst.id == iid), None)


def target(ctx: "Ctx", iid: str):
    """Compte du registre, ou groupe « tous les comptes » (terminaux lancés) pour ALL_ID ; None si introuvable/vide."""
    from .ui import ALL_ID, Group
    if iid == ALL_ID:
        members = [i for i in ctx.db.list() if ctx.mgr.is_running(i)]
        return Group(members) if members else None
    return ctx.db.get(iid)


async def send_cmd(ctx: Ctx, inst: Instance, cmd: str, args: list[str] | None = None) -> CmdResult:
    """Envoie une commande à l'EA et attend l'ack (⏳ → ✅/❌). Les commandes qui changent quelque chose sont
    inscrites au journal des actions (🛠 Système)."""
    r = await ctx.cmdq.send_and_wait(inst.id, cmd, args or [], timeout=ctx.cfg.timing.ack_timeout_s)
    if cmd.upper() not in READ_ONLY_CMDS:
        journal(ctx, inst, " ".join([cmd.upper(), *[str(a) for a in (args or [])]]), r.ok, r.message)
    return r


def journal(ctx: Ctx, inst: Instance, text: str, ok: bool, result: str = "") -> None:
    """Inscrit une action demandée depuis Telegram (jamais bloquant : une erreur de base n'empêche pas l'action)."""
    try:
        ctx.db.add_action(inst.id, text, ok, " ".join(result.split()))
    except Exception:                                   # noqa: BLE001
        pass


async def action_start(ctx: Ctx, inst: Instance) -> str:
    if not ctx.mgr.folder_ok(inst):
        journal(ctx, inst, "TERMINAL lancer", False, "dossier introuvable")
        return "❌ Dossier de l'instance introuvable."
    if ctx.mgr.is_running(inst):
        ctx.db.set_desired(inst.id, DEMARRE)
        return "ℹ️ Déjà lancé."
    ctx.mgr.start(inst)
    journal(ctx, inst, "TERMINAL lancer", True)
    return "🚀 Terminal lancé."


async def action_stop(ctx: Ctx, inst: Instance, force: bool = False) -> str:
    r = await ctx.mgr.stop(inst, force=force)
    if r != "deja_arrete":
        journal(ctx, inst, "TERMINAL arrêter" + (" (forcé)" if force else ""), r != "attente_cloture", r)
    return {"propre": "🛑 Terminal fermé proprement (positions conservées chez le broker).",
            "kill": "🛑 Terminal arrêté (kill après délai).",
            "deja_arrete": "ℹ️ Déjà arrêté.",
            "attente_cloture": "⏳ Clôture à 500 en cours : arrêt reporté (utilise « Forcer l'arrêt » si besoin)."}[r]


async def action_restart(ctx: Ctx, inst: Instance, force: bool = False) -> str:
    r = await ctx.mgr.restart(inst, force=force)
    journal(ctx, inst, "TERMINAL redémarrer" + (" (forcé)" if force else ""), r == "relance", r)
    return "🔄 Terminal relancé." if r == "relance" else "⏳ Clôture à 500 en cours : redémarrage reporté."


async def wait_online(ctx: Ctx, inst: Instance, since: float, timeout: float,
                      tick: "Callable[[int, str], Awaitable[None]] | None" = None) -> tuple[bool, str]:
    """Bot 3.9.0 : attend que l'EA d'un terminal relancé réponde (heartbeat écrit après `since`) ET soit connecté au serveur.
    Bot 3.11.0 : timeout <= 0 = sans limite (le temps qu'il faut) ; abandon si le terminal reste fermé 30 s."""
    end = time.monotonic() + timeout if timeout > 0 else float("inf")
    last, shown = "aucun signe de vie", time.monotonic()
    from mt5.manager import Gone
    gone = Gone(ctx, inst)
    while time.monotonic() < end:
        st = ctx.status.read(inst.id)
        if st is not None and st.ts >= since - 2:
            if st.connecte:
                return True, f"🟢 en ligne et connecté en {int(time.time() - since)} s"
            last = "EA lancé, pas encore connecté au serveur"
        if await gone.check():
            return False, f"⚠️ terminal fermé pendant l'attente ({last}) : je passe au suivant"
        if tick and time.monotonic() - shown >= 5:
            shown = time.monotonic()
            await tick(int(time.time() - since), last)
        await asyncio.sleep(1.0)
    return False, f"⚠️ pas de réponse en {int(timeout)} s ({last}) : je passe au suivant"



async def action_delete(ctx: Ctx, inst: Instance) -> str:
    """Arrêt, archivage du dossier, état ARCHIVE (jamais de suppression brute)."""
    await ctx.mgr.stop(inst, force=True, set_desired=ARRETE)
    try:
        dst = ctx.mgr.archive_folder(inst)
    except OSError as exc:
        return f"❌ Archivage impossible : {exc}"
    ctx.db.archive(inst.id)
    ctx.cmdq.purge(inst.id)
    journal(ctx, inst, "COMPTE supprimé (archivé)", True)
    return f"🗑 Compte archivé{f' ({dst.name})' if dst else ''}."
