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
