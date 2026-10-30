"""Migration temporaire : installation de TrendPullback depuis Telegram (aucun Windows ni réseau réel)."""
import asyncio
import io
import json
import zipfile
from pathlib import Path

import pytest

from bot.system import system_kb
from migrations import trendpullback as tpm
from tests.test_bot import FakeSender, svc  # noqa: F401  (fixture)

GH = "github_pat_" + "A1b2C3d4E5" * 4
TG = "7123456789:" + "AAbbCCddEE" * 4
SX = "5555555555:" + "ZZyyXXwwVV" * 4
HC = "https://hc-ping.com/1234-abcd-5678"


def zip_pkg() -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("aigle3-TrendPullback-MT5-abc1234/install.ps1", "# installateur")
        z.writestr("aigle3-TrendPullback-MT5-abc1234/bot/version.txt", "0.3.1")
    return b.getvalue()


class FakeHttp:
    def __init__(self, gh_code=200, tg_code=200):
        self.gh_code, self.tg_code = gh_code, tg_code
        self.urls: list[str] = []
        self.auth: list[str] = []

    def __call__(self, url, headers):
        self.urls.append(url)
        self.auth.append(headers.get("Authorization", ""))
        if "api.telegram.org" in url:
            return self.tg_code, json.dumps({"ok": True, "result": {"username": "TrendPullbackBot"}}).encode(), {}
        if url.endswith("/commits/stable"):
            body = {"sha": "abc1234def", "commit": {"message": "Bot 0.3.1 : installation silencieuse",
                                                   "committer": {"date": "2026-10-30T10:00:00Z"}}}
            return self.gh_code, json.dumps(body).encode(), {}
        if "/zipball/" in url:
            return 200, zip_pkg(), {}
        return 404, b"", {}


class FakeRunner:
    def __init__(self, code=0):
        self.code = code
        self.calls: list[tuple[list, dict]] = []

    def __call__(self, cmd, env, lines, timeout):
        self.calls.append((cmd, env))
        lines += ["[OK] Python trouvé", f"jeton {env.get('TP_TELEGRAM_TOKEN', '')} écrit", "[OK] Bot démarré"]
        return self.code


def make(tmp_path, http=None, runner=None, roots=None, base=False):
    s = FakeSender()
    root = tmp_path / "TrendPullback"
    if base:
        (root / "MT5" / "BASE").mkdir(parents=True)
        (root / "MT5" / "BASE" / "terminal64.exe").write_bytes(b"MZ")
    inst = tpm.TPInstaller(s, 42, SX, tmp_path / "logs", root=root, http=http or FakeHttp(),
                           runner=runner or FakeRunner(), mt5_roots=roots or [], is_windows=True)
    return s, inst


def mt5_roots(tmp_path, *names):
    pf = tmp_path / "Program Files"
    for n in names:
        (pf / n).mkdir(parents=True)
        (pf / n / "terminal64.exe").write_bytes(b"MZ")
    (pf / "Autre").mkdir(parents=True, exist_ok=True)
    return [str(pf)]


async def finish(inst):
    for _ in range(200):
        if not inst.running:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("installation non terminée")


