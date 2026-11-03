"""🤖 Optimiseur autonome : variantes, décision (sécurité 60 j, choix 30 j, un cran), nuit complète, annulation, écran."""
import json
import time
from pathlib import Path

import pytest

from core import labo, optim
from tests.test_backtest import TesterProcs
from tests.test_bot import _confirm, _until, ctx_stack, svc  # noqa: F401  (fixture)
from tests.test_mt5 import seed

P = {"gridatr": "0.7", "gridmin": "200", "gridmax": "500", "dyngrid": "1", "maxpend": "8", "lotmult": "1.2",
     "sl_pct": "0.5", "f_fresh": "1", "freshbars": "12", "f_vol": "0", "f_atr": "1", "news_block": "1"}


def test_variantes_simples_puis_combinaisons():
    vs = optim.variants(P, "grille")
    assert [v.opts for v in vs[:6]] == [{"ga": "0.6"}, {"ga": "0.8"}, {"gn": "150"}, {"gn": "250"}, {"gx": "400"}, {"gx": "600"}]
    assert len(vs) == 18 and [v.key for v in vs] == [f"p{i}" for i in range(1, 19)]
    combo = vs[6]
    assert combo.opts == {"ga": "0.6", "gn": "150"} and combo.changes == {"gridatr": ("0.7", "0.6"), "gridmin": ("200", "150")}
    assert combo.name == "ATR grille 0,6 × ATR + Grille min 150 pts"
    f = optim.scenario_file(vs)
    assert f.startswith("# ") and "auto=0" in f and "p1|Multiplicateur ATR de la grille 0,6 × ATR|" in f and "|ga=0.6;gn=150" in f
    # min doit rester sous max, même combiné
    near = optim.variants({**P, "gridmin": "400", "gridmax": "500"}, "grille")
    assert all(float(v.changes.get("gridmin", ("", "400"))[1]) < float(v.changes.get("gridmax", ("", "500"))[1]) for v in near)
    assert optim.variants({**P, "dyngrid": "0"}, "grille") == []                     # grille fixe : rien à tester
    assert {v.param for v in optim.variants({**P, "f_fresh": "0"}, "objectif")} == {"sl_pct"}
    lots = optim.variants(P, "lots")
    assert len(lots) == 8 and {"mp": "9", "lm": "1.25"} in [v.opts for v in lots]
    flt = optim.variants(P, "filtres")
    assert len(flt) == 4 and flt[0].toks[labo.CODES.index("atr")] == "~" and flt[0].new == "0"
    assert optim.label_of("sl_pct", "0.5") == "Objectif : 0,5 %" and optim.value_text("f_vol", "1") == "activé"


def _rows(d_to, profits: dict, lost: tuple = (), n=20):
    rows = []
    for k in range(n):
        t = d_to - (25 - k) * 86400 // 2                    # tous dans les 30 derniers jours
        for sc, pr in profits.items():
            res = "liquide" if (sc, k) in lost else "atteint"
            rows.append({"sc": sc, "nom": sc, "res": res, "profit": -100 if res == "liquide" else pr, "dd": 95 if res == "liquide" else 30,
                         "fin": t, "entree_s": 10})
    return rows


def test_decision_securite_et_gain():
    d_to = 1_765_000_000
    vs = optim.variants(P, "grille")
    # p1 rapporte plus mais perd le compte une fois ; p7 (combinaison) rapporte plus et reste sûre → p7 appliquée entière
    d = optim.decide(_rows(d_to, {"miroir": 0.5, "p1": 2.0, "p7": 1.0}, lost=(("p1", 4),)), vs, d_to)
    assert d.change is not None and d.change.key == "p7" and len(d.change.changes) == 2 and "aucun compte perdu" in d.reason
    # rien de nettement meilleur → aucun changement
    d = optim.decide(_rows(d_to, {"miroir": 0.5, "p3": 0.51}), vs, d_to)
    assert d.change is None and "rien n'est changé" in d.note
    # réglages actuels dangereux : la variante sûre est prise même si elle rapporte un peu moins
    d = optim.decide(_rows(d_to, {"miroir": 0.6, "p4": 0.5}, lost=(("miroir", 2),)), vs, d_to)
    assert d.change is not None and d.change.key == "p4" and d.ref_unsafe and "perdent le compte" in d.reason
    # pas assez d'objectifs
    assert "pas assez" in optim.decide(_rows(d_to, {"miroir": 0.5}, n=3), vs, d_to).note


class OptTester(TesterProcs):
    """Testeur : joue les scénarios du fichier de l'optimiseur (p1 nettement meilleur)."""
    __test__ = False

    def launch(self, exe, ini=None):
        super().launch(exe, ini)
        files = sorted(self.common.glob("bt_*_scen.txt"))
        if files:
            rid = files[-1].name.split("_")[1]
            keys = ["miroir"] + [l.split("|")[0] for l in files[-1].read_text(encoding="utf-8").splitlines() if l.startswith("p")]
            now = int(time.time()) - 5 * 86400
            ev = [{"type": "LABO_SERIE", "ts": now, "data": {"sc": k, "nom": k, "res": "atteint", "profit": 1.5 if k == "p1" else 0.5,
                                                              "dd": 20, "fin": now + i, "entree_s": 10}}
                  for i in range(15) for k in keys]
            with open(self.common / f"events_bt_{rid}.jsonl", "a", encoding="utf-8") as f:
                f.write("\n" + "\n".join(json.dumps(e) for e in ev) + "\n")
        return 1234


