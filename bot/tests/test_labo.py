"""Séries virtuelles de l'optimiseur (EA 7.7.0) : stockage des événements et statistiques par scénario."""
import json

import pytest

from core import labo
from core.service import Monitor
from storage.db import DEMARRE, Instance


def serie(sc, res, profit=0.6, dd=2.0, entree=30, spec=None):
    return {"sc": sc, "nom": sc, "spec": spec or ",".join(["on"] * 11), "res": res, "profit": profit, "dd": dd,
            "entree_s": entree, "ml_min": 900.0}


def reel():
    return {"creneau": "4:00", "atteint": True, "profit": 0.6, "entree_s": 60, "filtres": {}}


def test_statistiques_par_scenario():
    rows = []
    for _ in range(6):
        rows += [serie("miroir", "atteint"), serie("miroir", "annule", 0, entree=-1)]
        rows += [serie("p4", "atteint"), serie("p4", "sl", -12.5, dd=10)]
        rows += [serie("p5", "liquide", -100, dd=99)]
    rows.append(serie("miroir", "saute"))
    st = labo.compute_scen(rows)
    m = st["miroir"]
    assert m.launched == 12 and m.won == 6 and m.missed == 6 and m.skipped == 1 and m.no_entry == 6 and m.rate == 0.5
    assert st["p4"].lost == 6 and st["p4"].dd_max == 10 and st["p4"].net == pytest.approx(-71.4)
    assert st["p5"].liquidations == 6 and st["p5"].ml_min == 900.0


# ------------------------------------------------------------------ événements de l'EA
@pytest.mark.asyncio
async def test_evenements_labo_stockes_jamais_notifies(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    db.add_instance(Instance("1_a", 1, "a", "Alpha", str(cfg.paths.instances / "1_a"), 1, "7.1.0", DEMARRE))
    from core.events import EventReader
    got = []

    async def emit(n):
        got.append(n)

    mon = Monitor(cfg, db, status, EventReader(cfg.paths.common()), emit)
    ea.event("1_a", "LABO_SERIE", "", 0, serie("t2", "atteint"), 1)
    ea.event("1_a", "LABO_REEL", "", 0, reel(), 2)
    ea.event("1_a", "LABO_ETAT", "", 0, {"sc": [{"sc": "miroir", "solde": 130}]}, 3)
    await mon.poll_events()
    assert got == [] and db.events_since(0) == []
    rows = db.labo_since(0)
    assert [r["kind"] for r in rows] == ["SERIE", "REEL"] and rows[0]["sc"] == "t2"
    assert json.loads(db.meta_get("labo_etat_1_a"))["sc"][0]["solde"] == 130
    db.prune()
    assert len(db.labo_since(0)) == 2