@pytest.mark.asyncio
async def test_parcours_complet_secrets_jamais_affiches(tmp_path, monkeypatch):
    monkeypatch.setattr(tpm, "PROGRESS_S", 0.01)
    roots = mt5_roots(tmp_path, "RoboForex - MetaTrader 5", "MetaTrader 5")
    http, runner = FakeHttp(), FakeRunner()
    s, inst = make(tmp_path, http, runner, roots)
    await inst.on_callback(["start"])
    assert inst.step == "gh" and inst.awaiting
    await inst.on_text("pas un jeton")
    assert inst.step == "gh" and "Format inattendu" in s.all_text()
    await inst.on_text(GH)
    assert inst.step == "tg" and http.auth[-1] == f"Bearer {GH}"
    await inst.on_text(SX)                                          # jeton de ScalpX : refusé
    assert inst.step == "tg" and "jeton du bot ScalpX" in s.all_text()
    await inst.on_text(TG)
    assert inst.step == "chat" and "@TrendPullbackBot" in s.all_text()
    await inst.on_callback(["chat"])
    assert inst.a["chat"] == "42" and inst.step == "sym"
    await inst.on_callback(["sym", "1"])
    assert inst.a["sym"] == "EURUSDc" and inst.step == "src"
    assert "RoboForex" in Path(inst.sources[0]).name                  # RoboForex proposé en premier
    await inst.on_callback(["src", "0"])
    assert inst.step == "srv"
    await inst.on_text("RoboForex-Demo, RoboForex-ECN")
    assert inst.a["srv"] == "RoboForex-Demo,RoboForex-ECN" and inst.step == "hc"
    await inst.on_text(HC)
    assert inst.step == "confirm"
    await inst.on_callback(["go"])
    assert inst.running and not inst.awaiting
    await finish(inst)
    # paquet stable extrait dans release, avec le commit (le bot TrendPullback ne le reproposera pas)
    rel = tmp_path / "TrendPullback" / "release"
    assert (rel / "install.ps1").exists()
    assert json.loads((rel / ".commit.json").read_text(encoding="utf-8"))["sha"] == "abc1234def"
    # secrets : uniquement dans l'environnement du processus, jamais sur la ligne de commande
    cmd, env = runner.calls[0]
    assert env["TP_GITHUB_TOKEN"] == GH and env["TP_TELEGRAM_TOKEN"] == TG and env["TP_CHAT_IDS"] == "42"
    assert env["TP_SYMBOL"] == "EURUSDc" and env["TP_HEALTHCHECK_URL"] == HC
    assert "RoboForex" in env["TP_MT5_SOURCE"] and env["TP_SERVERS"] == "RoboForex-Demo,RoboForex-ECN"
    assert "TELEGRAM_TOKEN" not in env and "GITHUB_TOKEN" not in env
    assert "-Silent" in cmd[-1] and not any(x in " ".join(cmd) for x in (GH, TG, HC))
    # rien de secret dans Telegram ni dans le journal ; mémoire vidée
    txt = s.all_text()
    assert "TrendPullback installé" in txt
    assert GH not in txt and TG not in txt and HC not in txt and SX not in txt
    logf = (tmp_path / "logs" / "trendpullback_install.log").read_text(encoding="utf-8")
    assert TG not in logf and "•••" in logf
    assert inst.a == {} and inst.step == "" and not inst.running


@pytest.mark.asyncio
async def test_base_deja_presente_et_reparation(tmp_path):
    s, inst = make(tmp_path, base=True)
    (tmp_path / "TrendPullback" / "bot").mkdir(parents=True)
    (tmp_path / "TrendPullback" / "bot" / ".env").write_text("TELEGRAM_TOKEN=x\n")
    await inst.on_callback(["start"])
    assert "déjà installé" in s.all_text()
    await inst.on_text(GH)
    assert inst.step == "sym"                                       # .env conservé : pas de jetons Telegram demandés
    await inst.on_text("EURUSD")
    assert inst.step == "srv"                                       # BASE présente : pas de choix de MT5
    await inst.on_callback(["skip"])
    await inst.on_callback(["skip"])
    assert inst.step == "confirm" and "déjà présent" in s.all_text()


@pytest.mark.asyncio
async def test_jetons_refuses_et_annulation(tmp_path):
    s, inst = make(tmp_path, FakeHttp(gh_code=404, tg_code=401), roots=[])
    await inst.on_callback(["start"])
    await inst.on_text(GH)
    assert inst.step == "gh" and "pas accès au dépôt" in s.all_text() and GH not in s.all_text()
    inst.http.gh_code = 200
    await inst.on_text(GH)
    await inst.on_text(TG)
    assert inst.step == "tg" and "refusé par Telegram" in s.all_text()
    await inst.on_callback(["cancel"])
    assert inst.a == {} and inst.step == "" and "annulée" in s.all_text()


@pytest.mark.asyncio
async def test_aucun_mt5_trouve(tmp_path):
    s, inst = make(tmp_path, roots=[str(tmp_path / "vide")])
    await inst.on_callback(["start"])
    await inst.on_text(GH)
    await inst.on_text(TG)
    await inst.on_text("123456789")
    await inst.on_text("EURUSD")
    assert inst.step == "" and "Aucune installation MT5" in s.all_text() and inst.a == {}


@pytest.mark.asyncio
async def test_echec_installateur(tmp_path, monkeypatch):
    monkeypatch.setattr(tpm, "PROGRESS_S", 0.01)
    s, inst = make(tmp_path, runner=FakeRunner(code=1), base=True)
    await inst.on_callback(["start"])
    for t in (GH, TG, "123456789", "EURUSD"):
        await inst.on_text(t)
    await inst.on_callback(["skip"])
    await inst.on_callback(["skip"])
    await inst.on_callback(["go"])
    await finish(inst)
    assert "Installation interrompue" in s.all_text() and TG not in s.all_text() and inst.a == {}


