"""Bot 3.7.2 : avant le redémarrage du bot (mise à jour), libérer assez de CPU pour qu'il redémarre vite.

Les terminaux sont fermés UN PAR UN ; après chaque fermeture, le CPU est mesuré pendant `wait_s` secondes.
Dès que la moyenne laisse au moins `target_free` % de CPU libre, on s'arrête là. Les terminaux fermés passent en
MAINTENANCE (le watchdog de l'ancien bot ne les relance pas) ; le nouveau bot les remet en DEMARRE et les relance
(échelonnés) à son démarrage. Positions et ordres restent chez le broker (TP en place), comme pour une mise à jour de l'EA.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Awaitable, Callable

from storage.db import DEMARRE, MAINTENANCE, Database

log = logging.getLogger(__name__)

META_KEY = "cpu_freed"                    # identifiants fermés avant le redémarrage, à remettre en DEMARRE au démarrage


def default_cpu() -> float:
    import psutil
    return float(psutil.cpu_percent(interval=None))


async def measure(cpu_fn: Callable[[], float], wait_s: float, sample_s: float) -> float:
    """CPU moyen (%) sur wait_s secondes (le premier appel de psutil sert de point de départ)."""
    await asyncio.to_thread(cpu_fn)
    vals: list[float] = []
    end = time.monotonic() + wait_s
    while True:
        await asyncio.sleep(sample_s)
        vals.append(await asyncio.to_thread(cpu_fn))
        if time.monotonic() >= end:
            break
    return sum(vals) / len(vals) if vals else 0.0


async def free_cpu(db: Database, mgr, cpu_fn: Callable[[], float] | None = None, target_free: float = 70.0,
                   wait_s: float = 30.0, sample_s: float = 2.0,
                   step: Callable[[str], Awaitable[None]] | None = None) -> list[str]:
    """Ferme les terminaux un par un jusqu'à `target_free` % de CPU libre. Renvoie les identifiants fermés."""
    cpu_fn = cpu_fn or default_cpu
    closed: list[str] = []

    async def say(text: str) -> None:
        log.info("libération du CPU : %s", text)
        if step:
            try:
                await step(text)
            except Exception:
                log.exception("affichage de la progression")

    cpu = await measure(cpu_fn, min(wait_s, 5.0), sample_s)
    if 100.0 - cpu >= target_free:
        await say(f"CPU libre {100 - cpu:.0f} % : aucun terminal à fermer")
        return closed
    running = [i for i in db.list(states=(DEMARRE,)) if mgr.is_running(i)]
    for k, inst in enumerate(running, 1):
        db.set_desired(inst.id, MAINTENANCE)                     # l'ancien bot ne le relance pas
        r = await mgr.stop(inst, force=False, set_desired=None)
        if r == "attente_cloture":                                # clôture 500 en cours : on n'y touche pas
            db.set_desired(inst.id, DEMARRE)
            await say(f"{inst.label} : clôture 500 en cours, laissé ouvert")
            continue
        closed.append(inst.id)
        db.meta_set(META_KEY, json.dumps(closed))
        await say(f"{k}/{len(running)} terminal(aux) fermé(s) ({inst.label}), mesure du CPU pendant {int(wait_s)} s…")
        cpu = await measure(cpu_fn, wait_s, sample_s)
        if 100.0 - cpu >= target_free:
            await say(f"CPU libre {100 - cpu:.0f} % après {len(closed)} fermeture(s) : redémarrage du bot")
            return closed
        await say(f"CPU libre {100 - cpu:.0f} % (< {target_free:.0f} %) : fermeture du suivant")
    await say(f"tous les terminaux fermés ({len(closed)}) : redémarrage du bot")
    return closed


def restore_after_restart(db: Database) -> list[str]:
    """Au démarrage du nouveau bot : les terminaux fermés pour le redémarrage repassent en DEMARRE (relancés ensuite)."""
    raw = db.meta_get(META_KEY) or ""
    if not raw:
        return []
    try:
        ids = [str(x) for x in json.loads(raw)]
    except ValueError:
        ids = []
    for iid in ids:
        inst = db.get(iid)
        if inst is not None and inst.desired_state == MAINTENANCE:
            db.set_desired(iid, DEMARRE)
    db.meta_set(META_KEY, "")
    return ids
