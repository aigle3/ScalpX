"""Bot 3.10.0 : ⚡ Optimiser le VPS et allègements du bot (statuts en cache, MaxBars, attente des acks)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from core import vpsopt
from core.config import Config


def test_parse_output_ignore_le_bruit():
    out = "﻿{\"k\":\"admin\",\"s\":\"ok\",\"d\":\"\"}\r\nAVERTISSEMENT : texte\r\n{\"k\":\"defender\",\"s\":\"deja\"}\r\n{casse\r\n"
    items = vpsopt.parse_output(out)
    assert [(i.key, i.state) for i in items] == [("admin", "ok"), ("defender", "deja")]


def test_rapport_admin_et_non_admin():
    rep = vpsopt.Report(items=[vpsopt.Item("admin", "non"), vpsopt.Item("defender", "admin"), vpsopt.Item("visual", "ok")],
                        cpu_before=95, cpu_after=80, top=[("ServerManager.exe", 4.0, 120.0)])
    txt = vpsopt.report_text(rep)
    assert "droits administrateur requis" in txt and "Set-ScheduledTask" in txt
    assert "95 % avant → 80 % après" in txt and "ServerManager.exe" in txt
    rep.items[0] = vpsopt.Item("admin", "ok")
    assert "Set-ScheduledTask" not in vpsopt.report_text(rep)


def test_dossiers_exclus(tmp_path):
    cfg = Config()
    cfg.paths.common_files = tmp_path / "MetaQuotes" / "Terminal" / "Common" / "Files"
    paths = vpsopt.exclusion_paths(cfg)
    assert str(tmp_path / "MetaQuotes") in paths and str(cfg.paths.instances) in paths
    assert len(paths) == len(set(paths))


def test_script_powershell_present_et_encode():
    raw = (vpsopt.BOT_ROOT / "vps_optimize.ps1").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    txt = raw.decode("utf-8")
    for k in ("defender", "wsearch", "servermanager", "power", "visual", "task"):
        assert f"'{k}'" in txt


def test_run_script_hors_windows():
    if os.name != "nt":
        assert vpsopt.run_script(Config()) == ([], "réservé à Windows")


def test_maxbars_dans_les_ini():
    from mt5.ini import build_ini, build_run_ini
    assert "MaxBars=5000" in build_run_ini(1, "S", "E", "s.set", "XAUUSD", "M5", "", 5000)
    ini = build_ini(1, "pw", "S", "E", "s.set", "XAUUSD", "M5", "ScalpX", 5000)
    assert "[Charts]\r\nProfileLast=ScalpX\r\nMaxBars=5000" in ini
    assert "MaxBars" not in build_run_ini(1, "S", "E", "s.set", "XAUUSD", "M5", "ScalpX")
    assert Config().mt5.max_bars == 5000


def test_statut_garde_en_cache_tant_que_le_fichier_ne_change_pas(tmp_path, monkeypatch):
    from core import status as stmod
    from core.fsio import atomic_write
    r = stmod.StatusReader(tmp_path)
    calls = []
    real = stmod.read_json
    monkeypatch.setattr(stmod, "read_json", lambda p: calls.append(p) or real(p))
    base = {"id": "1_s", "ts": int(time.time()), "etat": "ACTIF", "solde": 250.0, "equity": 250.0}
    atomic_write(r.path("1_s"), json.dumps(base))
    a = r.read("1_s")
    b = r.read("1_s")
    assert a is not None and a is b and len(calls) == 1          # 2e lecture : fichier inchangé, pas relu
    atomic_write(r.path("1_s"), json.dumps({**base, "solde": 260.5}))
    assert r.read("1_s").solde == 260.5 and len(calls) == 2
    r.remove("1_s")
    assert r.read("1_s") is None


def test_bouton_optimiser_dans_systeme():
    from bot.system import system_kb
    assert ("⚡ Optimiser le VPS", "d:vpo") in [b for row in system_kb(False) for b in row]
