"""🤖 Optimiseur autonome : 4 catégories, variantes, témoin, décision (sécurité 60 j, choix 30 j, plateau), créneaux,
portée élargie après 3 passages, nuit complète, annulation, écran."""
import json
import time
from pathlib import Path

import pytest

from core import labo, optim
from tests.test_backtest import TesterProcs
from tests.test_bot import _confirm, _until, ctx_stack, svc  # noqa: F401  (fixture)
from tests.test_mt5 import seed

# créneaux 02:00, 04:00, 06:00, 11:00, 20:00 ; coupures 13:45 et 21:00
SL00 = sum(1 << h for h in (2, 4, 6, 11, 20))
P = {"gridatr": "0.7", "gridmin": "200", "gridmax": "500", "dyngrid": "1", "maxpend": "8", "lotmult": "1.2", "tbm": "2",
     "fast": "10", "slow": "30", "atrp": "14", "dyntp": "1", "atrmult": "1.0", "tpmin": "100", "tpmax": "800",
     "f_atr": "1", "atrmin": "100", "atrmax": "1000", "f_vol": "0", "volmin": "0.5", "volmax": "3", "f_ext": "1", "extatr": "2",
     "f_can": "0", "canbody": "0.3", "f_fresh": "1", "freshbars": "12", "news_block": "1", "sl_pct": "0.5",
     "sl_on": "1", "sl_m00": str(SL00), "sl_m15": "0", "sl_m30": "0", "sl_m45": "0",
     "ct_on": "1", "ct_m00": str(1 << 21), "ct_m15": "0", "ct_m30": "0", "ct_m45": str(1 << 13)}


def test_cinq_categories_et_variantes():
    assert [g[0] for g in optim.GROUPS] == ["grille_lots", "signal_tp", "filtres", "objectif"]
    vs = optim.variants(P, "grille_lots")
    assert [v.opts for v in vs[:6]] == [{"ga": "0.6"}, {"ga": "0.8"}, {"gn": "150"}, {"gn": "250"}, {"gx": "400"}, {"gx": "600"}]
    assert len(vs) == 36 and [v.key for v in vs] == [f"p{i}" for i in range(1, 37)]       # 18 grille + 18 lots
    assert {"tb": "1"} in [v.opts for v in vs] and {"mp": "9", "tb": "3"} in [v.opts for v in vs]
    assert not any(set(v.changes) == {"gridatr", "maxpend"} for v in vs)                   # pas de combinaison entre familles
    assert all(v.ref == "miroir" and v.kind == "num" for v in vs)
    combo = vs[6]
    assert combo.changes == {"gridatr": ("0.7", "0.6"), "gridmin": ("200", "150")} and combo.name == "ATR grille 0,6 × ATR + Grille min 150 pts"
    f = optim.scenario_file(vs)
    assert f.startswith("# ") and "auto=0" in f and "p1|Multiplicateur ATR de la grille 0,6 × ATR|" in f and "|ga=0.6;gn=150" in f
    assert len(optim.variants({**P, "dyngrid": "0"}, "grille_lots")) == 18                 # grille fixe : seulement les lots
    # signal et TP : témoin en tête, variantes du signal recalculées entièrement (r=1), comparées au témoin
    st = optim.variants(P, "signal_tp")
    assert st[0].key == optim.TEMOIN and st[0].kind == "temoin" and st[0].opts == {"r": "1"}
    assert len(optim.tested(st)) == 36 and len(st) == 37
    sig = [v for v in st if "fast" in v.changes]
    assert sig and all(v.opts.get("r") == "1" and v.ref == optim.TEMOIN for v in sig)
    tp = [v for v in st if set(v.changes) == {"atrmult"}]
    assert [v.opts for v in tp] == [{"am": "0.9"}, {"am": "1.1"}] and all(v.ref == "miroir" for v in tp)
    assert all(float(v.changes.get("fast", ("", "10"))[1]) < float(v.changes.get("slow", ("", "30"))[1]) for v in st)
    near = optim.variants({**P, "fast": "28", "slow": "30"}, "signal_tp")
    assert not any(v.changes.get("fast", ("", ""))[1] == "29" and v.changes.get("slow", ("", ""))[1] == "28" for v in near)
    assert not any("atrmult" in v.changes for v in optim.variants({**P, "dyntp": "0"}, "signal_tp"))   # TP fixe
    # filtres : chacun inversé + seuils des filtres actifs seulement (volume et bougie désactivés : pas de seuil testé)
    flt = optim.variants(P, "filtres")
    toggles = [v for v in flt if v.kind == "flt"]
    assert len(toggles) == 6 and toggles[0].toks[labo.CODES.index("atr")] == "~" and toggles[0].new == "0"
    keys = {k for v in flt for k in v.changes}
    assert {"atrmin", "atrmax", "extatr", "freshbars"} <= keys and not keys & {"volmin", "volmax", "canbody"}
    assert flt[0].key == optim.TEMOIN and {"an": "90", "r": "1"} in [v.opts for v in flt]
    # objectif et créneaux : ±1 cran + chaque heure pile libre testée seule (pas les heures déjà prises, pas 13 h ni 21 h)
    ob = optim.variants(P, "objectif")
    hours = [v.hour for v in ob if v.kind == "slot"]
    assert [v.opts for v in ob if v.kind == "num"] == [{"o": "0.4"}, {"o": "0.6"}]
    assert hours == [h for h in range(24) if h not in (2, 4, 6, 11, 20, 13, 21)]
    assert next(v for v in ob if v.hour == 3).opts == {"sm": f"{1 << 3},0,0,0"}
    assert optim.slot_candidates({**P, "sl_on": "0"}) == []
    assert optim.label_of("sl_pct", "0.5") == "Objectif : 0,5 %" and optim.value_text("f_vol", "1") == "activé"
    assert optim.value_text("sl_m00", str(SL00)) == "02:00, 04:00, 06:00, 11:00, 20:00"


