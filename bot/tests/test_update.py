"""Mise à jour automatique depuis le paquet OneDrive."""
import os
import time
from pathlib import Path

import pytest

from core import update as u


def make_pkg(tmp_path, cfg, bot_root):
    src = tmp_path / "release"
    (src / "bot" / "bot").mkdir(parents=True)
    (src / "bot" / "main.py").write_text("print('v2')")
    (src / "bot" / "bot" / "x.py").write_text("X = 2")
    (src / "bot" / "config.toml").write_text("[paths]\n")
    (src / "bot" / ".env").write_text("TELEGRAM_TOKEN=secret")
    (src / "bot" / "data").mkdir()
    (src / "bot" / "data" / "scalpx.db").write_text("db")
    ex = src / "ea" / "MQL5" / "Experts"
    ex.mkdir(parents=True)
    (src / "ea" / "MQL5" / "Presets").mkdir(parents=True)
    (ex / "SX_Core.mqh").write_bytes(b'\xef\xbb\xbf#define SX_VERSION   "5.3"\r\nint a;\r\n')
    (src / "ea" / "MQL5" / "Presets" / "scalpx_v5.set").write_text("A=1\nB=2\n")
    (src / "ea" / "version.txt").write_text("5.3")
    old = time.time() - 300
    for p in src.rglob("*"):
        if p.is_file():
            os.utime(p, (old, old))
    cfg.update.source = str(src)
    # installation actuelle
    (bot_root / "bot").mkdir(parents=True)
    (bot_root / "main.py").write_text("print('v2')")
    (bot_root / "bot" / "x.py").write_text("X = 1")
    (bot_root / "config.toml").write_text("[paths]\nbase='C:/perso'\n")
    (cfg.paths.base / "MQL5" / "Experts" / "SX_Core.mqh").write_bytes(b'#define SX_VERSION   "5.3"\nint a;\n')
    (cfg.paths.base / "version.txt").write_text("5.3")
    return src


def test_plan_ignore_secrets_donnees_et_lignes_de_version(tmp_path, cfg):
    bot_root = tmp_path / "botinst"
    src = make_pkg(tmp_path, cfg, bot_root)
    plan = u.plan_update(cfg, bot_root)
    assert plan.bot_files == ["bot/x.py"]                  # ni .env, ni config.toml, ni data\
    assert plan.ea_files == []                             # SX_Core identique hors BOM/CRLF ; .set identique
    # seule la ligne de version diffère → pas une mise à jour
    (cfg.paths.base / "MQL5" / "Experts" / "SX_Core.mqh").write_bytes(b'#define SX_VERSION   "5.4"\nint a;\n')
    assert u.plan_update(cfg, bot_root).ea_files == []


def test_installation_ea_puis_version_txt_en_dernier(tmp_path, cfg):
    bot_root = tmp_path / "botinst"
    src = make_pkg(tmp_path, cfg, bot_root)
    (src / "ea" / "MQL5" / "Experts" / "SX_Core.mqh").write_bytes(b'#define SX_VERSION   "5.3"\nint b;\n')
    old = time.time() - 300
    os.utime(src / "ea" / "MQL5" / "Experts" / "SX_Core.mqh", (old, old))
    plan = u.plan_update(cfg, bot_root)
    assert plan.ea_files == ["MQL5/Experts/SX_Core.mqh"]
    new = u.install_ea(cfg, plan)
    assert new == "5.4"                                    # paquet 5.3 = BASE 5.3 → version incrémentée pour recompiler
    base = cfg.paths.base
    assert (base / "version.txt").read_text() == "5.4"
    assert (base / "version.txt").stat().st_mtime > (base / "MQL5" / "Experts" / "SX_Core.mqh").stat().st_mtime
    from mt5 import build as b
    (base / "MQL5" / "Experts" / "ScalpX_v5.mq5").write_text("x")
    (base / "MetaEditor64.exe").write_bytes(b"MZ")
    t = time.time() - 30
    os.utime(base / "version.txt", (t, t))
    os.utime(base / "MQL5" / "Experts" / "SX_Core.mqh", (t - 5, t - 5))
    from storage.db import Database
    db = Database(tmp_path / "u.db")
    assert b.pending_build(cfg, db).version == "5.4"       # la compilation automatique prend le relais
    db.close()


