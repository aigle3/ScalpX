"""Backtest piloté depuis Telegram (EA 7.2.0) : fichiers du testeur, suivi, résultats, écrans et rapport quotidien."""
import json
import time
from pathlib import Path

import pytest

from bot import render
from bot.reports import build_report
from core import backtest as bt, labo
from mt5.backtester import BacktestManager, Run
from storage.db import DEMARRE, Instance
from tests.conftest import FakeProcs
from tests.test_bot import _until, svc, write_statut  # noqa: F401  (fixture)


def ev(typ, data=None, ts=None, texte=""):
    return {"type": typ, "ts": ts or 1_760_000_000, "texte": texte, "data": data or {}}


EVENTS = [
    ev("BT_DEBUT", {"solde": 125}),
    ev("OBJECTIF_AUTO", {"creneau": "02:00"}), ev("OBJECTIF_ATTEINT"),
    ev("OBJECTIF_AUTO", {"creneau": "04:00"}), ev("OBJECTIF_ANNULE"),
    ev("OBJECTIF_AUTO", {"creneau": "02:00"}), ev("NOTIF", texte="🚨 STOP LOSS D'URGENCE DÉCLENCHÉ"),
    ev("BT_JOUR", {"solde": 125, "equity": 125}, 1_760_000_000), ev("BT_JOUR", {"solde": 140, "equity": 139}, 1_760_086_400),
    ev("LABO_SERIE", {"sc": "miroir", "nom": "Réglages actuels", "res": "atteint", "profit": 0.6, "entree_s": 30}, 1_760_000_100),
    ev("LABO_REEL", {"atteint": True, "entree_s": 20, "filtres": {}}, 1_760_000_200),
]


# ------------------------------------------------------------------ fichiers et calculs
def test_dates_periodes_et_saisie():
    now = time.mktime((2026, 11, 2, 15, 0, 0, 0, 0, -1))
    assert bt.parse_date("01/11/2026", now) == 1 and bt.parse_date("02-11-2025", now) == 365
    assert bt.parse_date("02/11/2026", now) is None and bt.parse_date("01/11/2025", now) is None and bt.parse_date("abc", now) is None
    a, b = bt.date_range(30, now)
    assert b - a == 30 * 86400 and bt.mt5_date(b) == "2026.11.02" and bt.period_label(274) == "9 mois"


def test_ini_du_testeur_et_set():
    ini = bt.tester_ini(login=123, server="Broker-Demo", password="pw", ea_name="ScalpX_v5", symbol="XAUUSD", period="M5",
                        model=4, d_from=1_760_000_000, d_to=1_762_592_000, deposit=125, currency="EUR", leverage=500, report="reports\\bt_3")
    for k in ("Login=123", "Password=pw", "KeepPrivate=1", "AllowLiveTrading=0", "Model=4", "FromDate=2025.10.09",
              "Leverage=1:500", "ShutdownTerminal=1", f"ExpertParameters={bt.TESTER_SET}"):
        assert k in ini
    assert "Password" not in bt.tester_ini(login=1, server="S", ea_name="E", symbol="X", period="M5", model=1, d_from=0, d_to=86400,
                                           deposit=1, currency="EUR", leverage=100, report="r")
    with pytest.raises(ValueError):
        bt.tester_ini(login=1, server="S\nPassword=x", ea_name="E", symbol="X", period="M5", model=1, d_from=0, d_to=1, deposit=1,
                      currency="EUR", leverage=1, report="r")
    base = b"\xff\xfe" + "LotSize=0.01\r\nBacktestId=\r\n".encode("utf-16-le")
    out = bt.set_inputs(base, {"BacktestId": "7", "BtSlippagePts": "2.50"})
    txt = out[2:].decode("utf-16-le")
    assert out[:2] == b"\xff\xfe" and "LotSize=0.01" in txt and "BacktestId=7" in txt and "BtSlippagePts=2.50" in txt
    assert bt.params_cfg({"f_vol": "0", "lot": "0.01", "x": ""}) == "f_vol=0\nlot=0.01\n"


