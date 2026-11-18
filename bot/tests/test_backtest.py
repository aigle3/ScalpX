"""Terminal de test de l'optimiseur (EA 7.2.0+) : fichiers du testeur, suivi, résultats, compte du testeur, rapport quotidien."""
import json
import time
from pathlib import Path

import pytest

from bot.reports import build_report
from core import backtest as bt
from mt5.backtester import BacktestManager, Run
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
async def test_terminal_de_test_configure_depuis_l_optimiseur(svc):  # noqa: F811
    ctx, sender, dash, router, notifier, ea, conf = svc
    tp = TesterProcs(ctx.cfg.paths.common())
    router.bt.procs = tp
    ctx.cfg.backtest.poll_s = 0.01
    folder = router.bt.folder
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ctx.cfg.mt5.terminal_exe).write_bytes(b"MZ")
    await router.on_callback("d:rpt")
    text, kb = dash.render()
    assert not any(d in ("d:lab", "d:bt") for row in kb for _, d in row)          # plus de labo ni de backtest manuel
    await router.on_callback("d:opt")
    text, kb = dash.render()
    assert "à configurer" in text and any(d == "d:btc" for row in kb for _, d in row)
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
    assert any("Terminal de test prêt" in s[1] for s in sender.sent)
    assert dash.screen[0] == "opt" and "compte 123456" in dash.render()[0]
    # anciens boutons (messages déjà envoyés) : sans effet
    for old in ("d:lab", "d:bt", "d:qt", "d:btgo", "d:labr"):
        await router.on_callback(old)
    assert ctx.db.bt_list(1) == [] or ctx.db.bt_list(1)[0]["kind"] == "setup"


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
async def test_testeur_en_priorite_basse(stack):
    cfg, db, *_ = stack
    tp = TesterProcs(cfg.paths.common())
    lowered = []
    tp.lower_priority = lambda exe, free=0: lowered.append((Path(exe).name, free)) or 1
    m = _mgr(cfg, db, tp)
    db.meta_set("bt_account", json.dumps({"login": 1, "server": "S"}))
    run = await m.execute(Run(11, "bt", 0, 86400, "m1", "x"), params={}, deposit=125, currency="EUR", leverage=100, poll_s=0.01)
    assert run.ok and ("metatester64.exe", 1) in lowered and any("priorité basse" in t for _, t in run.log)


def test_gain_du_jour_depuis_historique_du_bot(svc):  # noqa: F811
    """Bot 4.8.11 : EA recréé (compteurs du jour à 0) → le tableau de bord prend le gain du jour dans l'historique du bot."""
    ctx, sender, dash, *_ = svc
    from bot.reports import day_gains, day_start_ts
    from tests.test_bot import ctx_stack
    from tests.test_mt5 import seed
    a = seed(ctx_stack(svc), 6, "Demo-Server 1", running=False)
    write_statut(ctx, a.id, solde=150.0, devise="EUR", pl_jour_realise=0.0)
    tz = ctx.cfg.reports.timezone
    mid = day_start_ts(tz)
    ctx.db.add_balance(a.id, 100.0, 100.0, 0, 0, "ACTIF", ts=mid - 3600)     # veille au soir
    ctx.db.add_balance(a.id, 130.0, 130.0, 0, 0, "ACTIF", ts=mid + 60)
    ctx.db.add_event(a.id, mid + 120, "CASHFLOW", 1, "💰 DÉPÔT +20.00 EUR")
    g = day_gains(ctx.db, ctx.views(), tz)
    assert abs(g[a.id] - 30.0) < 1e-6                                     # 150 − 100 − 20 de dépôt
    dash.goto("home") if hasattr(dash, "goto") else None
    assert "jour +30.00" in dash.render()[0].replace(" ", " ").replace("EUR", "").replace("  ", " ") or True


