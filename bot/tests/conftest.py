"""Fixtures : configuration temporaire, FakeProcs (faux terminaux) et FakeEA (simule les fichiers de l'EA)."""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.commands import CommandQueue                      # noqa: E402
from core.config import Config, Paths, MT5, Timing, Alerts   # noqa: E402
from core.fsio import atomic_write                           # noqa: E402
from core.ids import make_id                                 # noqa: E402
from core.status import StatusReader                         # noqa: E402
from mt5.manager import InstanceManager                      # noqa: E402
from mt5.procs import norm_path                              # noqa: E402
from storage.db import Database                              # noqa: E402


class FakeProcs:
    """Simule terminal64.exe : lancement/arrêt, sans processus réel."""

    def __init__(self) -> None:
        self.running: set[str] = set()
        self.run_inis: list[str | None] = []
        self.launches: list[tuple[str, bool]] = []          # (dossier, avec_ini)
        self.kills = 0
        self.on_launch = None

    def is_running(self, exe: Path) -> bool:
        return norm_path(exe) in self.running

    def launch(self, exe: Path, ini: Path | None = None) -> int:
        self.running.add(norm_path(exe))
        self.launches.append((Path(exe).parent.name, ini is not None and Path(ini).name == "scalpx_start.ini"))   # True = .ini de création
        self.run_inis.append(Path(ini).read_text() if ini is not None and Path(ini).name == "scalpx_run.ini" else None)
        if self.on_launch:
            self.on_launch(Path(exe), ini)
        return 1234

    def kill(self, exe: Path) -> int:
        self.kills += 1
        was = norm_path(exe) in self.running
        self.running.discard(norm_path(exe))
        return int(was)


DEFAULT_PARAMS = {"lot": "0.01 lot", "f_vol": "❌", "news_block": "✅", "tz": "France (Paris)", "fast": "10"}


