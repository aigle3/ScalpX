"""Bot 4.6.1 : détection des terminaux avec les lignes de commande RÉELLES vues sous Wine devel 11.x (htop du VPS,
18/11/2026). Un terminal non reconnu = « terminal absent » → relancé ou supprimé à tort : jamais."""
from pathlib import Path

import pytest

from mt5 import procs as pr

INST = "/home/scalpx/mt5/instances"
A = f"{INST}/52030694_RoboForex_Pro"
B = f"{INST}/52030697_RoboForex_Pro"
ZA = "Z:\\home\\scalpx\\mt5\\instances\\52030694_RoboForex_Pro"


class P:
    def __init__(self, pid, name, cmdline, cwd=None):
        self.pid, self.info, self._c, self._cwd = pid, {"name": name, "uids": None}, cmdline, cwd

    def cmdline(self):
        return self._c

    def cwd(self):
        if self._cwd is None:
            raise OSError("illisible")
        return self._cwd


VUS = [
    # titre réécrit par Wine : tout dans un seul argument (affichage htop)
    P(1, "main", [f"{A}/terminal64.exe /portable /config:{ZA}\\scalpx_run.ini"]),
    # chargeur Wine devel : « wine » puis le chemin Linux du terminal
    P(2, "wine", ["/opt/wine-devel/lib/wine/x86_64-unix/wine", f"{B}/terminal64.exe", "/portable"]),
    # MetaEditor du compte (compilation) : chemin Windows Z:\ — ce n'est PAS le terminal
    P(3, "main", [f"{ZA}\\metaeditor64.exe /portable /compile:{ZA}\\MQL5"]),
    # services Wine : ignorés
    P(4, "wineserver", ["/opt/wine-devel/bin/wineserver"]),
    P(5, "main", ["C:\\windows\\system32\\winedevice.exe"]),
]


def test_terminaux_reconnus_avec_wine_devel(monkeypatch, tmp_path):
    monkeypatch.setattr(pr, "own_procs", lambda *a: iter(VUS))
    ps = pr.PsutilProcesses()
    assert ps.is_running(Path(A) / "terminal64.exe")
    assert ps.is_running(Path(B) / "terminal64.exe")
    assert not ps.is_running(Path(INST) / "99999_Autre" / "terminal64.exe")
    ps._invalidate()
    assert ps.is_running(Path(A) / "metaeditor64.exe")               # MetaEditor vu à part, jamais pris pour le terminal


def test_ligne_de_commande_illisible_dossier_courant(monkeypatch, tmp_path):
    inst = tmp_path / "instances" / "52030694_RoboForex_Pro"
    inst.mkdir(parents=True)
    (inst / "terminal64.exe").write_bytes(b"")
    monkeypatch.setattr(pr, "own_procs", lambda *a: iter([P(7, "main", [], cwd=str(inst))]))
    ps = pr.PsutilProcesses()
    assert ps.is_running(inst / "terminal64.exe")


@pytest.mark.skipif(not Path("/proc/self/cmdline").exists(), reason="Linux seulement")
def test_vrai_processus_linux(tmp_path):
    """Un vrai processus dont la ligne de commande imite le terminal Wine (dossier d'instance, chemin Linux)."""
    import subprocess, sys, time
    inst = tmp_path / "instances" / "1_Demo"
    inst.mkdir(parents=True)
    exe = inst / "terminal64.exe"
    exe.write_text("#!/bin/sh\nsleep 30\n")
    exe.chmod(0o755)
    p = subprocess.Popen([str(exe), "/portable"], cwd=str(inst))
    try:
        time.sleep(0.5)
        ps = pr.PsutilProcesses()
        assert ps.is_running(exe)
    finally:
        p.kill()