def test_masque_et_sources(tmp_path):
    assert tpm.mask(f"a {GH} b {TG}", []) == "a ••• b •••"
    assert tpm.mask("url https://hc-ping.com/xyz123", ["https://hc-ping.com/xyz123"]) == "url •••"
    roots = mt5_roots(tmp_path, "MetaTrader 5", "RoboForex MT5 Terminal")
    found = tpm.find_mt5_sources(roots)
    assert len(found) == 2 and "RoboForex" in found[0]


def test_hors_windows(tmp_path):
    s = FakeSender()
    inst = tpm.TPInstaller(s, 1, SX, tmp_path, root=tmp_path, is_windows=False)
    asyncio.run(inst.on_callback(["start"]))
    assert "uniquement sur le VPS Windows" in s.all_text() and inst.step == ""


@pytest.mark.asyncio
async def test_bouton_systeme_et_routage(svc, tmp_path):  # noqa: F811
    ctx, sender, dash, router, *_ = svc
    assert ("🧩 Installer TrendPullback", "mig:start") in [b for row in system_kb(False) for b in row]
    router.mig = tpm.TPInstaller(sender, 1, SX, tmp_path / "logs", root=tmp_path / "TP", http=FakeHttp(),
                                 runner=FakeRunner(), mt5_roots=[], is_windows=True)
    await router.on_callback("mig:start")
    assert router.mig.step == "gh"
    await router.on_text(GH)                                        # texte attendu : pris par l'installateur
    assert router.mig.step == "tg" and router.mig.a["gh"] == GH
    await router.on_callback("mig:cancel")
    assert not router.mig.awaiting


# ------------------------------------------------------------------ vérification de l'installation (lecture seule)
from migrations import tp_check as tpc  # noqa: E402


def tp_tree(tmp_path, gh=GH, tg=TG):
    root = tmp_path / "TrendPullback"
    bot, rel, base = root / "bot", root / "release", root / "MT5" / "BASE"
    for d in (bot / ".venv" / "Scripts", bot / "data" / "logs", rel / "bot", rel / "ea", base / "MQL5" / "Experts",
              base / "MQL5" / "Presets", base / "Profiles" / "Charts" / "Default", root / "MT5" / "instances",
              root / "MT5" / "archives"):
        d.mkdir(parents=True, exist_ok=True)
    for f in ("main.py", "requirements.txt", "watchdog.ps1", ".venv/Scripts/python.exe", ".venv/Scripts/pythonw.exe"):
        (bot / f).write_text("x")
    (bot / "version.txt").write_text("0.3.1")
    (rel / "bot" / "version.txt").write_text("0.3.1")
    (rel / "ea" / "version.txt").write_text("0.5.2")
    (base / "version.txt").write_text("0.5.2")
    (rel / ".commit.json").write_text(json.dumps({"sha": "abc1234def", "message": "Bot 0.3.1 : x", "fetched": 1}))
    for f in ("terminal64.exe", "MetaEditor64.exe", "MQL5/Experts/TrendPullback.ex5", "MQL5/Experts/TrendPullback.mq5",
              "MQL5/Presets/trendpullback.set"):
        (base / f).write_text("x")
    (base / "Profiles" / "Charts" / "Default" / "chart01.chr").write_text("<chart>\nsymbol=EURUSD\n</chart>")
    (bot / "config.toml").write_text('[paths]\nbase = "%s"\ndata = "data"\n\n[mt5]\nsymbol = "EURUSD"\n\n'
                                     '[runtime]\nlock_port = 47663\n\n[update]\ngithub = "aigle3/TrendPullback-MT5"\n'
                                     % str(base).replace("\\", "\\\\"))
    (bot / ".env").write_text(f"TELEGRAM_TOKEN={tg}\nALLOWED_CHAT_IDS=123456789\nGITHUB_TOKEN={gh}\n"
                              f"HEALTHCHECK_URL={HC}\n")
    (bot / "data" / "trendpullback.db").write_text("")
    (bot / "data" / "logs" / "bot.log").write_text(
        "2026-10-30 22:00:00 INFO main: TrendPullback central démarré (1 chat autorisé) en 2.0 s\n"
        f"2026-10-30 22:00:01 INFO core.github: jeton {gh} utilisé\n")
    common = tmp_path / "AppData" / "MetaQuotes" / "Terminal" / "Common" / "Files" / "TrendPullback"
    common.mkdir(parents=True)
    return root, common