def test_resume_des_evenements_et_journal_mt5(tmp_path):
    fin = {"depart": 125, "solde": 180, "retraits": 375, "recharges": 0, "cycles": 1, "dd_max": 12.5, "note": "test"}
    r = bt.summarize(5, 0, 86400 * 3, fin, EVENTS)
    assert (r.goals, r.won, r.cancelled, r.sl) == (3, 1, 1, 1) and r.net == pytest.approx(430)
    assert r.slots == {"02:00": [2, 1], "04:00": [1, 0]} and len(r.days) == 2
    back = bt.Result.from_json(r.to_json())
    assert back.won == 1 and back.slots["02:00"] == [2, 1]
    assert [k for _, k, _, _ in bt.labo_rows(EVENTS)] == ["SERIE", "REEL"]
    log = tmp_path / "Tester" / "logs"
    log.mkdir(parents=True)
    (log / "20261102.log").write_bytes(b"\xff\xfe" + "KN\t0\t17:42:01.123\tTester\tXAUUSD,M5: history synchronization started\r\n".encode("utf-16-le"))
    assert bt.tail_log(tmp_path) == "Tester : XAUUSD,M5: history synchronization started"
    txt = render.bt_result_text(5, "bt", "compte Alpha", "reel", r, bt.scenario_table(EVENTS), "EUR", 600)
    assert "Gain net +430.00" in txt and "02:00 1/2" in txt and "Réglages actuels" in txt


# ------------------------------------------------------------------ terminal de backtest (testeur simulé)
class TesterProcs(FakeProcs):
    __test__ = False
    """Le testeur : au lancement, l'« EA » écrit l'avancement, ses événements et son résultat, puis le terminal se ferme."""

    def __init__(self, common: Path, ok: bool = True, log_line: str = "") -> None:
        super().__init__()
        self.common, self.ok, self.log_line = common, ok, log_line
        self.checks = 0
        self.inis: list[str] = []

    def launch(self, exe, ini=None):
        self.inis.append(Path(ini).read_text(encoding="utf-8") if ini else "")
        rid = [l for l in Path(exe).parent.joinpath("MQL5/Profiles/Tester", bt.TESTER_SET).read_bytes()[2:]
               .decode("utf-16-le").split("\r\n") if l.startswith("BacktestId=")][0].split("=")[1]
        if self.log_line:
            d = Path(exe).parent / "Tester" / "logs"
            d.mkdir(parents=True, exist_ok=True)
            (d / "20261102.log").write_text(self.log_line + "\n", encoding="utf-8")
        if self.ok:
            f = bt.bt_files(self.common, int(rid))
            f["progress"].write_text(json.dumps({"sim": 1_760_043_200, "solde": 130, "objectifs": 2}), encoding="utf-8")
            f["events"].write_text("\n".join(json.dumps(e) for e in EVENTS), encoding="utf-8")
            f["fin"].write_text(json.dumps({"depart": 125, "solde": 180, "retraits": 0, "fin": True}), encoding="utf-8")
        self.checks = 0
        return super().launch(exe, None)

    def is_running(self, exe):
        if "backtest" in str(exe):
            self.checks += 1
            return self.checks < 3 and super().is_running(exe)
        return super().is_running(exe)


def _mgr(cfg, db, procs):
    m = BacktestManager(cfg, db, procs)
    m.folder.mkdir(parents=True, exist_ok=True)
    (m.folder / cfg.mt5.terminal_exe).write_bytes(b"MZ")
    return m


