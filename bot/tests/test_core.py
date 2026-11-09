import asyncio
import json
import threading
import time
from pathlib import Path

import pytest

from core.commands import CommandQueue
from core.config import Config, load_config, parse_env
from core.events import EventReader, parse_lines
from core.fsio import atomic_write, read_json
from core.health import diagnose
from core.ids import make_id, normalize_server, valid_login
from core.models import Statut
from core.service import Monitor, event_to_notice
from core.status import StatusReader
from mt5.ini import IniError, build_ini, mask
from mt5 import base as basemod
from storage.db import DEMARRE, Database, Instance


def test_ids_identiques_a_l_ea():
    assert normalize_server("ICMarkets-Demo 02") == "ICMarkets_Demo_02"
    assert make_id(123, "A.B-C d") == "123_A_B_C_d"
    assert make_id("007", "Srv") == "7_Srv"
    assert valid_login("12345") and not valid_login("12a") and not valid_login("")


def test_ecriture_atomique_utf8_sans_bom(tmp_path: Path):
    p = tmp_path / "x.json"
    atomic_write(p, '{"a":"é"}')
    assert p.read_bytes()[:3] != b"\xef\xbb\xbf"
    assert read_json(p) == {"a": "é"}
    assert not (tmp_path / "x.json.tmp").exists()


def test_lecture_json_tolerante(tmp_path: Path):
    (tmp_path / "a.json").write_text('{"x":')            # partiel
    (tmp_path / "b.json").write_text("")                  # vide
    (tmp_path / "c.json").write_text("[1,2]")             # pas un objet
    for n in "abc":
        assert read_json(tmp_path / f"{n}.json") is None
    assert read_json(tmp_path / "absent.json") is None
    (tmp_path / "bom.json").write_bytes(b"\xef\xbb\xbf" + b'{"ok":1}')
    assert read_json(tmp_path / "bom.json") == {"ok": 1}


def test_statut_json_tolerant_champs_inconnus(tmp_path: Path):
    s = Statut.model_validate({"id": "1_a", "solde": 400, "champ_futur": 1, "ts": int(time.time())})
    assert s.solde == 400 and s.age_s() < 5 and s.etat == "ACTIF"
    assert StatusReader(tmp_path).read("zzz") is None


def test_progress_et_retrait():
    s = Statut(solde=300, solde_plus_flottant_ea=400, seuil_arret=500, seuil_reprise=125, etat="ACTIF")
    assert abs(s.progress500() - (400 - 125) / (500 - 125)) < 1e-9 and s.to_retire() == 0
    s3 = Statut(solde_plus_flottant_ea=206.19, seuil_arret=500, seuil_reprise=200)
    assert abs(s3.progress500() - 6.19 / 300) < 1e-9          # 200 = 0 %, 500 = 100 %
    assert Statut(solde_plus_flottant_ea=150, seuil_arret=500, seuil_reprise=200).progress500() == 0.0
    s2 = Statut(solde=503.2, etat="ARRET_500", seuil_reprise=125)
    assert abs(s2.to_retire() - 378.2) < 1e-9


def test_commandes_une_par_fichier_ordre_croissant(tmp_path: Path):
    q = CommandQueue(tmp_path)
    ns = [q.send("1_a", "PING", [str(i)]) for i in range(10)]
    assert ns == sorted(ns) and len(set(ns)) == 10
    files = list(tmp_path.glob("cmd_1_a_*.json"))
    assert len(files) == 10
    d = json.loads(q.cmd_path("1_a", ns[0]).read_text("utf-8"))
    assert d["id"] == "1_a" and d["cmd"] == "PING" and d["args"] == ["0"] and d["ts"] > 1e9


