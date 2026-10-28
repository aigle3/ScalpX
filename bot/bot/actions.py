"""Actions métier (sans Telegram) : commandes EA, lancer/arrêter/redémarrer, suppression, rapports."""
from __future__ import annotations

import asyncio
import re
import time
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

# Commandes EA autorisées depuis Telegram (liste blanche : jamais de commande libre ni de chemin).
EA_CMDS = {
    "PAUSE", "REPRISE", "DEMARRER", "STATUT", "PING", "MAINTENANCE_ON", "MAINTENANCE_OFF", "VALIDER",
    "FERMER_TOUT", "ANNULER_OBJECTIF", "PRESET_SAVE", "PRESET_LOAD", "PRESET_DELETE", "RESET_PARAMS", "BOUTON",
    "POSITIONS", "PARAMS", "FILTRES", "RISQUE", "PRESETS", "RAPPORT", "AIDE", "STATS", "JOURNAL", "MULTI", "NEWS",
    "OUTILS", "RAPIDE", "LOT", "TP", "GRILLE", "SPREAD", "VACANCES", "CAPTURE", "COURBE", "SET", "OBJECTIF",
}
# Commandes qui modifient le trading / les positions : confirmation obligatoire si cible = all
DANGEROUS = {"FERMER_TOUT", "RESET_PARAMS", "PRESET_LOAD", "PRESET_DELETE", "ANNULER_OBJECTIF", "PAUSE", "MAINTENANCE_ON"}
# Menu « commandes EA » de la fiche compte
CARD_CMDS = [("📌 Positions", "POSITIONS"), ("⚙️ Params", "PARAMS"), ("🧪 Filtres", "FILTRES"), ("🛡 Risque", "RISQUE"),
             ("📊 Stats", "STATS"), ("📒 Journal", "JOURNAL"), ("📝 Rapport", "RAPPORT"), ("🎯 Objectif", "OBJECTIF"),
             ("📸 Capture", "CAPTURE"), ("📈 Courbe", "COURBE"), ("📡 Statut", "STATUT"), ("🏓 Ping", "PING")]

_ARG_OK = re.compile(r"^[\w .,:+%=/\-éèêàùçÉÈÊÀÙÇ]{1,64}$")


def valid_cmd(cmd: str, args: list[str]) -> str | None:
    """Message d'erreur si la commande/arguments sont refusés, sinon None."""
    if cmd.upper() not in EA_CMDS:
        return f"Commande inconnue : {cmd[:20]}"
    for a in args:
        if not _ARG_OK.match(a):
            return "Argument invalide."
    return None


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


async def send_cmd(ctx: Ctx, inst: Instance, cmd: str, args: list[str] | None = None) -> CmdResult:
    """Envoie une commande à l'EA et attend l'ack (⏳ → ✅/❌)."""
    return await ctx.cmdq.send_and_wait(inst.id, cmd, args or [], timeout=ctx.cfg.timing.ack_timeout_s)


async def send_cmd_many(ctx: Ctx, insts: list[Instance], cmd: str, args: list[str] | None = None) -> list[tuple[Instance, CmdResult]]:
    res = await asyncio.gather(*[send_cmd(ctx, i, cmd, args) for i in insts])
    return list(zip(insts, res))


async def action_start(ctx: Ctx, inst: Instance) -> str:
    if not ctx.mgr.folder_ok(inst):
        return "❌ Dossier de l'instance introuvable."
    if ctx.mgr.is_running(inst):
        ctx.db.set_desired(inst.id, DEMARRE)
        return "ℹ️ Déjà lancé."
    ctx.mgr.start(inst)
    return "🚀 Terminal lancé."


async def action_stop(ctx: Ctx, inst: Instance, force: bool = False) -> str:
    r = await ctx.mgr.stop(inst, force=force)
    return {"propre": "🛑 Terminal fermé proprement (positions conservées chez le broker).",
            "kill": "🛑 Terminal arrêté (kill après délai).",
            "deja_arrete": "ℹ️ Déjà arrêté.",
            "attente_cloture": "⏳ Clôture à 500 en cours : arrêt reporté (utilise « Forcer l'arrêt » si besoin)."}[r]


async def action_restart(ctx: Ctx, inst: Instance, force: bool = False) -> str:
    r = await ctx.mgr.restart(inst, force=force)
    return "🔄 Terminal relancé." if r == "relance" else "⏳ Clôture à 500 en cours : redémarrage reporté."


async def action_delete(ctx: Ctx, inst: Instance) -> str:
    """Arrêt, archivage du dossier, état ARCHIVE (jamais de suppression brute)."""
    await ctx.mgr.stop(inst, force=True, set_desired=ARRETE)
    try:
        dst = ctx.mgr.archive_folder(inst)
    except OSError as exc:
        return f"❌ Archivage impossible : {exc}"
    ctx.db.archive(inst.id)
    ctx.cmdq.purge(inst.id)
    return f"🗑 Compte archivé{f' ({dst.name})' if dst else ''}."