@pytest.mark.asyncio
async def test_configuration_du_compte_et_backtest_de_bout_en_bout(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    tp = TesterProcs(cfg.paths.common())
    m = _mgr(cfg, db, tp)
    assert not m.configured()
    seen = []

    async def notify(run):
        seen.append(run.stage)

    run = await m.setup(123, "Broker-Demo", "s3cret", notify=notify, poll_s=0.01)
    assert run.ok and m.configured() and m.account()["login"] == 123
    assert "Password=s3cret" in tp.inis[-1] and not (m.folder / bt.TESTER_INI).exists()    # .ini avec mot de passe effacé
    assert "s3cret" not in db.meta_get("bt_account")
    assert db.labo_since(0) == []                                     # la vérification n'alimente pas le labo
    rid = db.bt_create("bt", 1_760_000_000, 1_760_259_200, "reel", "compte Alpha")
    run = await m.execute(Run(rid, "bt", 1_760_000_000, 1_760_259_200, "reel", "compte Alpha"), params={"f_vol": "0"},
                          deposit=125, currency="EUR", leverage=500, comm=3.5, slip=2.0, notify=notify, poll_s=0.01)
    assert run.ok and run.result.won == 1 and run.progress["objectifs"] == 2
    assert "Password" not in tp.inis[-1] and "Model=4" in tp.inis[-1]
    st = (m.folder / "MQL5/Profiles/Tester" / bt.TESTER_SET).read_bytes()[2:].decode("utf-16-le")
    assert "BtCommissionLot=3.5000" in st and "UseSavedParams=false" in st
    assert {r["kind"] for r in db.labo_since(0, f"bt_{rid}")} == {"SERIE", "REEL"}
    assert not bt.bt_files(cfg.paths.common(), rid)["events"].exists()     # fichiers d'échange nettoyés
    assert any("Test en cours" in s for s in seen) and any("Analyse" in s for s in seen)


@pytest.mark.asyncio
async def test_identifiants_refuses_et_test_sans_resultat(stack):
    cfg, db, *_ = stack
    tp = TesterProcs(cfg.paths.common(), ok=False, log_line="KN\t0\t10:00\tNetwork\t'123': authorization on Broker-Demo failed (Invalid account)")
    m = _mgr(cfg, db, tp)
    run = await m.setup(123, "Broker-Demo", "bad", poll_s=0.01)
    assert not run.ok and "refusés" in run.error and not m.configured()
    tp.log_line = "KN\t0\t10:00\tTester\tsomething went wrong"
    db.meta_set("bt_account", json.dumps({"login": 1, "server": "S"}))
    run = await m.execute(Run(9, "bt", 0, 86400, "m1", "x"), params={}, deposit=125, currency="EUR", leverage=100, poll_s=0.01)
    assert not run.ok and "sans résultat" in run.error and "something went wrong" in run.error


# ------------------------------------------------------------------ écrans Telegram
@pytest.mark.asyncio
async def test_ecran_backtest_configuration_lancement_et_labo_complete(svc):  # noqa: F811
    ctx, sender, dash, router, notifier, ea, conf = svc
    tp = TesterProcs(ctx.cfg.paths.common())
    router.bt.procs = tp
    ctx.cfg.backtest.poll_s = 0.01
    folder = router.bt.folder
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ctx.cfg.mt5.terminal_exe).write_bytes(b"MZ")
    await router.on_callback("d:rpt")
    await router.on_callback("d:bt")
    assert "à configurer" in dash.render()[0]
    # assistant : login → serveur (bouton) → mot de passe (texte effacé par l'app)
    ctx.cfg.mt5.servers = ["Broker-Demo"]
    await router.on_callback("d:btc")
    await router.on_text("abc")
    assert "invalide" in dash.render()[0]
    await router.on_text("123456")
    assert any(d == "d:btsv:0" for row in dash.render()[1] for _, d in row)
    await router.on_callback("d:btsv:0")
    assert "mot de passe" in dash.render()[0]
    await router.on_text("pw")
    assert await _until(lambda: router.bt.configured(), 200)
    assert any("Terminal de backtest prêt" in s[1] for s in sender.sent)
    # réglages : période, date libre, modèle, source = preset
    await router.on_callback("d:bt:182")
    await router.on_callback("d:btd")
    await router.on_text("32/13/2026")
    assert dash.bt_days == 182
    await router.on_callback("d:btm")
    assert dash.bt_model == "m1"
    dash.store.put("conseille", {"f_vol": "0", "lot": "0.01"})
    while not dash.bt_src.startswith("preset:"):
        await router.on_callback("d:bts")
    assert "preset conseille" in dash.render()[0]
    await router.on_callback("d:btgo")
    assert await _until(lambda: any("Backtest #" in s[1] and "terminé" in s[1] for s in sender.sent), 200)
    row = ctx.db.bt_list(1)[0]
    assert row["status"] == "ok" and row["source"] == "preset conseille" and row["model"] == "m1"
    assert bt.bt_files(ctx.cfg.paths.common(), row["id"])["params"].exists() is False
    await router.on_callback("d:btl")
    assert f"#{row['id']}" in dash.render()[0]
    await router.on_callback(f"d:btr:{row['id']}")
    assert "Gain net" in dash.render()[0]
    # labo sur 30 j : complété par le backtest (période antérieure aux mesures en direct)
    ctx.db.add_instance(Instance("7_b", 7, "b", "Bravo", str(ctx.cfg.paths.instances / "7_b"), 1, "7.2.0", DEMARRE))
    now = int(time.time())
    ctx.db.bt_update(row["id"], "ok", ctx.db.bt_get(row["id"])["result"])
    with ctx.db._lock:
        ctx.db._cx.execute("UPDATE backtests SET d_from=?, d_to=? WHERE id=?", (now - 30 * 86400, now - 86400, row["id"]))
        ctx.db._cx.execute("UPDATE labo SET ts=? WHERE iid=?", (now - 10 * 86400, f"bt_{row['id']}"))
        ctx.db._cx.commit()
    ctx.db.add_labo("7_b", now - 3600, "SERIE", "miroir", json.dumps({"sc": "miroir", "res": "atteint", "profit": 1.0}))
    await router.on_callback("d:lab:30")
    text, kb = dash.render()
    assert f"backtest #{row['id']}" in text and "2 obj" in text            # 1 objectif virtuel en direct + 1 du backtest
    assert "Labo en direct" in text