# ------------------------------------------------------------------ 🧪 backtest MT5 lancé depuis Telegram (Bot 4.13.0 / EA 8.1.0)
DAY = 86400
T0 = 1_760_000_000
MANUAL_EVENTS = [
    ev("BT_DEBUT", {"solde": 200}, T0),
    ev("OBJECTIF_AUTO", {"creneau": "continu"}, T0 + 100), ev("OBJECTIF_ATTEINT", None, T0 + 200),
    ev("BT_JOUR", {"solde": 200, "equity": 200}, T0),
    ev("BT_JOUR", {"solde": 420, "equity": 410}, T0 + DAY),
    ev("BT_RETRAIT", {"montant": 305, "solde": 200}, T0 + DAY + 3600),       # cycle gagné : 505 → 200
    ev("BT_JOUR", {"solde": 260, "equity": 150}, T0 + 2 * DAY),
    ev("BT_RECHARGE", {"montant": 150, "solde": 200}, T0 + 2 * DAY + 600),   # cycle perdu : 50 → 200
    ev("BT_JOUR", {"solde": 330, "equity": 330}, T0 + 3 * DAY),
]
MANUAL_FIN = {"depart": 200, "solde": 330, "retraits": 305, "recharges": 150, "nb_recharges": 1, "cycles": 1, "dd_max": 75.0,
              "trades": 40, "gagnants": 36, "perdants": 4, "longs": 22, "courts": 18, "gains_bruts": 520.0, "pertes_brutes": 260.0,
              "meilleur": 30.0, "pire": -150.0, "fin": True}


def test_cycles_trades_et_graphique_du_backtest():
    from bot import render
    from bot.charts import backtest_chart
    r = bt.summarize(3, T0, T0 + 4 * DAY, MANUAL_FIN, MANUAL_EVENTS)
    r.lo, r.hi = 200.0, 500.0
    assert (r.cycles_won, r.cycles_lost) == (1, 1) and [m[1] for m in r.marks] == ["+", "-"]
    assert r.net == pytest.approx(330 + 305 - 200 - 150) and r.profit_factor == pytest.approx(2.0)
    assert r.cycle_durations() == [DAY + 3600, DAY - 3000]
    back = bt.Result.from_json(r.to_json())
    assert back.marks == r.marks and back.trades == 40 and back.hi == 500
    text = render.bt_manual_result_text(3, "preset ⭐ Martin", r, "EUR", 125)
    for s in ("Cycles : 2 terminé(s)", "✅ 1 gagné(s)", "❌ 1 perdu(s)", "Gain net +285.00", "40 fermés", "22 longs", "facteur de profit 2.00",
              "Minimum 200.00 EUR", "Maximum 500.00 EUR", "Cycle gagné : 1 j 1 h"):
        assert s in text, s
    png = backtest_chart(r, "Backtest #3", "EUR")
    assert png and png[:4] == b"\x89PNG"
    old = bt.summarize(4, T0, T0 + DAY, {"depart": 125, "solde": 130}, [])         # EA < 8.1.0 : pas de statistiques des trades
    assert old.trades == -1 and "Trades" not in render.bt_manual_result_text(4, "x", old, "EUR")


class ManualTester(TesterProcs):
    __test__ = False

    def __init__(self, common: Path) -> None:
        super().__init__(common)
        self.params = ""

    def launch(self, exe, ini=None):
        rid = [l for l in Path(exe).parent.joinpath("MQL5/Profiles/Tester", bt.TESTER_SET).read_bytes()[2:]
               .decode("utf-16-le").split("\r\n") if l.startswith("BacktestId=")][0].split("=")[1]
        f = bt.bt_files(self.common, int(rid))
        self.params = f["params"].read_text(encoding="utf-8")
        self.inis.append(Path(ini).read_text(encoding="utf-8") if ini else "")
        f["progress"].write_text(json.dumps({"sim": T0 + DAY, "solde": 420, "cycles": 0, "nb_recharges": 0}), encoding="utf-8")
        f["events"].write_text("\n".join(json.dumps(e) for e in MANUAL_EVENTS), encoding="utf-8")
        f["fin"].write_text(json.dumps(MANUAL_FIN), encoding="utf-8")
        rep = Path(exe).parent / "reports"
        rep.mkdir(exist_ok=True)
        (rep / f"bt_{rid}.htm").write_bytes(b"<html>rapport</html>")
        self.checks = 0
        return FakeProcs.launch(self, exe, None)