def fake_run(tasks=None, check_code=0, check_out=None):
    tasks = tasks if tasks is not None else [
        {"n": "TrendPullback Bot", "s": "Running", "last": "2026-10-30T22:00:00", "res": 0x41301, "user": "VPS\\admin",
         "trig": "MSFT_TaskLogonTrigger"},
        {"n": "TrendPullback Watchdog", "s": "Ready", "last": "2026-10-30T22:02:00", "res": 0, "user": "VPS\\admin",
         "trig": "MSFT_TaskTimeTrigger"}]

    def run(cmd, cwd, timeout):
        if cmd[0] == "powershell":
            return 0, json.dumps(tasks)
        if cmd[-1] == "--version":
            return 0, "Python 3.12.7"
        if cmd[-1] == "--check":
            return check_code, check_out or "[OK]   BASE saine\n[OK]   TELEGRAM_TOKEN présent dans .env\n\nPrêt."
        return 1, ""
    return run


def checker(root, tmp_path, procs=None, port=True, run=None):
    bot_exe = str(root / "bot" / ".venv" / "Scripts" / "pythonw.exe")
    procs = procs if procs is not None else [
        {"pid": 4242, "name": "pythonw.exe", "exe": bot_exe, "cmdline": f"{bot_exe} main.py", "started": 1000.0, "rss": 80e6},
        {"pid": 77, "name": "pythonw.exe", "exe": r"C:\ScalpX\bot\.venv\Scripts\pythonw.exe", "cmdline": "main.py",
         "started": 1.0, "rss": 1}]
    return tpc.TPCheck(root, scalpx_token=SX, scalpx_port=47653, scalpx_logs=tmp_path / "logs", run=run or fake_run(),
                       procs=lambda: procs, port_check=lambda p: port, appdata=str(tmp_path / "AppData"), now=5000.0)


def levels(sections):
    return [(s.title, l, t) for s in sections for l, t in s.items]


def test_installation_saine_aucune_erreur_ni_secret(tmp_path):
    root, _ = tp_tree(tmp_path)
    sec = checker(root, tmp_path).scan()
    bad = [x for x in levels(sec) if x[1] in (tpc.ERR, tpc.WARN)]
    assert bad == [], bad
    txt = tpc.summary(sec) + tpc.full_report(sec)
    assert "Tout est en ordre" in txt and "pid 4242" in txt and "Python 3.12.7" in txt
    assert GH not in txt and TG not in txt and HC not in txt
    assert "pid 77" not in txt                                      # le bot ScalpX n'est pas pris pour celui de TP


def test_problemes_detectes(tmp_path):
    root, common = tp_tree(tmp_path, tg=SX)                         # même jeton que ScalpX
    (root / "MT5" / "BASE" / "MQL5" / "Experts" / "TrendPullback.ex5").unlink()
    (root / "MT5" / "BASE" / "Config").mkdir()
    (root / "MT5" / "BASE" / "Config" / "accounts.dat").write_text("x")
    (root / "MT5" / "BASE" / "Profiles" / "Charts" / "Default" / "chart02.chr").write_text("<expert>\nname=X\n</expert>")
    (root / "release" / "bot" / "version.txt").write_text("0.4.0")
    cfg = (root / "bot" / "config.toml").read_text() + '\n[paths2]\nx = "C:\\\\ScalpX\\\\bot"\n'
    (root / "bot" / "config.toml").write_text(cfg)
    with open(root / "bot" / "data" / "logs" / "bot.log", "a") as f:
        f.write("2026-10-30 22:05:00 ERROR bot.app: boom\n")
    (root / "bot" / "data" / "logs" / "crash.log").write_text("2026-10-30 22:06:00\nTraceback\n")
    (common / "statut_123_roboforexdemo.json").write_text(json.dumps(
        {"login": 123, "serveur": "RoboForex-Demo", "version": "0.5.2", "etat": "ACTIF", "connecte": True,
         "algo_terminal": True, "algo_ea": False, "ts": 4000}))
    tasks = [{"n": "TrendPullback Bot", "s": "Ready", "last": "", "res": 1, "user": "u", "trig": ""}]
    sec = checker(root, tmp_path, procs=[], port=False,
                  run=fake_run(tasks, 1, "[ERREUR] BASE : EA absent\n[OK]   x")).scan()
    txt = "\n".join(f"{l} {t}" for _, l, t in levels(sec))
    for needle in ("TrendPullback.ex5 absent", "warn BASE contient un compte", "warn 1 graphique",
                   "warn Bot : installé 0.3.1, paquet 0.4.0", "err config.toml pointe vers un dossier de ScalpX",
                   "IDENTIQUE à celui de ScalpX", "err bot TrendPullback NON lancé", "err verrou 127.0.0.1:47663 fermé",
                   "warn « TrendPullback Bot »", "warn « TrendPullback Watchdog » absente", "warn 1 erreur(s)",
                   "warn 1 plantage", "AutoTrading NON", "err BASE : EA absent"):
        assert needle in txt, needle
    s = tpc.summary(sec)
    assert "Problème(s) à corriger" in s and SX not in tpc.full_report(sec) and len(s) <= 3800


