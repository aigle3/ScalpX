"""Terminal de backtest (à part) : configuration du compte, lancement du testeur MT5, suivi de l'avancement.

Le terminal de backtest est une copie de BASE dans son propre dossier (jamais surveillé par le watchdog, jamais
d'EA sur un graphique : il ne sert qu'au testeur). Le mot de passe n'existe que dans le .ini de la première
configuration, supprimé dans tous les cas ; MT5 le mémorise ensuite (KeepPrivate=1).
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from core import backtest as bt
from core.config import Config
from core.fsio import atomic_write, safe_remove
from storage.db import Database
from . import base as basemod
from .ini import mask
from .procs import ProcessAPI

log = logging.getLogger(__name__)

META_ACCOUNT = "bt_account"


@dataclass
class Run:
    rid: int
    kind: str                      # "bt" (backtest demandé) ou "labo" (complément du labo des filtres)
    d_from: int
    d_to: int
    model: str
    source: str                    # libellé des réglages utilisés
    started: float = field(default_factory=time.time)
    stage: str = "📋 Préparation"
    log: list = field(default_factory=list)          # [(heure, texte)] : journal visible dans Telegram
    progress: dict | None = None
    last_line: str = ""
    cancelled: bool = False
    done: bool = False
    ok: bool = False
    error: str = ""
    result: bt.Result | None = None
    sim_started: float = 0.0       # première date simulée vue (début réel du test, après l'historique)

    def note(self, text: str) -> None:
        self.log.append((time.strftime("%H:%M:%S"), text))
        self.log = self.log[-8:]
        self.stage = text


Notify = Callable[[Run], Awaitable[None]]


async def _noop(_: Run) -> None:
    return None


class BacktestManager:
    def __init__(self, cfg: Config, db: Database, procs: ProcessAPI) -> None:
        self.cfg, self.db, self.procs = cfg, db, procs
        self.run: Run | None = None
        self._lock = asyncio.Lock()

    # ---- emplacement et compte
    @property
    def folder(self) -> Path:
        return Path(self.cfg.backtest.folder) if self.cfg.backtest.folder else self.cfg.paths.instances.parent / "backtest"

    @property
    def exe(self) -> Path:
        return self.folder / self.cfg.mt5.terminal_exe

    def account(self) -> dict | None:
        try:
            d = json.loads(self.db.meta_get(META_ACCOUNT, "") or "{}")
        except ValueError:
            return None
        return d if d.get("login") and d.get("server") else None

    def configured(self) -> bool:
        return self.account() is not None and self.exe.is_file()

    def busy(self) -> bool:
        return self.run is not None and not self.run.done

    def is_running(self) -> bool:
        return self.procs.is_running(self.exe)

    async def _kill(self) -> None:
        for name in (self.cfg.mt5.terminal_exe, "metatester64.exe"):
            try:
                await asyncio.to_thread(self.procs.kill, self.folder / name)
            except Exception:
                log.exception("backtest : arrêt de %s", name)

    # ---- préparation du dossier
    def _sync_ea(self) -> None:
        """EA compilé et sources de BASE → terminal de backtest (toujours la version installée sur le VPS)."""
        src = self.cfg.paths.base / "MQL5" / "Experts"
        dst = self.folder / "MQL5" / "Experts"
        dst.mkdir(parents=True, exist_ok=True)
        for p in src.iterdir() if src.is_dir() else []:
            if p.is_file() and p.suffix.lower() in (".ex5", ".mq5", ".mqh"):
                shutil.copy2(p, dst / p.name)

    def _write_set(self, rid: int, comm: float, slip: float) -> None:
        base_set = self.cfg.paths.base / "MQL5" / "Presets" / self.cfg.mt5.set_name
        data = base_set.read_bytes() if base_set.is_file() else b"\xff\xfe"
        vals = {"BacktestId": str(rid), "UseSavedParams": "false", "BtGmtHiver": str(self.cfg.backtest.gmt_winter),
                "BtHeureEteUS": "true" if self.cfg.backtest.us_dst else "false",
                "BtCommissionLot": f"{max(0.0, comm):.4f}", "BtSlippagePts": f"{slip:.2f}"}
        d = self.folder / "MQL5" / "Profiles" / "Tester"
        d.mkdir(parents=True, exist_ok=True)
        (d / bt.TESTER_SET).write_bytes(bt.set_inputs(data, vals))

    def _ini(self, run: Run, acc: dict, password: str, deposit: float, currency: str, leverage: int) -> Path:
        content = bt.tester_ini(login=int(acc["login"]), server=acc["server"], password=password, ea_name=self.cfg.mt5.ea_name,
                                symbol=self.cfg.mt5.symbol, period=self.cfg.mt5.period, model=bt.MODELS[run.model][0],
                                d_from=run.d_from, d_to=run.d_to, deposit=deposit, currency=currency, leverage=leverage,
                                report=f"reports\\bt_{run.rid}")
        p = self.folder / bt.TESTER_INI
        atomic_write(p, content)
        return p

    # ---- exécution d'un test (configuration comprise)
    async def execute(self, run: Run, *, params: dict[str, str], deposit: float, currency: str, leverage: int,
                      comm: float = 0.0, slip: float = 0.0, password: str = "", acc: dict | None = None,
                      notify: Notify = _noop, poll_s: float = 4.0) -> Run:
        """Lance le testeur et suit l'avancement jusqu'à la fin (notify(run) à chaque étape et toutes les poll_s)."""
        async with self._lock:
            self.run = run
            try:
                await self._execute(run, params, deposit, currency, leverage, comm, slip, password, acc or self.account() or {},
                                    notify, poll_s)
            except Exception as exc:
                log.error("backtest #%s : %s", run.rid, mask(repr(exc), password))
                run.error = "Erreur inattendue : " + mask(str(exc), password)
            finally:
                safe_remove(self.folder / bt.TESTER_INI)          # contient le mot de passe à la configuration
                run.done = True
                run.ok = run.ok and not run.error and not run.cancelled
                await notify(run)
            return run

    async def _execute(self, run, params, deposit, currency, leverage, comm, slip, password, acc, notify, poll_s) -> None:
        if not acc:
            run.error = "Terminal de backtest non configuré."
            return
        files = bt.bt_files(self.cfg.paths.common(), run.rid)
        for p in files.values():
            safe_remove(p)
        if not self.exe.is_file():
            problems = basemod.check_base(self.cfg)
            if problems:
                run.error = "BASE non conforme : " + " ; ".join(problems)
                return
            run.note("📁 Copie de BASE vers le terminal de backtest…")
            await notify(run)
            if self.folder.exists():
                shutil.rmtree(self.folder, ignore_errors=True)
            await asyncio.to_thread(basemod.copy_base, self.cfg.paths.base, self.folder)
        run.note("📋 Préparation : EA de BASE, réglages, fichier du testeur")
        await notify(run)
        await asyncio.to_thread(self._sync_ea)
        atomic_write(files["params"], bt.params_cfg(params))
        self._write_set(run.rid, comm, slip)
        ini = self._ini(run, acc, password, deposit, currency, leverage)
        if self.is_running():
            run.note("🧹 Terminal de backtest déjà ouvert : fermeture")
            await notify(run)
            await self._kill()
        run.note(f"🚀 Lancement du testeur ({bt.MODELS[run.model][1]}, {bt.human_date(run.d_from)} → {bt.human_date(run.d_to)})")
        await notify(run)
        self.procs.launch(self.exe, ini)
        await self._monitor(run, files, notify, poll_s)
        if run.error:
            return
        if run.cancelled:
            await self._kill()
            run.error = "Arrêté à ta demande."
            return
        fin = bt.read_json(files["fin"])
        events = bt.read_events(files["events"])
        if fin is None:
            line = bt.tail_log(self.folder)
            run.error = "Le testeur s'est arrêté sans résultat" + (f" (dernier message de MT5 : {line})" if line else "") + "."
            return
        if bt.stopped_early(fin, run.d_to):                    # Bot 3.1.1 : test incomplet → pas d'analyse trompeuse
            sim = int(fin.get("sim", 0))
            rs = fin.get("raison")
            why = bt.DEINIT.get(int(rs), f"code {rs}") if isinstance(rs, (int, float)) else "inconnue"
            errs = await asyncio.to_thread(bt.log_errors, self.folder)
            line = bt.tail_log(self.folder)
            run.error = (f"le test s'est arrêté au {bt.human_date(sim)} au lieu du {bt.human_date(run.d_to)} "
                         f"(raison : {why} ; solde {float(fin.get('solde', 0) or 0):.2f} ; stop-outs {int(fin.get('stopouts', 0) or 0)}"
                         + (f" ; marge mini {float(fin['marge_min']):.0f} %" if fin.get("marge_min") else "")
                         + f" ; EA {fin.get('ea', '< 7.8.2')})"
                         + (" — MT5 : " + " | ".join(errs) if errs else (f" — MT5 : {line}" if line else "")))
            log.warning("backtest #%s incomplet : %s", run.rid, run.error)
            return
        run.note("📊 Analyse des résultats")
        await notify(run)
        run.result = bt.summarize(run.rid, run.d_from, run.d_to, fin, events)
        if run.kind != "setup":
            for ts, kind, sc, data in bt.labo_rows(events):
                self.db.add_labo(f"bt_{run.rid}", ts, kind, sc, data)
        run.ok = True
        for k in ("params", "progress", "fin", "events", "etat"):
            safe_remove(files[k])

    async def _monitor(self, run: Run, files: dict[str, Path], notify: Notify, poll_s: float) -> None:
        """Suit le terminal : journal de MT5 (connexion, historique), puis l'avancement écrit par l'EA."""
        mins = self.cfg.backtest.setup_stall_min if run.kind == "setup" else self.cfg.backtest.stall_min
        stall_s = max(60, mins * 60)
        last_change = time.monotonic()
        last_sig = ""
        seen_proc = False
        t0 = time.monotonic()
        while True:
            await asyncio.sleep(poll_s)
            if run.cancelled:
                return
            running = self.is_running()
            seen_proc = seen_proc or running
            if running and self.cfg.backtest.low_priority and hasattr(self.procs, "lower_priority"):
                n = 0
                for name in (self.cfg.mt5.terminal_exe, "metatester64.exe"):
                    try:
                        n += await asyncio.to_thread(self.procs.lower_priority, self.folder / name,
                                                     getattr(self.cfg.backtest, "free_cores", 1))
                    except Exception:
                        log.exception("backtest : priorité de %s", name)
                if n and not getattr(run, "_low_noted", False):
                    run._low_noted = True
                    run.note("🐢 Testeur en priorité basse : les comptes réels gardent la main sur le processeur")
            p = bt.read_json(files["progress"])
            line = await asyncio.to_thread(bt.tail_log, self.folder)
            if p is not None:
                if run.progress is None:
                    run.sim_started = time.time()
                    run.note("⚙️ Test en cours (l'EA tourne dans le testeur)")
                run.progress = p
            elif line and line != run.last_line and run.progress is None:   # avant le début du test seulement
                run.stage = "📥 MT5 : connexion / historique"
            run.last_line = line
            low = line.lower()
            if "authoriz" in low and ("fail" in low or "invalid" in low):
                run.error = "Identifiants refusés par le serveur (mot de passe ou serveur incorrect)."
                await self._kill()
                return
            sig = json.dumps(p, sort_keys=True) + line
            if sig != last_sig:
                last_sig, last_change = sig, time.monotonic()
            await notify(run)
            if not running and (seen_proc or time.monotonic() - t0 > 60):
                return                                              # fin du test (ShutdownTerminal=1) ou échec
            if time.monotonic() - last_change > stall_s:
                run.error = f"Aucun progrès depuis {mins} min" + (f" (MT5 : {line})" if line else "") + "."
                await self._kill()
                return

    # ---- configuration du terminal (compte + mot de passe) : un test d'une journée en OHLC M1
    async def setup(self, login: int, server: str, password: str, *, currency: str = "EUR", notify: Notify = _noop,
                    poll_s: float = 4.0) -> Run:
        acc = {"login": int(login), "server": server}
        d_from, d_to = bt.date_range(3)
        run = Run(0, "setup", d_from, d_to, "m1", "vérification")
        run = await self.execute(run, params={}, deposit=125.0, currency=currency, leverage=self.cfg.backtest.leverage,
                                 password=password, acc=acc, notify=notify, poll_s=poll_s)
        if run.ok:
            self.db.meta_set(META_ACCOUNT, json.dumps({**acc, "ok_ts": int(time.time())}))
        return run

    def cancel(self) -> bool:
        if not self.busy():
            return False
        self.run.cancelled = True
        return True
