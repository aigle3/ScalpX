"""Labo des filtres (EA 7.1.0) : configuration des scénarios, stockage des événements, analyse, écran Telegram."""
import json
import time

import pytest

from bot import render
from core import labo
from core.service import Monitor
from storage.db import DEMARRE, Instance
from tests.test_bot import svc  # noqa: F401  (fixture)

FIL = {c: [0, 0] for c in labo.CODES}


def reel(atteint=True, entree=60, profit=0.6, **fl):
    f = dict(FIL)
    f.update(fl)
    return {"creneau": "4:00", "atteint": atteint, "profit": profit, "entree_s": entree, "attente_s": 600,
            "signal_s": 500, "bloque_s": sum(v[0] for v in fl.values()), "filtres": f}


def serie(sc, res, profit=0.6, dd=2.0, entree=30, spec=None):
    return {"sc": sc, "nom": sc, "spec": spec or ",".join(["on"] * 11), "res": res, "profit": profit, "dd": dd,
            "entree_s": entree, "ml_min": 900.0}


# ------------------------------------------------------------------ configuration (fichier lu par les EA)
def test_config_aller_retour_et_jetons(tmp_path):
    cfg = labo.load_config(tmp_path)
    assert cfg.active and cfg.reset == "" and cfg.customs == [] and cfg.next_key() == "p1"
    toks = ["="] * 11
    toks[0], toks[2] = "+20", "off"
    cfg.customs.append(labo.Custom("p1", labo.auto_name(toks), toks))
    cfg.reset, cfg.active = "1700000000", False
    labo.save_config(tmp_path, cfg)
    txt = (tmp_path / labo.SCEN_FILE).read_text(encoding="utf-8")
    assert "actif=0" in txt and "reset=1700000000" in txt and "p1|" in txt and "+20,=,off" in txt
    back = labo.load_config(tmp_path)
    assert not back.active and back.reset == "1700000000" and back.customs[0].toks == toks and back.next_key() == "p2"
    assert labo.auto_name(toks) == "Volatilité (ATR) assoupli 20 %, Volume désactivé"
    # cycle des réglages : seuils seulement pour les filtres qui en ont
    assert [labo.next_tok(0, t) for t in ("=", "off", "on", "+50", "-30")] == ["off", "on", "+10", "-10", "="]
    assert [labo.next_tok(1, t) for t in ("=", "off", "on")] == ["off", "on", "="]


# ------------------------------------------------------------------ analyse
def test_journal_reel_qui_bloque_et_objectifs_rates():
    r = labo.compute_real([reel(), reel(atteint=False, entree=-1, profit=0, vol=[300, 200], hi=[100, 0]),
                           reel(atteint=False, entree=-1, profit=0)])
    assert r.goals == 3 and r.won == 1 and r.no_entry == 2 and r.no_entry_filters == 1
    assert r.filters["vol"].seconds == 300 and r.filters["vol"].sole == 200 and r.filters["vol"].goals_missed == 1
    assert r.filters["hi"].goals_missed == 0


def test_scenarios_et_verdicts():
    rows = []
    for _ in range(6):
        rows += [serie("miroir", "atteint"), serie("miroir", "annule", 0, entree=-1)]
        rows += [serie("t2", "atteint", spec=",".join(["on", "on", "off"] + ["on"] * 8)) for _ in range(2)]
        rows += [serie("t4", "atteint"), serie("t4", "sl", -12.5, dd=10)]
        rows += [serie("aucun", "atteint"), serie("aucun", "atteint", 0.6, dd=35)]
    rows.append(serie("miroir", "saute"))
    st = labo.compute_scen(rows)
    m = st["miroir"]
    assert m.launched == 12 and m.won == 6 and m.missed == 6 and m.skipped == 1 and m.no_entry == 6
    assert labo.verdict(st["t2"], m).icon == "🟢"                 # même risque, plus d'objectifs : trop restrictif
    assert labo.verdict(st["t4"], m).icon == "🛡️"                # des pertes en plus : le filtre protège
    assert labo.verdict(st["aucun"], m).icon != "🟢"              # drawdown bien plus profond : pas « meilleur »
    few = labo.compute_scen([serie("miroir", "atteint"), serie("t2", "atteint")])
    assert labo.verdict(few["t2"], few["miroir"]).icon == "⏳"
    assert labo.spec_diff_text(st["t2"].spec, m.spec) == "Volume désactivé"