def _rows(d_to, profits: dict, lost: tuple = (), n=20, dd=30, slot="02:00"):
    rows = []
    for k in range(n):
        t = d_to - (25 - k) * 86400 // 2                    # tous dans les 30 derniers jours
        for sc, pr in profits.items():
            res = "liquide" if (sc, k) in lost else "atteint"
            rows.append({"sc": sc, "nom": sc, "res": res, "profit": -100 if res == "liquide" else pr, "dd": 95 if res == "liquide" else dd,
                         "fin": t, "entree_s": 10, "creneau": slot})
    return rows


def test_decision_securite_gain_et_temoin():
    d_to = 1_765_000_000
    vs = optim.variants(P, "grille_lots")
    # p1 rapporte plus mais perd le compte une fois ; p7 (combinaison) rapporte plus et reste sûre → p7 appliquée entière
    d = optim.decide(_rows(d_to, {"miroir": 0.5, "p1": 2.0, "p7": 1.0}, lost=(("p1", 4),)), vs, d_to)
    assert d.change is not None and d.change.key == "p7" and len(d.change.changes) == 2 and "aucun compte perdu" in d.reason
    d = optim.decide(_rows(d_to, {"miroir": 0.5, "p3": 0.51}), vs, d_to)
    assert d.change is None and "rien n'est changé" in d.note
    d = optim.decide(_rows(d_to, {"miroir": 0.6, "p4": 0.5}, lost=(("miroir", 2),)), vs, d_to)
    assert d.change is not None and d.change.key == "p4" and d.ref_unsafe and "perdent le compte" in d.reason
    assert "pas assez" in optim.decide(_rows(d_to, {"miroir": 0.5}, n=3), vs, d_to).note
    # signal : comparé au témoin (calcul propre), pas aux réglages actuels
    st = optim.variants(P, "signal_tp")
    k = next(v.key for v in st if v.changes == {"fast": ("10", "9")})
    d = optim.decide(_rows(d_to, {"miroir": 1.5, "pt": 0.5, k: 1.0}), st, d_to)
    assert d.change is not None and d.change.key == k and "recalculés de la même façon" in d.reason
    assert [r.name for r in d.table[:2]] == ["Réglages actuels", "Réglages actuels (calcul propre)"]