def test_controle_en_erreur_n_arrete_pas_les_autres(tmp_path):
    root, _ = tp_tree(tmp_path)

    def boom():
        raise PermissionError("accès refusé")
    sec = checker(root, tmp_path, procs=None).__class__(root, run=fake_run(), procs=boom, port_check=lambda p: True,
                                                         appdata=str(tmp_path / "AppData")).scan()
    titles = [s.title for s in sec]
    assert "c_procs" in titles and "📜 Journaux" in titles and "✔️ Vérification du bot (main.py --check)" in titles


@pytest.mark.asyncio
async def test_bouton_verifier_envoie_resume_et_rapport(tmp_path):
    root, _ = tp_tree(tmp_path)
    s = FakeSender()
    inst = tpm.TPInstaller(s, 42, SX, tmp_path / "logs", root=root, is_windows=True,
                           checker=lambda r, **kw: checker(r, tmp_path))
    assert ("🔎 Vérifier TrendPullback", "mig:chk") in [b for row in system_kb(False) for b in row]
    await inst.on_callback(["chk"])
    for _ in range(200):
        if not inst.checking:
            break
        await asyncio.sleep(0.01)
    txt = s.all_text()
    assert "Vérification de TrendPullback" in txt and "Tout est en ordre" in txt
    assert s.docs and s.docs[0][0] == "trendpullback_verification.txt"
    rep = s.docs[0][1].decode("utf-8")
    assert "[OK]" in rep and GH not in rep and TG not in rep and HC not in rep and not inst.checking


def test_journaux_mt5_des_comptes(tmp_path):
    root, _ = tp_tree(tmp_path)
    inst = root / "MT5" / "instances" / "68526357_RoboForex_Pro"
    (inst / "logs").mkdir(parents=True)
    (inst / "MQL5" / "Logs").mkdir(parents=True)
    (inst / "MQL5" / "Experts").mkdir(parents=True)
    (inst / "terminal64.exe").write_text("x")
    term = ("﻿KO\t0\t00:08:01.123\tNetwork\t'68526357': authorized on RoboForex-Pro through Access Point\r\n"
            "JK\t2\t00:08:02.456\tExperts\texpert TrendPullback (EURUSD,H4) loading failed\r\n")
    (inst / "logs" / "20261031.log").write_bytes(term.encode("utf-16-le"))
    (inst / "MQL5" / "Logs" / "20261031.log").write_bytes("﻿PN\t0\t00:08:03.000\tTrendPullback (EURUSD,H4)\tarray out of range in 'TP_News.mqh' (12,5)\r\n".encode("utf-16-le"))
    bot_exe = str(root / "bot" / ".venv" / "Scripts" / "pythonw.exe")
    procs = [{"pid": 4242, "name": "pythonw.exe", "exe": bot_exe, "cmdline": "main.py", "started": 1.0, "rss": 1},
             {"pid": 9, "name": "metaeditor64.exe", "exe": str(inst / "MetaEditor64.exe"), "cmdline": "", "started": 1.0, "rss": 1}]
    sec = checker(root, tmp_path, procs=procs).scan()
    txt = "\n".join(f"{l} {t}" for _, l, t in levels(sec))
    assert "warn 68526357_RoboForex_Pro : TrendPullback.ex5 absent" in txt
    assert "warn 68526357_RoboForex_Pro [terminal] 00:08:02 Experts\texpert TrendPullback (EURUSD,H4) loading failed" in txt
    assert "array out of range in 'TP_News.mqh'" in txt and "authorized on RoboForex-Pro" in txt
    assert "warn MetaEditor lancé" in txt
    rep = tpc.full_report(sec)
    assert "--- 68526357_RoboForex_Pro : journal terminal 20261031.log" in rep and "loading failed" in rep


def test_fichiers_d_echange_et_journal_ea_dans_le_rapport(tmp_path):
    root, common = tp_tree(tmp_path)
    (common / "logs").mkdir()
    (common / "logs" / "68526357_2026-10-31.jsonl").write_text('{"type":"system","msg":"démarrage"}\n')
    (common / "events_68526357_RoboForex_Pro.jsonl").write_text('{"type":"DEMARRAGE_EA"}\n')
    rep = tpc.full_report(checker(root, tmp_path).scan())
    assert "logs" in rep and '"msg":"démarrage"' in rep and "DEMARRAGE_EA" in rep
    assert "--- bot.log (60 dernières lignes) ---" in rep and GH not in rep