def test_installation_bot_garde_config_et_secrets(tmp_path, cfg):
    bot_root = tmp_path / "botinst"
    make_pkg(tmp_path, cfg, bot_root)
    plan = u.plan_update(cfg, bot_root)
    u.install_bot(plan, bot_root)
    assert (bot_root / "bot" / "x.py").read_text() == "X = 2"
    assert "C:/perso" in (bot_root / "config.toml").read_text()
    assert not (bot_root / ".env").exists() and not (bot_root / "data").exists()
    assert u.plan_update(cfg, bot_root).empty


def test_bump():
    assert u.bump("5.3") == "5.4" and u.bump("5.9") == "5.10" and u.bump("5") == "6"


def _zip(files: dict[str, bytes]) -> bytes:
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in files.items():
            z.writestr("aigle3-scalpx-abc1234/" + k, v)
    return buf.getvalue()


def test_github_telecharge_le_paquet_une_fois_par_commit(tmp_path, cfg):
    import json
    from core import github as gh
    cfg.update.github, cfg.update.source, cfg.github_token = "aigle3/scalpx", str(tmp_path / "release"), "github_pat_x"
    state = {"sha": "s1", "zips": 0}

    def http(url, headers):
        assert headers["Authorization"] == "Bearer github_pat_x"
        if url.endswith("/repos/aigle3/scalpx/commits/stable"):           # seulement les publications validées
            return 200, json.dumps({"sha": state["sha"], "commit": {"message": "Ajout pause news\n\ndétails", "committer": {"date": "2026-10-28"}}}).encode()
        if "/zipball/" in url:
            state["zips"] += 1
            return 200, _zip({"bot/main.py": b"print(1)", "ea/version.txt": b"5.4", "../evil.txt": b"x"})
        return 404, b""

    r = gh.fetch(cfg, http)
    assert r.ok and r.changed and (tmp_path / "release" / "bot" / "main.py").read_text() == "print(1)"
    assert not (tmp_path / "evil.txt").exists()
    assert u.commit_info(tmp_path / "release")["message"].startswith("Ajout pause news")
    assert gh.fetch(cfg, http).changed is False and state["zips"] == 1         # même commit : rien retéléchargé
    state["sha"] = "s2"
    assert gh.fetch(cfg, http).changed and state["zips"] == 2


def test_github_jeton_refuse(tmp_path, cfg):
    from core import github as gh
    cfg.update.github, cfg.update.source = "aigle3/scalpx", str(tmp_path / "release")
    r = gh.fetch(cfg, lambda url, h: (401, b""))
    assert not r.ok and "jeton" in r.error


def test_fichiers_retires_du_depot_supprimes(tmp_path, cfg):
    bot_root = tmp_path / "botinst"
    make_pkg(tmp_path, cfg, bot_root)
    (bot_root / "scalpx_bot_task.xml").write_text("<old/>")
    (bot_root / "bot" / "ancien.py").write_text("x = 1")
    (bot_root / "data").mkdir()
    (bot_root / "data" / "notes.txt").write_text("à garder")
    plan = u.plan_update(cfg, bot_root)
    assert sorted(plan.bot_remove) == ["bot/ancien.py", "scalpx_bot_task.xml"]
    u.install_bot(plan, bot_root)
    assert not (bot_root / "bot" / "ancien.py").exists() and (bot_root / "data" / "notes.txt").exists()
    assert (bot_root / "config.toml").exists() and u.plan_update(cfg, bot_root).empty


def test_detection_processus_reels():
    import psutil
    from mt5.procs import PsutilProcesses
    p = PsutilProcesses()
    assert p.is_running(psutil.Process().exe()) and not p.is_running("/nulle/part/terminal64.exe")


def test_ping_de_surveillance(cfg):
    from core import monitor
    calls = []
    http = lambda url, h: (calls.append(url) or (200, b"OK"))
    assert monitor.ping(cfg, http) is False and calls == []                        # non configuré : rien envoyé
    cfg.healthcheck_url = "https://hc-ping.com/abc"
    assert monitor.ping(cfg, http) and monitor.ping(cfg, http, fail=True)
    assert calls == ["https://hc-ping.com/abc", "https://hc-ping.com/abc/fail"]
    assert monitor.ping(cfg, lambda u, h: (404, b"")) is False