def test_plateau_valeur_entouree_de_bons_resultats():
    d_to = 1_765_000_000
    vs = optim.variants(P, "grille_lots")
    by = {tuple(sorted(v.changes.items())): v.key for v in vs}
    lone = by[(("gridatr", ("0.7", "0.6")),)]             # à peine meilleure, seule
    solid = by[(("gridmin", ("200", "250")),)]            # presque aussi bonne, et ses combinaisons gagnent aussi
    combos = [v.key for v in vs if v.changes.get("gridmin") == ("200", "250") and len(v.changes) == 2]
    profits = {"miroir": 0.5, lone: 1.02, solid: 1.0, **{c: 0.6 for c in combos}}
    d = optim.decide(_rows(d_to, profits), vs, d_to)
    assert d.change is not None and d.change.key == solid and d.plateau and "plus robuste" in d.reason
    # écart net : la meilleure l'emporte
    d = optim.decide(_rows(d_to, {**profits, lone: 2.0}), vs, d_to)
    assert d.change.key == lone and not d.plateau


def test_creneaux_retires_au_dela_de_80_pourcent_et_ajoutes_si_surs():
    d_to = 1_765_000_000
    vs = optim.variants(P, "objectif")
    k3 = next(v.key for v in vs if v.hour == 3)
    k5 = next(v.key for v in vs if v.hour == 5)
    k7 = next(v.key for v in vs if v.hour == 7)
    rows = (_rows(d_to, {"miroir": 0.5}, slot="02:00") + _rows(d_to, {"miroir": 0.5}, slot="04:00", dd=85)
            + _rows(d_to, {k3: 0.4}, dd=40) + _rows(d_to, {k5: 0.4}, dd=82) + _rows(d_to, {k7: -0.1}, dd=10))
    d = optim.decide(rows, vs, d_to, P)
    sp = d.slots
    assert sp.removed == [4 * 60] and sp.added == [3 * 60]          # 04:00 dépasse 80 % ; 03:00 sûr et gagnant ; 05:00 trop risqué ; 07:00 perd
    assert sp.changes == {"sl_m00": (str(SL00), str(SL00 - (1 << 4) + (1 << 3)))}
    assert any("04:00 retiré" in ln for ln in sp.lines) and any("03:00 ajouté" in ln for ln in sp.lines)
    # jamais zéro créneau : le moins risqué reste
    one = {**P, "sl_m00": str((1 << 2) | (1 << 4))}
    vs1 = optim.variants(one, "objectif")
    rows = _rows(d_to, {"miroir": 0.5}, slot="02:00", dd=95) + _rows(d_to, {"miroir": 0.5}, slot="04:00", dd=85)
    sp = optim.decide(rows, vs1, d_to, one).slots
    assert sp.removed == [2 * 60] and sp.kept_last == 4 * 60 and sp.changes == {"sl_m00": (one["sl_m00"], str(1 << 4))}
    assert optim.ea_too_old([{"sc": k3, "opts": "o=0.5", "res": "atteint"}], vs) and not optim.ea_too_old(rows, vs)