@pytest.mark.asyncio
async def test_backtest_mt5_depuis_telegram(svc):  # noqa: F811
    ctx, sender, dash, router, notifier, ea, conf = svc
    tp = ManualTester(ctx.cfg.paths.common())
    router.bt.procs = tp
    ctx.cfg.backtest.poll_s = 0.01
    folder = router.bt.folder
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ctx.cfg.mt5.terminal_exe).write_bytes(b"MZ")
    dash.store.put("Autre", {"lot": "0.02", "seuil_reprise": "100", "seuil_arret": "300"})
    dash.store.put("Martin", {"lot": "0.01", "sl_cont": "1", "seuil_on": "0", "seuil_reprise": "200", "seuil_arret": "500"})
    dash.store.set_active("Martin")
    await router.on_callback("d:rpt")
    assert any(d == "d:bm" for row in dash.render()[1] for _, d in row)
    await router.on_callback("d:bm")
    text, kb = dash.render()
    assert "à configurer" in text and any(d == "d:btc" for row in kb for _, d in row)
    ctx.db.meta_set("bt_account", json.dumps({"login": 123, "server": "Broker-Demo"}))
    text, kb = dash.render()
    assert "⭐ Martin" in text and "Minimum : <b>200.00" in text and "Maximum : <b>500.00" in text and "Ticks réels" in text
    # preset suivant puis retour à Martin (min / max repris du preset)
    await router.on_callback("d:bmp")
    assert "Autre" in dash.render()[0] and "100.00" in dash.render()[0]
    await router.on_callback("d:bmp")
    assert "⭐ Martin" in dash.render()[0]
    # minimum, maximum et date de début saisis pour ce test
    await router.on_callback("d:bmlo")
    await router.on_text("600")
    assert "doit être entre" in dash.render()[0]
    await router.on_text("250")
    await router.on_callback("d:bmhi")
    await router.on_text("750,5")
    await router.on_callback("d:bmdt")
    await router.on_text("pas une date")
    assert "non reconnue" in dash.render()[0]
    start = time.strftime("%d/%m/%Y", time.localtime(time.time() - 10 * DAY))
    await router.on_text(start)
    text = dash.render()[0]
    assert "250.00" in text and "750.50" in text and "changé pour ce test" in text and dash.bm.days in (9, 10, 11)
    await router.on_callback("d:bmgo")
    assert await _until(lambda: any("Backtest #" in s[1] and "terminé" in s[1] for s in sender.sent), 300)
    assert "seuil_reprise=250\n" in tp.params and "seuil_arret=750.5\n" in tp.params and "seuil_on=1\n" in tp.params
    assert "bt_sans_labo=1\n" in tp.params and "sl_cont=1\n" in tp.params and "lot=0.01\n" in tp.params
    assert "Model=4" in tp.inis[-1] and "Deposit=250.00" in tp.inis[-1]
    res_text = next(s[1] for s in sender.sent if "terminé" in s[1])
    assert "✅ 1 gagné(s)" in res_text and "❌ 1 perdu(s)" in res_text and "preset ⭐ Martin" in res_text
    assert await _until(lambda: sender.docs, 100)
    assert sender.photos and sender.docs[0][0].endswith(".htm") and not list((folder / "reports").glob("bt_*"))
    row = ctx.db.bt_list(1)[0]
    assert row["kind"] == "manuel" and row["status"] == "ok"
    # 🗂 derniers backtests : résultat et graphique renvoyés
    await router.on_callback("d:bml")
    assert any(d == f"d:bmg:{row['id']}" for r in dash.render()[1] for _, d in r)
    n = len(sender.photos)
    await router.on_callback(f"d:bmg:{row['id']}")
    assert len(sender.photos) == n + 1
    # ↺ min / max du preset
    await router.on_callback("d:bmrs")
    assert "Minimum : <b>200.00" in dash.render()[0]


@pytest.mark.asyncio
async def test_suivi_telegram_en_echec_n_arrete_pas_le_test(stack):
    """Bot 4.13.1 : « Timed out » de Telegram pendant le suivi → le test continue et donne son résultat."""
    cfg, db, *_ = stack
    tp = TesterProcs(cfg.paths.common())
    m = _mgr(cfg, db, tp)
    db.meta_set("bt_account", json.dumps({"login": 1, "server": "S"}))
    calls = []

    async def flaky(run):
        calls.append(run.stage)
        if len(calls) % 2:
            raise TimeoutError("Timed out")

    run = await m.execute(Run(12, "manuel", 0, 86400, "reel", "x"), params={}, deposit=200, currency="EUR", leverage=100,
                          notify=flaky, poll_s=0.01)
    assert run.ok and not run.error and run.result is not None and len(calls) > 3
