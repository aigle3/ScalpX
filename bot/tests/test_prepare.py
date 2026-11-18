"""Bot 4.5.0 : BASE « prêt » = MT5 a fini sa compilation de départ ; création de comptes refusée avant."""
from pathlib import Path
from types import SimpleNamespace

from mt5 import prepare


def _cfg(tmp_path):
    base = tmp_path / "BASE"
    (base / "logs").mkdir(parents=True)
    (base / "terminal64.exe").write_bytes(b"x" * 10)
    return SimpleNamespace(paths=SimpleNamespace(base=base), mt5=SimpleNamespace(terminal_exe="terminal64.exe"),
                           wine=SimpleNamespace(cmd="wine", options=lambda: SimpleNamespace(
                               prefix="", display="", webview2=False, cmd="wine")))


def _journal(base: Path, *lines: str) -> None:
    p = base / "logs" / "20261118.log"
    old = p.read_bytes().decode("utf-16") if p.exists() else ""
    p.write_bytes(("﻿" if not old else "") .encode("utf-16-le") * 0 + (old + "".join(l + "\r\n" for l in lines)).encode("utf-16"))


class _Proc:
    def __init__(self): self.stopped = False
    def terminate(self): self.stopped = True


def _run(cfg, steps, monkeypatch):
    """Le journal évolue à chaque attente (steps[i] = lignes ajoutées après la i-ème attente)."""
    from mt5 import wine
    monkeypatch.setattr(wine, "env", lambda opts: {})
    t = [0.0]
    it = iter(steps)

    def sleep(s):
        t[0] += s
        try:
            add = next(it)
        except StopIteration:
            add = ()
        if add:
            _journal(cfg.paths.base, *add)
    proc = _Proc()
    ok, msg = prepare.prepare(cfg, timeout=600, poll=5, launcher=lambda cmd, env: proc, clock=lambda: t[0], sleep=sleep)
    return ok, msg, proc


def test_prepare_attend_la_fin_de_la_compilation_de_depart(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    base = cfg.paths.base
    _journal(base, "Compiler\tfull recompilation has been finished: 3 file(s) compiled")   # ancienne compilation : ignorée
    assert not prepare.is_ready(base, "terminal64.exe")
    ok, msg, proc = _run(cfg, [(), ("Compiler\tfull recompilation has been started",), (), (),
                               ("Compiler\tfull recompilation has been finished: 131 file(s) compiled",)], monkeypatch)
    assert ok and "terminée" in msg and proc.stopped and prepare.is_ready(base, "terminal64.exe")
    (base / "terminal64.exe").write_bytes(b"y" * 11)                  # MT5 remplacé : à refaire
    assert not prepare.is_ready(base, "terminal64.exe")


def test_prepare_rien_a_compiler_ou_trop_long(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    ok, msg, _ = _run(cfg, [], monkeypatch)                            # aucune compilation annoncée en 2 min
    assert ok and msg == "rien à compiler" and prepare.is_ready(cfg.paths.base, "terminal64.exe")
    cfg2 = _cfg(tmp_path / "b")
    ok, msg, proc = _run(cfg2, [("full recompilation has been started",)], monkeypatch)   # jamais finie
    assert not ok and "non terminée" in msg and proc.stopped and not prepare.is_ready(cfg2.paths.base, "terminal64.exe")


def test_creation_refusee_tant_que_base_pas_prete(tmp_path, monkeypatch):
    from mt5 import base as basemod
    from mt5.creator import InstanceCreator
    cr = InstanceCreator.__new__(InstanceCreator)
    cr.cfg = SimpleNamespace(paths=SimpleNamespace(base=tmp_path, instances=tmp_path), mt5=SimpleNamespace(terminal_exe="t.exe"),
                             alerts=SimpleNamespace(min_disk_gb=0))
    cr.db = SimpleNamespace(exists_active=lambda iid: False)
    monkeypatch.setattr(basemod, "check_base", lambda cfg: [])
    msg = cr.precheck("12345", "Demo-Server")
    assert msg and "MT5 se prépare" in msg