def test_portee_elargie_apres_trois_passages_sans_changement():
    keys = ["gridatr", "gridmin"]
    sc, idle = {}, {}
    for _ in range(2):
        sc, idle = optim.next_scale(sc, idle, keys, set())
    assert sc == {} and idle == {"gridatr": 2, "gridmin": 2}
    sc, idle = optim.next_scale(sc, idle, keys, set())
    assert sc == {"gridatr": 2, "gridmin": 2} and idle == {"gridatr": 0, "gridmin": 0}
    for _ in range(3):
        sc, idle = optim.next_scale(sc, idle, keys, {"gridmin"})
    assert sc == {"gridatr": optim.MAX_SCALE, "gridmin": 1}                 # 1 → 2 → 4 crans ; changé : retour à 1 cran
    for _ in range(3):
        sc, idle = optim.next_scale(sc, idle, keys, set())
    assert sc["gridatr"] == 1                                               # rien même à 4 crans : retour à 1 cran
    wide = optim.variants(P, "grille_lots", {"gridmin": 2})
    assert {"gn": "100"} in [v.opts for v in wide] and {"gn": "300"} in [v.opts for v in wide]   # ±100 pts au lieu de ±50


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
    ctx.db.meta_set("opt_grp", "2")                          # catégorie 🛡️ Filtres (réglages connus du faux EA)
    ea.start()
    try:
        from bot import presets_ui as pui_
        dash.store.put("Martin", await pui_.read_raw(ctx, a))   # Bot 3.5.0 : le preset ⭐ fait foi
        dash.store.set_active("Martin")
        await router.on_callback("d:rpt")
        await router.on_callback("d:opt")
        text, kb = dash.render()
        assert "Optimiseur automatique" in text and "21:00" in text and "Filtres" in text and "Grille et lots → 📐 Signal et TP" in text
        assert any(d == "d:btc" for row in kb for _, d in row)
        await router.on_callback("d:opth")
        assert router.optim.hour == 22
        await router.optim.run(manual=True)
        msg = [s for s in sender.sent if "Optimiseur · " in s[1]][-1]
        assert "Changé" in msg[1] and "Filtre" in msg[1] and "→" in msg[1], msg[1]
        oid = ctx.db.opt_list(1)[0]["id"]
        applied = json.loads(ctx.db.opt_get(oid)["applied"])
        key, (old, new) = next(iter(applied.items()))
        assert ea.accounts[a.id]["params"][key] == ("✅" if new == "1" else "❌")
        assert any(d == f"d:opu:{oid}" for row in msg[2] for _, d in row)
        assert int(ctx.db.meta_get("opt_grp")) == 3                                    # catégorie suivante
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
        assert "3 passages sans changement" in sender.sent[n + 1][1]
    finally:
        await ea.stop()


def test_heure_de_lancement(svc):  # noqa: F811
    ctx, sender, dash, router, *_ = svc
    o = router.optim
    assert o.hour == 21
    from datetime import datetime
    o._now = lambda: datetime(2026, 11, 3, 21, 5)
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


def test_securite_au_plus_une_fois_par_tour():
    """Logique du compteur : après une nuit de sécurité, une nuit normale par catégorie avant la suivante."""
    import types
    from bot.optim import Optimizer
    meta = {}
    db = types.SimpleNamespace(meta_get=lambda k, d="": meta.get(k, d), meta_set=lambda k, v: meta.__setitem__(k, v))
    o = Optimizer.__new__(Optimizer)
    o.ctx = types.SimpleNamespace(db=db)
    nights = []
    o.schedule_safety(lost=True, was_safety=False)
    nights.append(o.safety_next)
    for _ in range(len(optim.GROUPS) + 1):
        o.schedule_safety(lost=True, was_safety=o.safety_next)
        nights.append(o.safety_next)
    assert nights == [True] + [False] * len(optim.GROUPS) + [True]
    meta.clear()
    o.schedule_safety(lost=False, was_safety=False)
    assert not o.safety_next



def test_mode_continu_hors_optimisation():
    """Bot 3.2.1 : aucune nuit ne touche au mode continu."""
    assert all("sl_cont" not in k for g in optim.GROUPS for ks in g[2] for k in ks)
    for g in optim.GROUPS:
        assert all("sl_cont" not in v.changes for v in optim.variants({**P, "sl_cont": "1"}, g[0]))

# ---- Bot 3.2.0 : contrôle des réglages actuels par le robot réel du testeur (sans rejeu depuis 3.2.2)
def test_real_check():
    from core.backtest import Result
    ok, why = optim.real_check(Result(1, 0, 0, deposit=200, final=260, dd_max=40, stopouts=0))
    assert ok and "aucun stop-out" in why and "+60.00" in why
    ok, why = optim.real_check(Result(1, 0, 0, dd_max=95, stopouts=2, reloads=1))
    assert not ok and "2 stop-out(s)" in why and "compte perdu 1 fois" in why and "drawdown 95 %" in why
    assert optim.real_check(None)[0] is False