# ------------------------------------------------------------------ rapport quotidien
def test_rapport_quotidien_essentiel(svc):  # noqa: F811
    ctx, sender, dash, *_ = svc
    from tests.test_bot import ctx_stack
    from tests.test_mt5 import seed
    a = seed(ctx_stack(svc), 5, "Demo-Server 1", running=False)
    write_statut(ctx, a.id, solde=140.0, devise="EUR")
    now = int(time.time())
    ctx.db.add_balance(a.id, 120.0, 120.0, 0, 0, "ACTIF") if hasattr(ctx.db, "add_balance") else None
    for typ, text in [("OBJECTIF_AUTO", ""), ("OBJECTIF_ATTEINT", ""), ("OBJECTIF_AUTO", ""), ("OBJECTIF_ANNULE", ""),
                      ("CASHFLOW", "💰 DÉPÔT +10.00 EUR")]:
        ctx.db.add_event(a.id, now - 60, typ, 1, text)
    rep = build_report(ctx, now - 86400, title="📝 Rapport quotidien")
    assert "Capital : 140.00 EUR" in rep and "1 atteints sur 2" in rep and "1 manqués" in rep
    assert "Par compte" in rep and "🎯 1/2" in rep and "Performance par compte" not in rep


@pytest.mark.asyncio
async def test_tests_rapides_un_bouton_scenarios_et_backtest(svc):  # noqa: F811
    ctx, sender, dash, router, notifier, ea, conf = svc
    tp = TesterProcs(ctx.cfg.paths.common())
    router.bt.procs = tp
    ctx.cfg.backtest.poll_s = 0.01
    folder = router.bt.folder
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ctx.cfg.mt5.terminal_exe).write_bytes(b"MZ")
    await router.on_callback("d:qt")
    assert "configure" in dash.render()[0].lower()
    ctx.db.meta_set("bt_account", json.dumps({"login": 1, "server": "S"}))
    await router.on_callback("d:qtd:7")
    text, kb = dash.render()
    assert "Bougies après le croisement" in text and any(d == "d:qtgo:bougies" for row in kb for _, d in row)
    dash.bt_src = "base"
    await router.on_callback("d:qtgo:bougies")
    cfg = labo.load_config(ctx.cfg.paths.common())
    assert [c.opts.get("b") for c in cfg.customs] == ["6", "12", "24", "6", "12", "24", "36"]
    assert all(c.toks[labo.CODES.index("fresh")] == "on" for c in cfg.customs)
    line = (ctx.cfg.paths.common() / labo.SCEN_FILE).read_text(encoding="utf-8")
    assert "b=6;" not in line and "|n=3;b=6" in line or "|b=6" in line
    assert await _until(lambda: any("Backtest #" in s[1] and "terminé" in s[1] for s in sender.sent), 200)
    row = ctx.db.bt_list(1)[0]
    assert row["source"].startswith("⏱ Bougies après le croisement") and row["d_to"] - row["d_from"] == 7 * 86400
    await router.on_callback("d:qtgo:filtres")                       # thème sans scénario perso : seulement les 13 automatiques
    assert labo.load_config(ctx.cfg.paths.common()).customs == []