@pytest.mark.asyncio
async def test_nuit_complete_application_et_annulation(svc):  # noqa: F811
    ctx, sender, dash, router, notifier, ea, conf = svc
    a = seed(ctx_stack(svc), 81, "Demo-Server 1")
    tp = OptTester(ctx.cfg.paths.common())
    tp.running = ctx.mgr.procs.running                      # mêmes terminaux lancés que le reste du test
    router.bt.procs = tp
    ctx.cfg.backtest.poll_s = 0.01
    router.bt.folder.mkdir(parents=True, exist_ok=True)
    (router.bt.folder / ctx.cfg.mt5.terminal_exe).write_bytes(b"MZ")
    ctx.db.meta_set("bt_account", json.dumps({"login": 1, "server": "S"}))
    ctx.db.meta_set("opt_grp", "3")                          # groupe 🛡️ Filtres (réglages connus du faux EA)
    ea.start()
    try:
        await router.on_callback("d:rpt")
        await router.on_callback("d:opt")
        text, kb = dash.render()
        assert "Optimiseur automatique" in text and "23:00" in text and "Filtres" in text
        await router.on_callback("d:opth")
        assert router.optim.hour == 0
        await router.optim.run(manual=True)
        msg = [s for s in sender.sent if "Optimiseur · " in s[1]][-1]
        assert "Changé" in msg[1] and "Filtre" in msg[1] and "→" in msg[1], msg[1]
        oid = ctx.db.opt_list(1)[0]["id"]
        applied = json.loads(ctx.db.opt_get(oid)["applied"])
        key, (old, new) = next(iter(applied.items()))
        assert ea.accounts[a.id]["params"][key] == ("✅" if new == "1" else "❌")
        assert any(d == f"d:opu:{oid}" for row in msg[2] for _, d in row)
        assert int(ctx.db.meta_get("opt_grp")) == 0                                    # groupe suivant
        assert not list(Path(ctx.cfg.paths.common()).glob("bt_*_scen.txt"))           # fichier de scénarios nettoyé
        assert "CHANGEMENT" in router.optim.path.read_text(encoding="utf-8")
        # annulation (confirmée)
        await router.on_callback(f"d:opu:{oid}")
        await _confirm(router, dash)
        assert await _until(lambda: ctx.db.opt_get(oid)["reverted"] == 1, 100)
        assert ea.accounts[a.id]["params"][key] == ("✅" if old == "1" else "❌")
        # journal et guide
        n = len(sender.sent)
        await router.on_callback("d:optj")
        await router.on_callback("d:optg")
        assert "Journal de l'optimiseur" in sender.sent[n][1] and "L'optimiseur, en bref" in sender.sent[n + 1][1]
    finally:
        await ea.stop()


def test_heure_de_lancement(svc):  # noqa: F811
    ctx, sender, dash, router, *_ = svc
    o = router.optim
    from datetime import datetime
    o._now = lambda: datetime(2026, 11, 3, 23, 5)
    assert o.due()
    ctx.db.meta_set("opt_day", "2026-11-03")
    assert not o.due()
    o.set_on(False)
    ctx.db.meta_set("opt_day", "")
    assert not o.due()


def test_securite_echelle_plancher_et_pertes_comprises():
    vs = optim.variants(P, "securite")
    names = [v.name for v in vs]
    assert names[0] == "Multiplicateur de lot 1,15 ×" and "Ordres en attente max 5" in names
    assert any(v.changes.keys() == {"lotmult", "maxpend"} for v in vs) and len(vs) <= 19
    assert all(float(v.changes["lotmult"][1]) >= 1.0 for v in vs if "lotmult" in v.changes)
    d_to = 1_765_000_000
    # compte perdu il y a 50 jours (hors des 30 derniers) : réglages dangereux, gain positif sur 30 j
    old_loss = [{"sc": "miroir", "nom": "miroir", "res": "liquide", "profit": -100, "dd": 99, "fin": d_to - 50 * 86400, "entree_s": 5}]
    # p1 sûre mais ne garde que 60 % du gain → refusée ; p2 sûre et 90 % → prise
    d = optim.decide(_rows(d_to, {"miroir": 1.0, "p1": 0.6, "p2": 0.9}) + old_loss, vs, d_to)
    assert d.change is not None and d.change.key == "p2" and "90 % du gain gardé" in d.reason and "pertes comprises" in d.reason
    d = optim.decide(_rows(d_to, {"miroir": 1.0, "p1": 0.6}) + old_loss, vs, d_to)
    assert d.change is None and "trop prudent" in d.note


@pytest.mark.asyncio
async def test_nuit_de_securite_apres_une_nuit_dangereuse(svc):  # noqa: F811
    ctx, sender, dash, router, *_ = svc
    o = router.optim
    ctx.db.meta_set("opt_safety", "1")
    assert o.safety_next and "Sécurité" in o.status_text()