def _opt_env(tmp_path, monkeypatch, results):
    """Optimiseur branché sur une vraie base, testeur simulé : `results` = Result rendus par backtest successif."""
    import asyncio as aio
    from types import SimpleNamespace as NS
    from storage.db import Database
    from bot import optim as bo
    from core.backtest import Result
    db = Database(tmp_path / "o.db")
    sent, calls, sets = [], [], []
    inst = NS(id="A", label="A")

    class BT:
        def configured(self): return True
        async def execute(self, run, *, params, **kw):
            calls.append(dict(params))
            run.result = results[len(calls) - 1]
            run.ok = True
            return run

    class Sender:
        async def send(self, chat, text, kb, silent): sent.append(text)

    async def bt_inputs(src):
        assert src == "preset:Martin"                         # Bot 3.5.0 : jamais un compte
        return (dict(store.presets["Martin"]), "Martin", 200.0, "EUR", 500, 0.0, 0.0)
    async def flash_refresh(**kw): return None
    from core.presets import PresetStore
    store = PresetStore(tmp_path / "p0.json")
    store.put("Martin", dict(P))
    store.set_active("Martin")
    r = NS(bt=BT(), sender=Sender(), chat_id=1, _bt_inputs=bt_inputs,
           dash=NS(flash=lambda *a: None, refresh=flash_refresh, store=store), sync_base_set=lambda: None)
    paths = NS(data=tmp_path, common=lambda: tmp_path)
    r.ctx = NS(db=db, cfg=NS(paths=paths, backtest=NS(poll_s=1), reports=NS(timezone="Europe/Paris")), deploy=NS(running=False),
               mgr=NS(is_running=lambda i: True))
    monkeypatch.setattr(db, "list", lambda *a, **k: [inst])

    async def read_raw(ctx, i): return dict(P)
    async def send_cmd(ctx, i, cmd, args):
        sets.append(tuple(args))
        return NS(ok=True)
    monkeypatch.setattr(bo.pui, "read_raw", read_raw)
    monkeypatch.setattr(bo.act, "send_cmd", send_cmd)
    monkeypatch.setattr(bo.labo, "rows_of", lambda *a: [])
    o = bo.Optimizer(r)
    o.started = __import__("time").time()
    return o, db, sent, calls, sets, aio



def test_testeur_sans_trading_reel(tmp_path, monkeypatch):
    """Bot 3.2.3 : un seul backtest, robot du testeur sans trading (bt_labo_seul), décision sur les séries virtuelles."""
    from core.backtest import Result
    v = optim.Variant("v1", "Grille 0,9 × ATR", {"gridatr": ("0.7", "0.9")})
    dec = optim.Decision(change=v, reason="meilleur", table=[])
    monkeypatch.setattr(optim, "decide", lambda *a, **k: dec)
    o, db, sent, calls, sets, aio = _opt_env(tmp_path, monkeypatch, [Result(1, 0, 0, deposit=200, final=200)])
    aio.run(o._run(True))
    assert len(calls) == 1 and calls[0]["bt_labo_seul"] == "1" and ("gridatr", "0.9") in sets
    assert "Réglages actuels" not in sent[-1]