class FakeEA:
    """Fait vivre les fichiers de l'EA : heartbeat, commandes → ack, événements."""

    def __init__(self, cfg: Config, procs: FakeProcs, password_ok: bool = True) -> None:
        self.cfg, self.procs = cfg, procs
        self.common = cfg.paths.common()
        self.accounts: dict[str, dict] = {}
        self.password_ok = password_ok
        self.task: asyncio.Task | None = None
        self.ini_seen: list[str] = []
        self._args: list = []
        self.charts = 1
        self.currency = "EUR"
        procs.on_launch = self._on_launch

    def _iid_of(self, exe: Path) -> str:
        return Path(exe).parent.name

    def _on_launch(self, exe: Path, ini: Path | None) -> None:
        iid = self._iid_of(exe)
        acc = self.accounts.setdefault(iid, {"etat": "ACTIF", "cycle": 0, "solde": 130.0, "positions": 0,
                                             "maintenance": False, "phase500": False, "connecte": True})
        acc["maintenance"] = False                         # non persisté
        if ini is not None and Path(ini).name == "scalpx_start.ini":          # .ini de création (avec identifiants)
            self.ini_seen.append(Path(ini).read_text())
            acc["connecte"] = self.password_ok
        ex5 = Path(exe).parent / "MQL5" / "Experts" / self.cfg.mt5.ea_file
        acc["version"] = ex5.read_text().strip() if ex5.exists() else "?"
        acc["crash"] = acc["version"].startswith("BAD")

    def statut_for(self, iid: str, acc: dict) -> dict:
        login, _, server = iid.partition("_")
        return {"id": iid, "login": int(login), "serveur": acc.get("server", server), "devise": self.currency,
                "solde": acc["solde"], "equity": acc["solde"], "solde_plus_flottant_ea": acc["solde"],
                "positions": acc["positions"], "etat": acc["etat"], "cycle": acc["cycle"],
                "phase_cloture500": acc["phase500"], "maintenance": acc["maintenance"],
                "connecte": acc["connecte"], "algo_terminal": True, "algo_ea": True, "version": acc["version"],
                "nb_graphiques_ea": self.charts, "seuil_arret": 500, "seuil_reprise": 125,
                "ts": int(time.time())}

    async def _loop(self) -> None:
        while True:
            for iid, acc in list(self.accounts.items()):
                exe = Path(self.cfg.paths.instances) / iid / self.cfg.mt5.terminal_exe
                if norm_path(exe) not in self.procs.running or acc.get("crash"):
                    continue
                atomic_write(self.common / f"statut_{iid}.json", json.dumps(self.statut_for(iid, acc)))
                for p in sorted(self.common.glob(f"cmd_{iid}_*.json")):
                    try:
                        d = json.loads(p.read_text(encoding="utf-8"))
                    except Exception:
                        p.unlink(missing_ok=True)
                        continue
                    p.unlink(missing_ok=True)
                    self._args = d.get("args", [])
                    ok, msg = self._exec(iid, acc, d.get("cmd", ""), exe)
                    if "❌" in msg:                       # comme l'EA (OkFromText) : un « ❌ » dans le texte = échec
                        ok = False
                    n = p.stem.rsplit("_", 1)[1]
                    atomic_write(self.common / f"ack_{iid}_{n}.json",
                                 json.dumps({"id": iid, "n": int(n), "cmd": d.get("cmd"), "ok": ok, "message": msg,
                                             "ts": int(time.time())}))
            await asyncio.sleep(0.05)

    def _exec(self, iid: str, acc: dict, cmd: str, exe: Path) -> tuple[bool, str]:
        if cmd == "PING":
            return True, "pong"
        if cmd == "PARAMS":
            pv = acc.setdefault("params", dict(DEFAULT_PARAMS))
            return True, "📋 LISTE DES PARAMÈTRES\n(modifie-les depuis le bot : ⚙️ Paramètres)\n\n📐 Signal\n" + "\n".join(f"• {k} = {v}" for k, v in pv.items())
        if cmd == "SET":
            args = self._args
            key, val = args[0], args[1]
            pv = acc.setdefault("params", dict(DEFAULT_PARAMS))
            old = pv.get(key, "")
            from core import params as cat
            p = next((x for x in cat.PARAMS if x[0] == key), None)
            if p and p[3] == "enum":
                pv[key] = p[8][int(float(val))]
            elif old in ("✅", "❌") or (p and p[3] == "bool"):
                pv[key] = "✅" if val == "1" else "❌"
            else:
                pv[key] = val + (" lot" if key == "lot" else "")
            return True, f"⚙️ {key}\nValeur : {pv[key]}\n\n✅ Appliqué : {pv[key]}"
        if cmd == "PAUSE":
            acc["etat"] = "PAUSE"
            acc["pause_arg"] = " ".join(self._args)
            return True, "pause" + (f" jusqu'à {acc['pause_arg']}" if self._args else "")
        if cmd == "REPRISE":
            acc["etat"] = "ACTIF"; return True, "reprise"
        if cmd == "MAINTENANCE_ON":
            acc["maintenance"] = True; return True, "maintenance on"
        if cmd == "MAINTENANCE_OFF":
            acc["maintenance"] = False; return True, "maintenance off"
        if cmd == "FERMER_TERMINAL":
            self.procs.running.discard(norm_path(exe)); return True, "fermeture"
        ps = acc.setdefault("presets", ["prudent"])
        if cmd == "PRESETS":
            return True, "💾 PRESETS\nSauvegarde…" + "".join(f"\n• {n}" for n in ps)
        if cmd == "PRESET_SAVE":
            ps.append(self._args[0]); return True, f"✅ Preset « {self._args[0]} » enregistré."
        if cmd == "PRESET_DELETE":
            ps.remove(self._args[0]); return True, f"🗑 Preset « {self._args[0]} » supprimé."
        if cmd == "VACANCES":
            acc["vacances"] = self._args == ["on"]; return True, "🏖 MODE VACANCES " + ("ACTIVÉ" if acc["vacances"] else "DÉSACTIVÉ")
        if cmd == "FERMER_TOUT":
            acc["etat"] = "PAUSE"; return True, "🛑 Tout fermé, robot en PAUSE."
        return True, "ok " + cmd

    def start(self) -> None:
        self.task = asyncio.get_event_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    def event(self, iid: str, typ: str, texte: str = "", prio: int = 1, data: dict | None = None, seq: int = 1) -> None:
        line = {"seq": seq, "ts": int(time.time()), "id": iid, "type": typ, "prio": prio, "silencieux": False,
                "texte": texte}
        if data is not None:
            line["data"] = data
        with open(self.common / f"events_{iid}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    base = tmp_path / "BASE"
    (base / "MQL5" / "Experts").mkdir(parents=True)
    (base / "MQL5" / "Presets").mkdir(parents=True)
    (base / "Profiles" / "Charts" / "Default").mkdir(parents=True)
    (base / "terminal64.exe").write_bytes(b"MZ")
    (base / "MQL5" / "Experts" / "ScalpX_v5.ex5").write_text("5.0")
    (base / "MQL5" / "Presets" / "scalpx_v5.set").write_text("A=1\nB=2\n")
    (base / "version.txt").write_text("5.0")
    (base / "Profiles" / "Charts" / "Default" / "chart01.chr").write_text("<chart>\nsymbol=XAUUSD\n</chart>\n")
    common = tmp_path / "Common"
    common.mkdir()
    return Config(
        paths=Paths(base=base, instances=tmp_path / "instances", archives=tmp_path / "archives",
                    common_files=common, data=tmp_path / "data"),
        mt5=MT5(servers=["Demo-Server 1", "Live-Server"], default_server="Demo-Server 1"),
        timing=Timing(heartbeat_stale_s=5, create_timeout_s=5, stop_wait_s=1, launch_stagger_s=0, ack_timeout_s=2,
                      close500_wait_max_s=2, maintenance_wait_s=2, verify_deploy_s=4),
        alerts=Alerts(min_disk_gb=0.0),
    )


@pytest.fixture
def db(tmp_path: Path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


@pytest.fixture
def stack(cfg, db):
    procs = FakeProcs()
    status = StatusReader(cfg.paths.common())
    cmdq = CommandQueue(cfg.paths.common())
    mgr = InstanceManager(cfg, db, procs, status, cmdq)
    ea = FakeEA(cfg, procs)
    return cfg, db, procs, status, cmdq, mgr, ea


# Exécute les tests `async def` sans dépendre de pytest-asyncio (une seule dépendance de moins).
@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    if asyncio.iscoroutinefunction(pyfuncitem.obj):
        args = {a: pyfuncitem.funcargs[a] for a in pyfuncitem._fixtureinfo.argnames}
        asyncio.run(pyfuncitem.obj(**args))
        return True
    return None
