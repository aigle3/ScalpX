"""Backtest piloté depuis Telegram (EA 7.2.0) : fichiers du testeur MT5, lecture de l'avancement et des résultats.

Le testeur tourne dans un terminal à part (dossier « backtest », copie de BASE, compte du broker mémorisé).
Fichiers (Common\\Files) échangés avec l'EA lancé dans le testeur :
  bt_<id>_params.cfg      réglages du compte de référence (clé=valeur, comme la commande PARAMS)
  bt_<id>_progress.json   avancement (date simulée, solde, objectifs…) toutes les 3 s
  bt_<id>_fin.json        résultat final
  events_bt_<id>.jsonl    événements (OBJECTIF_*, LABO_*, BT_JOUR, BT_RETRAIT…)
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import labo

PERIODS = [(1, "1 j"), (2, "2 j"), (7, "1 sem"), (30, "1 mois"), (90, "3 mois"), (182, "6 mois"), (274, "9 mois"), (365, "12 mois")]
MODELS = {"reel": (4, "Ticks réels (précis, lent)"), "m1": (1, "OHLC M1 (rapide)")}
MAX_DAYS = 365
TESTER_SET = "scalpx_bt.set"                     # dans MQL5\Profiles\Tester du terminal de backtest
TESTER_INI = "scalpx_bt.ini"


def period_label(days: int) -> str:
    return next((lbl for d, lbl in PERIODS if d == days), f"{days} j")


def parse_date(text: str, now: float | None = None) -> int | None:
    """« 15/03/2026 » (ou 15-03-2026, 15.03.26) → nombre de jours jusqu'à aujourd'hui (1 à 365), sinon None."""
    t = text.strip().replace("-", "/").replace(".", "/")
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            d = datetime.strptime(t, fmt)
            break
        except ValueError:
            d = None
    if d is None:
        return None
    today = datetime.fromtimestamp(now if now is not None else time.time()).replace(hour=0, minute=0, second=0, microsecond=0)
    days = (today - d).days
    return days if 1 <= days <= MAX_DAYS else None


def date_range(days: int, end: float | None = None) -> tuple[int, int]:
    """(début, fin) en secondes GMT : fin = aujourd'hui 00:00 UTC (journée en cours exclue : historique complet)."""
    e = datetime.fromtimestamp(end if end is not None else time.time(), tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((e - timedelta(days=days)).timestamp()), int(e.timestamp())


def mt5_date(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y.%m.%d")


def human_date(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d/%m/%Y")


# ------------------------------------------------------------------ fichiers du testeur
def tester_ini(*, login: int, server: str, password: str = "", ea_name: str, symbol: str, period: str, model: int,
               d_from: int, d_to: int, deposit: float, currency: str, leverage: int, report: str) -> str:
    """.ini du testeur (doc MetaQuotes « Platform Start », section [Tester]). Le mot de passe n'y figure qu'à la
    configuration du terminal ; ensuite MT5 utilise celui qu'il a mémorisé (KeepPrivate=1)."""
    for v in (server, password, symbol, currency):
        if any(c in v for c in "\r\n\x00"):
            raise ValueError("valeur invalide dans le .ini")
    lines = ["; ScalpX - backtest (écrit par le bot)", "[Common]", f"Login={int(login)}", f"Server={server}"]
    if password:
        lines.append(f"Password={password}")
    lines += ["KeepPrivate=1", "NewsEnable=0", "[Experts]", "AllowLiveTrading=0", "AllowDllImport=0", "Enabled=1",
              "[Tester]", f"Expert={ea_name}", f"ExpertParameters={TESTER_SET}", f"Symbol={symbol}", f"Period={period}",
              f"Model={model}", "ExecutionMode=0", "Optimization=0", f"FromDate={mt5_date(d_from)}", f"ToDate={mt5_date(d_to)}",
              "ForwardMode=0", f"Deposit={deposit:.2f}", f"Currency={currency}", f"Leverage=1:{int(leverage)}",
              "ProfitInPips=0", "Visual=0", f"Report={report}", "ReplaceReport=1", "ShutdownTerminal=1"]
    return "\r\n".join(lines) + "\r\n"


def set_inputs(data: bytes, values: dict[str, str]) -> bytes:
    """.set (UTF-16 LE + BOM) avec ces inputs remplacés ou ajoutés ; le reste est conservé."""
    text = data[2:].decode("utf-16-le") if data[:2] == b"\xff\xfe" else data.decode("utf-8-sig", errors="replace")
    out, seen = [], set()
    for line in text.split("\r\n") if "\r\n" in text else text.split("\n"):
        name = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith(";") else ""
        if name in values:
            line = f"{name}={values[name]}"
            seen.add(name)
        out.append(line)
    while out and out[-1] == "":
        out.pop()
    out += [f"{k}={v}" for k, v in values.items() if k not in seen] + [""]
    return b"\xff\xfe" + "\r\n".join(out).encode("utf-16-le")


def params_cfg(values: dict[str, str]) -> str:
    return "".join(f"{k}={v}\n" for k, v in values.items() if k and v not in ("", None))


def bt_files(common: Path, rid: int) -> dict[str, Path]:
    c = Path(common)
    return {"params": c / f"bt_{rid}_params.cfg", "progress": c / f"bt_{rid}_progress.json", "fin": c / f"bt_{rid}_fin.json",
            "events": c / f"events_bt_{rid}.jsonl", "etat": c / f"etat_bt_{rid}.json"}


def read_json(p: Path) -> dict | None:
    try:
        d = json.loads(Path(p).read_text(encoding="utf-8-sig"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def read_events(p: Path) -> list[dict]:
    try:
        text = Path(p).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        line = line.strip().lstrip("﻿")
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def tail_log(folder: Path, max_bytes: int = 6000) -> str:
    """Dernière ligne utile des journaux du terminal de backtest (testeur, agents, terminal) : « où en est MT5 ? »."""
    best: Path | None = None
    for sub in ("Tester/logs", "logs"):
        d = Path(folder) / sub
        for p in list(d.glob("*.log")) if d.is_dir() else []:
            if best is None or p.stat().st_mtime > best.stat().st_mtime:
                best = p
    agents = Path(folder) / "Tester"
    if agents.is_dir():
        for p in agents.glob("Agent-*/logs/*.log"):
            if best is None or p.stat().st_mtime > best.stat().st_mtime:
                best = p
    if best is None:
        return ""
    try:
        with open(best, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            raw = f.read()
    except OSError:
        return ""
    if raw[:2] == b"\xff\xfe" or raw.count(b"\x00") > len(raw) // 4:
        if len(raw) % 2:
            raw = raw[1:]
        text = raw.decode("utf-16-le", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace")
    lines = [l.strip() for l in text.replace("﻿", "").splitlines() if l.strip()]
    if not lines:
        return ""
    last = lines[-1]
    parts = last.split("\t")
    return (parts[-2] + " : " + parts[-1] if len(parts) >= 5 else last)[-160:]


# ------------------------------------------------------------------ résultats
@dataclass
class Result:
    rid: int
    d_from: int
    d_to: int
    deposit: float = 0.0
    final: float = 0.0
    withdrawn: float = 0.0
    reloaded: float = 0.0
    reloads: int = 0
    cycles: int = 0
    goals: int = 0
    won: int = 0
    cancelled: int = 0
    cut: int = 0
    sl: int = 0
    skipped: int = 0
    dd_max: float = 0.0
    note: str = ""
    days: list = field(default_factory=list)        # [ts, solde, equity] par jour simulé
    slots: dict = field(default_factory=dict)       # créneau → [lancés, atteints]

    @property
    def net(self) -> float:
        """Gain réel de la stratégie : solde final + retraits − dépôt initial − recharges."""
        return self.final + self.withdrawn - self.deposit - self.reloaded

    @property
    def missed(self) -> int:
        return self.cancelled + self.cut

    def to_json(self) -> str:
        d = self.__dict__.copy()
        return json.dumps(d, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "Result | None":
        try:
            d = json.loads(text)
            return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        except (TypeError, ValueError):
            return None


def summarize(rid: int, d_from: int, d_to: int, fin: dict | None, events: list[dict]) -> Result:
    r = Result(rid, d_from, d_to)
    f = fin or {}
    r.deposit = float(f.get("depart", 0) or 0)
    r.final = float(f.get("solde", 0) or 0)
    r.withdrawn = float(f.get("retraits", 0) or 0)
    r.reloaded = float(f.get("recharges", 0) or 0)
    r.reloads = int(f.get("nb_recharges", 0) or 0)
    r.cycles = int(f.get("cycles", 0) or 0)
    r.dd_max = float(f.get("dd_max", 0) or 0)
    r.note = str(f.get("note", "") or "")
    current = ""
    for e in events:
        t, d = e.get("type", ""), e.get("data") or {}
        if t == "OBJECTIF_AUTO":
            r.goals += 1
            current = str(d.get("creneau", ""))
            r.slots.setdefault(current, [0, 0])[0] += 1
        elif t == "OBJECTIF_ATTEINT":
            r.won += 1
            if current in r.slots:
                r.slots[current][1] += 1
            current = ""
        elif t == "OBJECTIF_ANNULE":
            r.cancelled += 1
            current = ""
        elif t == "OBJECTIF_COUPE":
            r.cut += 1
            current = ""
        elif t == "CRENEAU_SAUTE":
            r.skipped += 1
        elif t == "NOTIF" and "STOP LOSS D'URGENCE" in (e.get("texte") or ""):
            r.sl += 1
            current = ""
        elif t == "BT_JOUR":
            r.days.append([int(e.get("ts", 0)), float(d.get("solde", 0) or 0), float(d.get("equity", 0) or 0)])
        elif t == "BT_DEBUT" and not r.deposit:
            r.deposit = float(d.get("solde", 0) or 0)
    if not fin and r.days:
        r.final = r.days[-1][1]
    return r


def labo_rows(events: list[dict]) -> list[tuple[int, str, str, str]]:
    """(ts, kind, sc, json) des événements LABO_SERIE / LABO_REEL du backtest, pour la table du labo."""
    out = []
    for e in events:
        t = e.get("type", "")
        if t in ("LABO_SERIE", "LABO_REEL"):
            d = e.get("data") or {}
            out.append((int(e.get("ts", 0) or 0), t[5:], str(d.get("sc", "")), json.dumps(d, ensure_ascii=False)))
    return out


def progress_frac(p: dict | None, d_from: int, d_to: int) -> float:
    if not p or d_to <= d_from:
        return 0.0
    sim = int(p.get("sim", 0) or 0)
    return max(0.0, min(1.0, (sim - d_from) / (d_to - d_from)))


def scenario_table(events: list[dict]) -> dict[str, labo.ScenStat]:
    return labo.compute_scen([e.get("data") or {} for e in events if e.get("type") == "LABO_SERIE"])