@pytest.mark.asyncio
async def test_send_and_wait_ack_et_timeout(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    iid = "1_Srv"
    (cfg.paths.instances / iid).mkdir(parents=True)
    exe = cfg.paths.instances / iid / "terminal64.exe"
    exe.write_bytes(b"x")
    (cfg.paths.instances / iid / "MQL5" / "Experts").mkdir(parents=True)
    (cfg.paths.instances / iid / "MQL5" / "Experts" / "ScalpX_v5.ex5").write_text("5.0")
    procs.launch(exe)
    ea.start()
    try:
        r = await cmdq.send_and_wait(iid, "PAUSE", timeout=2)
        assert r.ok and not r.timeout and ea.accounts[iid]["etat"] == "PAUSE"
        # rafale de 10 commandes : toutes exécutées, 10 ack
        res = await asyncio.gather(*[cmdq.send_and_wait(iid, "PING", [str(i)], timeout=3) for i in range(10)])
        assert all(x.ok for x in res)
    finally:
        await ea.stop()
    r = await cmdq.send_and_wait("9_none", "PING", timeout=0.6)
    assert r.timeout and not r.ok
    assert not list(cfg.paths.common().glob("cmd_9_none_*"))      # commande non lue retirée


def test_events_drain_renomme_lit_supprime(tmp_path: Path):
    ids = "1_a"
    f = tmp_path / f"events_{ids}.jsonl"
    f.write_text('{"seq":1,"ts":5,"id":"1_a","type":"NOTIF","texte":"a"}\nLIGNE CORROMPUE\n'
                 '{"seq":2,"ts":6,"id":"1_a","type":"ARRET_500","prio":2,"texte":"b","data":{"solde":500}}\n', encoding="utf-8")
    ev = EventReader(tmp_path).drain(ids)
    assert [e.seq for e in ev] == [1, 2]
    assert not f.exists() and not list(tmp_path.glob("events_*"))
    assert EventReader(tmp_path).drain(ids) == []


def test_events_copie_laissee_par_un_crash_relue(tmp_path: Path):
    (tmp_path / "events_1_a.jsonl.123.proc").write_text('{"seq":1,"ts":1,"id":"1_a","type":"NOTIF"}\n')
    (tmp_path / "events_1_a.jsonl").write_text('{"seq":2,"ts":2,"id":"1_a","type":"NOTIF"}\n')
    assert [e.seq for e in EventReader(tmp_path).drain("1_a")] == [1, 2]


def test_events_ajout_concurrent_pas_de_perte(tmp_path: Path):
    """L'EA ajoute des lignes pendant que le bot lit : aucune ligne perdue."""
    stop = threading.Event()
    written = []

    def writer():
        i = 0
        while not stop.is_set():
            i += 1
            with open(tmp_path / "events_1_a.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"seq": i, "ts": i, "id": "1_a", "type": "NOTIF"}) + "\n")
            written.append(i)
            time.sleep(0.001)

    t = threading.Thread(target=writer)
    t.start()
    got = []
    rd = EventReader(tmp_path)
    t0 = time.time()
    while time.time() - t0 < 1.0:
        got += [e.seq for e in rd.drain("1_a")]
        time.sleep(0.01)
    stop.set()
    t.join()
    got += [e.seq for e in rd.drain("1_a")]
    assert sorted(got) == written


def test_ini_cles_et_masquage():
    ini = build_ini(123, "S3cret!", "Srv-1", "ScalpX_v5", "scalpx_v5.set", "XAUUSD", "M5")
    for k in ("Login=123", "Password=S3cret!", "Server=Srv-1", "KeepPrivate=1", "AllowDllImport=0", "AllowLiveTrading=1",
              "Enabled=1", "Expert=ScalpX_v5", "ExpertParameters=scalpx_v5.set", "Symbol=XAUUSD", "Period=M5"):
        assert k in ini
    assert mask("erreur avec S3cret! dedans", "S3cret!") == "erreur avec *** dedans"
    with pytest.raises(IniError):
        build_ini(1, "a\nAllowDllImport=1", "S", "E", "s", "X", "M5")     # injection de clé refusée


def test_env_et_config(tmp_path: Path):
    assert parse_env('# c\nTELEGRAM_TOKEN="abc:1"\nALLOWED_CHAT_IDS=1, -2\n') == {"TELEGRAM_TOKEN": "abc:1", "ALLOWED_CHAT_IDS": "1, -2"}
    (tmp_path / "config.toml").write_text('[mt5]\nservers=["A","B"]\ndefault_server="A"\n[paths]\nbase="C:\\\\MT5\\\\BASE"\n')
    (tmp_path / ".env").write_text("TELEGRAM_TOKEN=tok\nALLOWED_CHAT_IDS=42;43\n")
    c = load_config(tmp_path / "config.toml")
    assert c.token == "tok" and c.allowed_chats == [42, 43] and c.mt5.servers == ["A", "B"]


def test_base_saine_et_detection_ea_dans_profil(cfg):
    assert basemod.check_base(cfg) == []
    chr_ = cfg.paths.base / "Profiles" / "Charts" / "Default" / "chart01.chr"
    chr_.write_bytes("<chart>\n<expert>\nname=ScalpX_v5\n</expert>\n</chart>".encode("utf-16"))
    p = basemod.check_base(cfg)
    assert p and "graphique" in p[0]


def test_base_hash_stable(cfg):
    h = basemod.tree_hash(cfg.paths.base)
    assert h == basemod.tree_hash(cfg.paths.base)
    (cfg.paths.base / "x.txt").write_text("1")
    assert h != basemod.tree_hash(cfg.paths.base)


def test_set_keys_utf16(tmp_path: Path):
    p = tmp_path / "a.set"
    p.write_bytes("; c\r\nA=1\r\nB=2\r\n".encode("utf-16"))
    assert basemod.set_keys(p) == ["A", "B"]


def _inst(state=DEMARRE):
    return Instance("1_a", 1, "a", "", "x", 1, "5.0", state)


def test_diagnostic_croise(cfg):
    now = time.time()
    ok = Statut(id="1_a", connecte=True, algo_terminal=True, algo_ea=True, nb_graphiques_ea=1, devise="EUR", ts=int(now),
                solde=300.0)
    assert diagnose(_inst(), ok, True, cfg, now).icon == "🟢"
    d = diagnose(_inst(), ok.model_copy(update={"solde": 0.0}), True, cfg, now)          # sous le seuil de reprise (125)
    assert d.icon == "🟠" and d.code == "CAPITAL" and "0.00 EUR (minimum 125 EUR)" in d.reasons[0]
    assert diagnose(_inst(), None, False, cfg, now).icon == "🔴"
    assert diagnose(_inst("ARRETE"), None, False, cfg, now).icon == "⚪"
    old = ok.model_copy(update={"ts": int(now) - 60})
    assert diagnose(_inst(), old, True, cfg, now).icon == "🟠"
    assert diagnose(_inst(), ok.model_copy(update={"connecte": False}), True, cfg, now).icon == "🟠"
    assert diagnose(_inst(), ok.model_copy(update={"algo_ea": False}), True, cfg, now).icon == "🟠"
    d = diagnose(_inst(), ok.model_copy(update={"nb_graphiques_ea": 2}), True, cfg, now)
    assert d.icon == "🟠" and d.code == "DOUBLON"
    assert diagnose(_inst(), ok.model_copy(update={"phase_cloture500": True}), True, cfg, now).icon == "🔒"
    assert diagnose(_inst(), ok.model_copy(update={"maintenance": True}), True, cfg, now).icon == "⚙️"
    assert diagnose(_inst("MAINTENANCE"), ok, True, cfg, now).icon == "⚙️"
    assert diagnose(_inst(), ok.model_copy(update={"devise": "USD"}), True, cfg, now).code == "DEVISE"
    assert diagnose(_inst(), ok, True, cfg, now, folder_ok=False).code == "DOSSIER"


def test_db_registre_historique(db):
    db.add_instance(_inst())
    assert db.exists_active("1_a") and not db.exists_active("2_b")
    db.archive("1_a")
    assert not db.exists_active("1_a") and db.list() == []
    db.add_withdrawal("1_a", 378.2, 503.2, 125.0)
    assert db.total_withdrawn() == pytest.approx(378.2)
    assert "378.2" in db.export_csv("withdrawals")
    with pytest.raises(ValueError):
        db.export_csv("instances; DROP TABLE instances")
    db.meta_set("dash_msg", "42")
    assert db.meta_get("dash_msg") == "42"


@pytest.mark.asyncio
async def test_monitor_evenements_retrait_cycle_et_closures(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    db.add_instance(Instance("1_a", 1, "a", "Alpha", str(cfg.paths.instances / "1_a"), 1, "5.0", DEMARRE))
    from core.events import EventReader
    got = []

    async def emit(n):
        got.append(n)

    mon = Monitor(cfg, db, status, EventReader(cfg.paths.common()), emit)
    now = int(time.time())
    from core.fsio import atomic_write
    atomic_write(status.path("1_a"), json.dumps({"id": "1_a", "etat": "ARRET_500", "solde": 503.2, "ts": now, "devise": "EUR", "version": "5.0"}))
    mon.poll_status()
    ea.event("1_a", "CLOTURE_500_OK", "🏁 fini", 2, {"solde": 503.2, "ecart": 3.2, "a_retirer": 378.2, "cycle": 0}, 1)
    ea.event("1_a", "REPRISE_RETRAIT", "🟢 reprise", 2, {"solde": 125.4, "cycle": 1}, 2)
    ea.event("1_a", "CASHFLOW", "", 0, {"montant": -378, "solde": 125.4}, 3)
    await mon.poll_events()
    assert [n.kind for n in got] == ["CLOTURE_500_OK", "REPRISE_RETRAIT"]       # CASHFLOW vide : pas de notif
    assert all(n.prio == 2 and n.label == "Alpha" for n in got)
    w = db.withdrawals_since(0)
    assert len(w) == 1 and w[0]["amount"] == pytest.approx(377.8)
    assert db.closures_since(0)[0]["kind"] == "OK"
    assert len(db.cycles_since(0)) == 1
    assert len(db.events_since(0)) == 3


def test_event_to_notice_photo(tmp_path: Path):
    (tmp_path / "sx_shot_1_a.png").write_bytes(b"png")
    from core.models import Evenement
    n = event_to_notice(Evenement(id="1_a", type="CAPTURE", texte="📸", data={"fichier": "sx_shot_1_a.png"}), "A", tmp_path)
    assert n and n.photo and n.photo.name == "sx_shot_1_a.png"
    n2 = event_to_notice(Evenement(id="1_a", type="DEMARRAGE_EA", texte="x"), "A", tmp_path)
    assert n2 and n2.prio == 0


def test_nettoyage_des_orphelins(tmp_path: Path):
    import os
    q = CommandQueue(tmp_path)
    old = tmp_path / "ack_1_a_5.json"
    old.write_text("{}")
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    fresh = tmp_path / "ack_1_a_6.json"
    fresh.write_text("{}")
    (tmp_path / "statut_1_a.json").write_text("{}")
    assert q.cleanup_orphans(600) == 1 and not old.exists() and fresh.exists() and (tmp_path / "statut_1_a.json").exists()


def test_explication_arret_ea():
    from core.service import explain_stop
    assert "à la demande du bot" in explain_stop(9, True)
    assert "sans demande du bot" in explain_stop(9, False) and "watchdog" in explain_stop(9, False)
    assert "recompilé" in explain_stop(2, False) and "code 42" in explain_stop(42, False)


@pytest.mark.asyncio
async def test_monitor_arret_ea_explique(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    db.add_instance(Instance("2_b", 2, "b", "Beta", str(cfg.paths.instances / "2_b"), 1, "5.0", DEMARRE))
    from core.events import EventReader
    got = []

    async def emit(n):
        got.append(n)

    mon = Monitor(cfg, db, status, EventReader(cfg.paths.common()), emit)
    ea.event("2_b", "ARRET_EA", "🔌 ScalpX arrêté (raison 9)", 1, {"raison": 9}, 1)
    await mon.poll_events()
    db.meta_set("closing_2_b", str(int(time.time())))                      # fermeture demandée par le bot
    ea.event("2_b", "ARRET_EA", "🔌 ScalpX arrêté (raison 9)", 1, {"raison": 9}, 2)
    await mon.poll_events()
    assert "sans demande du bot" in got[0].text and "à la demande du bot" in got[1].text


def test_capital_insuffisant_notifie_une_fois_avec_son(cfg, db, tmp_path):
    from core.service import Monitor
    sent = []

    async def emit(n):
        sent.append(n)

    class St:
        def __init__(self): self.s = None
        def read(self, iid): return self.s

    sr = St()
    db.add_instance(_inst())
    mon = Monitor(cfg, db, sr, None, emit)
    mon.events = type("E", (), {"drain": lambda self, iid: []})()
    now = time.time()
    sr.s = Statut(id="1_a", solde=0.0, devise="EUR", ts=int(now))
    import asyncio
    for _ in range(3):
        mon.poll_status(now)
        asyncio.run(mon.poll_events())
    assert len(sent) == 1 and sent[0].prio == 2 and "CAPITAL INSUFFISANT" in sent[0].text and "0.00 EUR" in sent[0].text
    sr.s = Statut(id="1_a", solde=200.0, devise="EUR", ts=int(now))
    mon.poll_status(now)
    asyncio.run(mon.poll_events())
    assert len(sent) == 2 and "CAPITAL SUFFISANT" in sent[1].text


def test_env_example_healthcheck_en_commentaire():
    """.env.example propose HEALTHCHECK_URL en commentaire : ignoré tant qu'on n'a pas retiré le #, lu ensuite."""
    from pathlib import Path
    txt = (Path(__file__).resolve().parent.parent / ".env.example").read_text(encoding="utf-8")
    assert "# HEALTHCHECK_URL=https://" in txt
    assert "HEALTHCHECK_URL" not in parse_env(txt)
    actif = txt.replace("# HEALTHCHECK_URL=", "HEALTHCHECK_URL=")
    assert parse_env(actif)["HEALTHCHECK_URL"].startswith("https://")


@pytest.mark.asyncio
async def test_monitor_un_evenement_mal_forme_n_arrete_pas_les_suivants(stack):
    """Une donnée illisible (cycle non numérique) est journalisée ; les événements suivants et les autres comptes sont traités."""
    cfg, db, procs, status, cmdq, mgr, ea = stack
    db.add_instance(Instance("1_a", 1, "a", "Alpha", str(cfg.paths.instances / "1_a"), 1, "5.0", DEMARRE))
    db.add_instance(Instance("2_b", 2, "b", "Beta", str(cfg.paths.instances / "2_b"), 1, "5.0", DEMARRE))
    from core.events import EventReader
    got = []

    async def emit(n):
        got.append(n)

    mon = Monitor(cfg, db, status, EventReader(cfg.paths.common()), emit)
    ea.event("1_a", "REPRISE_RETRAIT", "🟢 reprise", 2, {"solde": 125.4, "cycle": "x"}, 1)     # cycle illisible
    ea.event("1_a", "NOTIF", "suite", 1, None, 2)
    ea.event("2_b", "NOTIF", "autre compte", 1, None, 1)
    assert await mon.poll_events() == 3
    assert [n.text for n in got] == ["suite", "autre compte"]
    # statut illisible pour un compte : les autres comptes continuent d'être échantillonnés
    from core.fsio import atomic_write
    now = int(time.time())
    atomic_write(status.path("2_b"), json.dumps({"id": "2_b", "etat": "ACTIF", "solde": 200.0, "ts": now, "devise": "EUR"}))
    orig = mon._check_capital
    mon._check_capital = lambda inst, st: (_ for _ in ()).throw(RuntimeError("boum")) if inst.id == "1_a" else orig(inst, st)
    atomic_write(status.path("1_a"), json.dumps({"id": "1_a", "etat": "ACTIF", "solde": 100.0, "ts": now, "devise": "EUR"}))
    mon.poll_status()
    assert [r["iid"] for r in db.balances("2_b", 0)] == ["2_b"]


def test_base_en_journal_wal_et_sauvegarde(tmp_path: Path):
    db = Database(tmp_path / "x.db")
    assert db._all("PRAGMA journal_mode")[0][0].lower() == "wal"
    db.meta_set("k", "v")
    db.backup_to(tmp_path / "copie.db")
    assert Database(tmp_path / "copie.db").meta_get("k") == "v"


def test_filtre_des_secrets_dans_les_traces():
    import logging
    import main
    f = main.SecretFilter("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef", "github_pat_SECRET1234567890", "https://hc-ping.com/uuid-1234")
    try:
        raise RuntimeError("POST https://api.telegram.org/bot123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef/getUpdates github_pat_SECRET1234567890")
    except RuntimeError:
        import sys
        rec = logging.LogRecord("x", logging.ERROR, __file__, 1, "ping %s Password=abc", ("https://hc-ping.com/uuid-1234",), sys.exc_info())
    assert f.filter(rec)
    out = logging.Formatter().format(rec)
    assert "ABCDEFGHIJ" not in out and "SECRET123" not in out and "uuid-1234" not in out and "abc" not in out.split("Password=")[1][:3]
    assert "<token>" in out and "Traceback" in out and "RuntimeError" in out


def test_resultat_du_jour_sans_le_latent():
    # EA 6.2.2+ : champ envoyé par l'EA
    assert Statut(id="1_a", solde=500, equity=450, pl_latent=-50, pl_jour=-60, pl_jour_realise=-10).day_realized() == -10
    # EA plus ancien : réalisé = (equity − départ) − (equity − solde)
    assert Statut(id="1_a", solde=500, equity=450, pl_latent=-50, pl_jour=-60).day_realized() == pytest.approx(-10)
    assert Statut(id="1_a", solde=500, equity=450, pl_jour=0).day_realized() == 0.0      # journée pas encore initialisée


def test_catalogue_du_bot_identique_aux_reglages_de_l_ea():
    """Chaque réglage du bot existe dans l'EA (Reg) avec les mêmes bornes, et chaque input du .set de BASE existe dans l'EA."""
    import re
    from pathlib import Path
    from core import params as cat, presets as cps
    ea = Path(__file__).resolve().parents[2] / "ea" / "MQL5"
    core = (ea / "Experts" / "SX_Core.mqh").read_text(encoding="utf-8-sig")
    def args(i):                                     # arguments d'un appel Reg( … ) (virgules hors parenthèses et chaînes)
        out, cur, depth, q = [], "", 0, False
        for ch in core[i:]:
            if ch == '"':
                q = not q
            if not q and ch == "(":
                depth += 1
            if not q and ch == ")":
                if depth == 0:
                    break
                depth -= 1
            if not q and ch == "," and depth == 0:
                out.append(cur.strip()); cur = ""
                continue
            cur += ch
        return out + [cur.strip()]
    regs = {}
    for m in re.finditer(r"\bReg\(", core):
        a = args(m.end())
        if len(a) > 8 and a[0].startswith("P_"):
            regs[a[1].strip('"')] = (a[6], a[7])
    for p in cat.PARAMS:
        assert p[0] in regs, f"{p[0]} absent de l'EA"
        mn, mx = (float(x) for x in regs[p[0]])
        assert (mn, mx) == (p[4], p[5]), f"bornes de {p[0]} : EA {mn}–{mx}, bot {p[4]}–{p[5]}"
    mq5 = (ea / "Experts" / "ScalpX_v5.mq5").read_text(encoding="utf-8-sig")
    inputs = set(re.findall(r"^input\s+\w+\s+(\w+)\s*=", mq5, re.M))
    for k, name in cps.SET_INPUTS.items():
        assert name in inputs, f"input {name} ({k}) absent de l'EA"
    st = (ea / "Presets" / "scalpx_v5.set").read_bytes()[2:].decode("utf-16-le")
    for line in st.split("\r\n"):
        if "=" in line and not line.startswith(";"):
            assert line.split("=")[0] in inputs, line
    for k in ("UseExtensionFilter", "ExtensionMaxATR", "UseFreshSignalFilter", "FreshSignalMaxBars", "ObjectifAutoEntreeMin"):
        assert f"\r\n{k}=" in st


def test_commandes_simultanees_numeros_uniques_hors_boucle(tmp_path):
    """send() tourne dans des fils (send_and_wait) : jamais deux fois le même numéro, même en parallèle."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from core.commands import CommandQueue
    q = CommandQueue(tmp_path)
    with ThreadPoolExecutor(8) as ex:
        ns = list(ex.map(lambda i: q.send("A", "SET", ["k", str(i)]), range(200)))
    assert len(set(ns)) == 200 and len(list(tmp_path.glob("cmd_A_*.json"))) == 200

    async def run():
        return await q.send_and_wait("B", "PING", timeout=0.3, poll=0.05)
    r = asyncio.run(run())
    assert r.timeout and not list(tmp_path.glob("cmd_B_*.json"))               # sans réponse : commande retirée


def test_backtest_incomplet(tmp_path):
    """Bot 3.1.1 : un test arrêté avant la date de fin est détecté (et pas analysé comme complet)."""
    from core import backtest as bt
    d_to = 1_762_473_600                                   # 07/11/2026
    assert bt.stopped_early({"sim": d_to - 40 * 86400}, d_to)
    assert not bt.stopped_early({"sim": d_to - 2 * 86400}, d_to)   # week-end : derniers ticks le vendredi
    assert not bt.stopped_early({}, d_to)
    lg = tmp_path / "Tester" / "Agent-127.0.0.1-3000" / "logs"
    lg.mkdir(parents=True)
    (lg / "20261107.log").write_bytes("﻿KP\t0\t19:20:06.5\tCore 1\t2026.09.30 10:00:00   critical runtime error 502 in OnTick (array out of range in 'SX_Labo.mqh' (1012,12))\r\nAB\t0\t19:20:07\tCore 1\tfinal balance 312.40 EUR\r\n".encode("utf-16-le"))
    errs = bt.log_errors(tmp_path)
    assert len(errs) == 1 and "array out of range" in errs[0]


def test_journaux_mt5_supprimes_pendant_la_lecture(tmp_path, monkeypatch):
    """Bot 3.3.1 : un journal listé puis supprimé par MT5 (rotation au démarrage) ne fait plus échouer le backtest."""
    from pathlib import Path
    from core import backtest as bt
    d = tmp_path / "Tester" / "logs"
    d.mkdir(parents=True)
    (d / "20261104.log").write_text("vieux")
    (d / "20261110.log").write_text("x\tx\t00:40\tTester\tsynchronisation\n")
    real = Path.stat

    def stat(self, *a, **k):
        if self.name == "20261104.log":
            raise FileNotFoundError(2, "The system cannot find the file specified", str(self))
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "stat", stat)
    assert "synchronisation" in bt.tail_log(tmp_path)
    assert bt.log_errors(tmp_path) == []