def test_rendu_rapport_labo():
    real = labo.compute_real([reel(atteint=False, entree=-1, profit=0, vol=[3700, 3600])])
    st = labo.compute_scen([serie("miroir", "atteint")] * 6 + [serie("t2", "atteint", 1.0)] * 6)
    st["t2"].name = "Sans Volume"
    txt = render.labo_text(real, st, 7, "tous les comptes (2)", "EUR")
    assert "Labo des filtres" in txt and "Qui bloque" in txt and "Volume : 1 h 01" in txt and "1 objectif(s) raté(s)" in txt
    assert "Sans Volume" in txt and "(+2.40)" in txt
    assert "Aucun objectif réel" in render.labo_text(labo.compute_real([]), {}, 1, "x", "EUR")
    # rapport trop long : lignes abrégées sans casser le HTML
    many = {f"p{i}": labo.ScenStat(f"p{i}", "n" * 300, launched=6, won=3) for i in range(30)}
    long = render.labo_text(real, many, 7, "x", "EUR", max_len=1500)
    assert len(long) <= 1500 and long.count("<b>") == long.count("</b>")


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


# ------------------------------------------------------------------ écran Telegram
@pytest.mark.asyncio
async def test_ecran_labo_scenarios_perso_pause_et_remise_a_zero(svc):  # noqa: F811
    ctx, sender, dash, router, notifier, ea, conf = svc
    common = ctx.cfg.paths.common()
    ctx.db.add_instance(Instance("7_b", 7, "b", "Bravo", str(ctx.cfg.paths.instances / "7_b"), 1, "7.1.0", DEMARRE))
    now = int(time.time())
    for d in [serie("miroir", "atteint")] * 6 + [serie("t2", "atteint", 1.0, spec=",".join(["on", "on", "off"] + ["on"] * 8))] * 6:
        ctx.db.add_labo("7_b", now - 5, "SERIE", d["sc"], json.dumps(d))
    await router.on_callback("d:rpt")
    assert any(d == "d:lab" for row in dash.render()[1] for _, d in row)
    await router.on_callback("d:lab:14")
    text, kb = dash.render()
    assert "Labo des filtres" in text and "14 j" in text and "Réglages actuels" not in text and "miroir" in text
    await router.on_callback("d:laba")                             # compte suivant : Bravo
    assert dash.lab_acc == "7_b" and "Bravo" in dash.render()[0]
    await router.on_callback("d:laba")
    assert dash.lab_acc == ""
    # scénario perso : Volume désactivé, ATR assoupli 10 %
    await router.on_callback("d:labs")
    await router.on_callback("d:labn")
    await router.on_callback("d:labv")                             # identique au compte : refusé
    assert "identique" in dash.render()[0]
    await router.on_callback("d:labt:2")
    for _ in range(3):
        await router.on_callback("d:labt:0")                       # = → off → on → +10
    await router.on_callback("d:labv")
    cfg = labo.load_config(common)
    assert [c.key for c in cfg.customs] == ["p1"] and cfg.customs[0].toks[:3] == ["+10", "=", "off"]
    assert "p1|" in (common / labo.SCEN_FILE).read_text(encoding="utf-8")
    await router.on_callback("d:labe:p1")
    await router.on_callback("d:labt:2")                           # off → on
    await router.on_callback("d:labv")
    assert labo.load_config(common).customs[0].toks[2] == "on"
    await router.on_callback("d:labd:p1")
    assert labo.load_config(common).customs == []
    # pause puis remise à zéro (confirmée)
    await router.on_callback("d:labp")
    assert not labo.load_config(common).active and "Labo en pause" in dash.render()[0]
    await router.on_callback("d:labr")
    from tests.test_bot import _confirm, _until
    await _confirm(router, dash)
    assert await _until(lambda: labo.load_config(common).reset != "")
    assert "Aucun objectif virtuel" in dash.render()[0]           # les séries d'avant la remise à zéro ne comptent plus