def test_reinitialisation(tmp_path, monkeypatch):
    """Bot 3.3.0 : ♻️ cycle à zéro + preset de base = valeurs d'avant la 1re optimisation (créé une fois, réutilisé)."""
    import json
    from core.presets import PresetStore
    o, db, sent, calls, sets, aio = _opt_env(tmp_path, monkeypatch, [])
    store = PresetStore(tmp_path / "presets.json")
    store.put("mon", {"gridatr": "0.9", "freshbars": "18", "f_atr": "0", "sl_cont": "1"})
    store.set_active("mon")
    o.r.dash.store = store
    o.r.sync_base_set = lambda: None
    # historique : f_atr 1→0, gridatr 0.7→0.8 puis 0.8→0.9, freshbars 30→18 (annulé puis refait : 1re valeur gardée)
    db.opt_add("filtres", "change", "", json.dumps({"f_atr": ["1", "0"]}), 1)
    db.opt_add("grille_lots", "change", "", json.dumps({"gridatr": ["0.7", "0.8"]}), 2)
    db.opt_add("grille_lots", "change", "", json.dumps({"gridatr": ["0.8", "0.9"]}), 3)
    db.opt_add("filtres", "change", "", json.dumps({"freshbars": ["30", "18"], "sl_cont": ["0", "1"]}), 4)
    db.meta_set("opt_grp", "3"); db.meta_set("opt_scale", '{"gridatr": 2}'); db.meta_set("opt_safety", "1")
    assert o.initial_values() == {"f_atr": "1", "gridatr": "0.7", "freshbars": "30"}

    async def read_raw(ctx, i): return {**P, "gridatr": "0.9", "freshbars": "18", "f_atr": "0", "sl_cont": "1"}
    from bot import optim as bo
    monkeypatch.setattr(bo.pui, "read_raw", read_raw)
    msg = aio.run(o.reset())
    assert set(sets) == {("f_atr", "1"), ("gridatr", "0.7"), ("freshbars", "30")}       # sl_cont jamais touché
    assert o.group_index() == 0 and o.scale() == {} and not o.safety_next
    assert store.presets[o.BASE_PRESET]["gridatr"] == "0.7" and store.presets["mon"]["gridatr"] == "0.7"
    assert store.active == "mon"
    assert "Optimiseur réinitialisé" in msg and "0,9 × ATR → 0,7 × ATR" in msg
    # 2e reset : preset de base réutilisé, l'historique du reset n'est pas une « optimisation »
    store.presets[o.BASE_PRESET]["gridatr"] = "0.6"
    assert "gridatr" in o.initial_values()
    sets.clear()
    aio.run(o.reset())
    assert ("gridatr", "0.6") in sets



def test_preset_de_travail_jamais_la_base(tmp_path, monkeypatch):
    """Bot 3.4.0 : l'optimiseur modifie et applique « Martin » (⭐), jamais le preset de base, même s'il était ⭐."""
    from core.presets import PresetStore
    o, db, sent, calls, sets, aio = _opt_env(tmp_path, monkeypatch, [])
    store = PresetStore(tmp_path / "presets.json")
    store.put("conseille", {"gridatr": "0.7"})
    store.put("Martin", {"gridatr": "0.8"})
    store.put(o.BASE_PRESET, {"gridatr": "0.7"})
    store.set_active(o.BASE_PRESET)
    o.r.dash.store = store
    synced = []
    o.r.sync_base_set = lambda: synced.append(store.active)
    aio.run(o._apply([], "gridatr", "1.0"))
    assert store.active == "Martin" and store.presets["Martin"]["gridatr"] == "1.0"
    assert store.presets[o.BASE_PRESET]["gridatr"] == "0.7" and synced == ["Martin"]
    # reset : Martin reprend la base, la base ne bouge pas, Martin reste ⭐
    store.set_active(o.BASE_PRESET)
    aio.run(o.reset())
    assert store.active == "Martin" and store.presets["Martin"]["gridatr"] == "0.7"
    assert store.presets[o.BASE_PRESET] == {"gridatr": "0.7"}



def test_depart_du_preset_martin(tmp_path, monkeypatch):
    """Bot 3.5.0 : l'optimiseur part de Martin ⭐ (pas d'un compte), crée la base (copie de Martin) au 1er lancement,
    puis modifie seulement Martin."""
    v = optim.Variant("v1", "Grille 0,9 × ATR", {"gridatr": ("0.7", "0.9")})
    seen = {}

    def decide(*a, **k):
        seen["params"] = a[0] if a else k.get("params")
        return optim.Decision(change=v, reason="meilleur", table=[])
    monkeypatch.setattr(optim, "decide", decide)
    from core.backtest import Result
    o, db, sent, calls, sets, aio = _opt_env(tmp_path, monkeypatch, [Result(1, 0, 0, deposit=200, final=200)])
    store = o.r.dash.store
    store.presets["Martin"]["gridatr"] = "0.7"
    from bot import optim as bo

    async def read_raw(ctx, i):
        raise AssertionError("ne doit plus lire les réglages d'un compte")
    monkeypatch.setattr(bo.pui, "read_raw", read_raw)
    aio.run(o._run(True))
    assert store.presets[o.BASE_PRESET]["gridatr"] == "0.7"              # base = copie initiale de Martin
    assert store.presets["Martin"]["gridatr"] == "0.9" and store.active == "Martin"
    assert ("gridatr", "0.9") in sets
