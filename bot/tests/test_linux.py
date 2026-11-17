"""Bot 3.15.0 : fonctionnement sous Linux (MT5 par Wine)."""
from __future__ import annotations

from pathlib import Path

import pytest

from mt5 import wine as w
from mt5.procs import PsutilProcesses


def test_chemins_wine_aller_retour(tmp_path):
    p = tmp_path / "BASE" / "terminal64.exe"
    win = w.to_win(p)
    assert win.startswith("Z:\\") and win.endswith("\\BASE\\terminal64.exe") and "/" not in win
    assert w.from_win(win) == str(p)
    assert w.to_win("C:\\deja\\windows.exe") == "C:\\deja\\windows.exe"
    pre = tmp_path / "pfx"
    assert w.from_win("C:\\Program Files\\MT5\\terminal64.exe", pre) == str(pre / "drive_c" / "Program Files" / "MT5" / "terminal64.exe")
    assert w.from_win("/deja/linux") == "/deja/linux"


def test_exe_ligne_windows_entiere_ou_nom_seul():
    """Bot 3.15.1 : formes réelles sous Wine (ligne entière dans un argument, chemin entre guillemets, nom seul)."""
    assert w.cmdline_exe(["Z:\\home\\s\\i1\\terminal64.exe /portable /config:Z:\\x.ini"]) == "/home/s/i1/terminal64.exe"
    assert w.cmdline_exe(['"C:\\Program Files\\MT5\\terminal64.exe" /portable'], Path("/p")) == \
        "/p/drive_c/Program Files/MT5/terminal64.exe"
    assert w.cmdline_exe(["terminal64.exe", "/portable"], cwd="/home/s/i1") == "/home/s/i1/terminal64.exe"


def test_terminal_reconnu_par_son_dossier(monkeypatch, tmp_path):
    exe = tmp_path / "i1" / "terminal64.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    pr = FakeProc(20, "terminal64.exe", [])                     # ligne de commande vide
    pr._cwd = str(exe.parent)
    import psutil
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter([pr]))
    assert PsutilProcesses(wine=w.WineOptions(prefix=tmp_path / "pfx")).is_running(exe)


def test_exe_lu_dans_la_ligne_de_commande():
    assert w.cmdline_exe(["Z:\\home\\scalpx\\mt5\\i1\\terminal64.exe", "/portable"]) == "/home/scalpx/mt5/i1/terminal64.exe"
    assert w.cmdline_exe(["wine", "/home/scalpx/mt5/i1/terminal64.exe", "/portable"]) == "/home/scalpx/mt5/i1/terminal64.exe"
    assert w.cmdline_exe(["/usr/bin/python3", "main.py"]) is None
    assert w.cmdline_exe([]) is None


def test_environnement_et_common_files(tmp_path):
    o = w.WineOptions(prefix=tmp_path / "pfx", display=":42")
    e = w.env(o)
    assert e["WINEPREFIX"] == str(tmp_path / "pfx") and e["DISPLAY"] == ":42" and e["WINEDEBUG"] == "-all"
    cf = w.common_files(o, user="scalpx")
    assert cf == tmp_path / "pfx" / "drive_c" / "users" / "scalpx" / "AppData" / "Roaming" / "MetaQuotes" / "Terminal" / "Common" / "Files"


class FakeProc:
    def __init__(self, pid, name, cmdline):
        self.pid, self.info, self._cmd = pid, {"name": name}, cmdline

    def cmdline(self):
        return self._cmd

    def exe(self):
        return "/usr/lib/wine/wine64-preloader"

    def cwd(self):
        return getattr(self, "_cwd", "/")


def test_terminal_reconnu_sous_wine(monkeypatch, tmp_path):
    exe = tmp_path / "i1" / "terminal64.exe"
    procs = [FakeProc(10, "terminal64.exe", [w.to_win(exe), "/portable"]),
             FakeProc(11, "python3", ["/usr/bin/python3", "main.py"]),
             FakeProc(12, "metatester64.ex", [w.to_win(tmp_path / "bt" / "metatester64.exe")])]
    import psutil
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(procs))
    p = PsutilProcesses(wine=w.WineOptions(prefix=tmp_path / "pfx"))
    assert p.is_running(exe)
    assert not p.is_running(tmp_path / "i2" / "terminal64.exe")
    assert [x.pid for x in p.find(exe)] == [10]
    assert [x.pid for x in p.find(tmp_path / "bt" / "metatester64.exe")] == [12]   # nom tronqué à 15 caractères


def test_lancement_par_wine(monkeypatch, tmp_path):
    seen = {}

    class P:
        pid = 99

    def popen(args, **kw):
        seen["args"], seen["kw"] = args, kw
        return P()

    import subprocess
    monkeypatch.setattr(subprocess, "Popen", popen)
    exe = tmp_path / "i1" / "terminal64.exe"
    ini = tmp_path / "i1" / "scalpx_run.ini"
    p = PsutilProcesses(wine=w.WineOptions(cmd="wine", prefix=tmp_path / "pfx", display=":99"))
    assert p.launch(exe, ini) == 99
    assert seen["args"] == ["wine", str(exe), "/portable", "/config:" + w.to_win(ini)]
    assert seen["kw"]["env"]["WINEPREFIX"] == str(tmp_path / "pfx") and seen["kw"]["start_new_session"] is True


def test_redemarrage_par_systemd(monkeypatch, tmp_path):
    import os
    from core import update as upd
    if os.name == "nt":
        pytest.skip("Linux seulement")
    monkeypatch.setenv("INVOCATION_ID", "abc")
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("aucun processus ne doit être lancé"))
    assert upd.restart_bot(tmp_path) == "systemd"


def test_config_linux(tmp_path):
    import os
    if os.name == "nt":
        pytest.skip("Linux seulement")
    from core.config import load_config
    c = tmp_path / "config.toml"
    c.write_text('[wine]\nprefix = "' + str(tmp_path / "pfx") + '"\n', encoding="utf-8")
    cfg = load_config(c, tmp_path / "absent.env")
    assert str(cfg.paths.common()).startswith(str(tmp_path / "pfx" / "drive_c" / "users"))
    assert cfg.paths.base == Path.home() / "mt5" / "BASE"


def test_menu_systeme_sans_options_windows():
    from bot.system import system_kb
    cbs = [cb for row in system_kb(False, windows=False) for _, cb in row]
    assert "d:vpo" not in cbs and "d:plex" not in cbs and "d:win" not in cbs and "d:rst" in cbs
