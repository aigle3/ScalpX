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


def _mtime(p: Path) -> float:
    """Bot 3.3.1 : MT5 supprime ses anciens journaux au démarrage (fichier listé puis disparu) : -1 au lieu d'une erreur."""
    try:
        return p.stat().st_mtime
    except OSError:
        return -1.0


def _newest(paths) -> Path | None:
    best, bt_ = None, -1.0
    for p in paths:
        t = _mtime(p)
        if t > bt_:
            best, bt_ = p, t
    return best


def tail_log(folder: Path, max_bytes: int = 6000) -> str:
    """Dernière ligne utile des journaux du terminal de backtest (testeur, agents, terminal) : « où en est MT5 ? »."""
    cands: list[Path] = []
    try:
        for sub in ("Tester/logs", "logs"):
            d = Path(folder) / sub
            if d.is_dir():
                cands += list(d.glob("*.log"))
        agents = Path(folder) / "Tester"
        if agents.is_dir():
            cands += list(agents.glob("Agent-*/logs/*.log"))
    except OSError:
        return ""
    best = _newest(cands)
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


# Bot 3.1.1 : un test qui s'arrête avant la date de fin ne doit pas être analysé comme s'il était complet
END_MARGIN_S = 3 * 86400            # week-end / jours fériés : les derniers ticks peuvent précéder d_to de 2-3 jours
DEINIT = {0: "arrêt par l'EA", 1: "EA retiré", 2: "EA recompilé", 3: "symbole/période changé", 4: "graphique fermé",
          5: "paramètres modifiés", 6: "compte changé", 7: "modèle appliqué", 8: "échec d'initialisation", 9: "terminal fermé"}
_ERR_WORDS = ("critical", "runtime error", "array out of range", "zero divide", "invalid pointer", "stack overflow",
              "not enough memory", "out of memory")


def stopped_early(fin: dict, d_to: int) -> bool:
    sim = int(fin.get("sim", 0) or 0)
    return 0 < sim < d_to - END_MARGIN_S


def log_errors(folder: Path, n: int = 3, max_bytes: int = 200_000) -> list[str]:
    """Dernières lignes d'erreur des journaux des agents du testeur (erreur d'exécution de l'EA, historique, mémoire)."""
    agents = Path(folder) / "Tester"
    try:
        last = _newest(list(agents.glob("Agent-*/logs/*.log"))) if agents.is_dir() else None
    except OSError:
        last = None
    if last is None:
        return []
    try:
        with open(last, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - max_bytes))
            raw = f.read()
    except OSError:
        return []
    if raw[:2] == b"\xff\xfe" or raw.count(b"\x00") > len(raw) // 4:
        text = raw[len(raw) % 2:].decode("utf-16-le", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace")
    out = []
    for l in text.replace("\ufeff", "").splitlines():
        low = l.lower()
        if any(w in low for w in _ERR_WORDS) or ("error" in low and "0 error" not in low):
            parts = l.strip().split("\t")
            out.append((parts[-1] if len(parts) >= 3 else l.strip())[-200:])
    return out[-n:]


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
    dd_ts: int = 0                                  # EA 7.6.0 : moment du pire drawdown (GMT), equity de référence et au plus bas
    dd_peak: float = 0.0
    dd_eq: float = 0.0
    stopouts: int = -1                              # stop-out du broker (-1 : EA < 7.6.0, inconnu)
    params: dict = field(default_factory=dict)      # réglages testés (clé brute de l'EA → valeur)
    days: list = field(default_factory=list)        # [ts, solde, equity] par jour simulé
    slots: dict = field(default_factory=dict)       # créneau → [lancés, atteints]
    # Bot 4.13.0 / EA 8.1.0 : backtest lancé depuis Telegram (🧪 Backtest)
    marks: list = field(default_factory=list)       # [ts, "+" retrait (cycle gagné) | "-" recharge (cycle perdu), montant, solde après]
    trades: int = -1                                # positions fermées (-1 : EA < 8.1.0, inconnu)
    t_won: int = 0
    t_lost: int = 0
    t_long: int = 0
    t_short: int = 0
    gross_win: float = 0.0
    gross_loss: float = 0.0
    best: float = 0.0
    worst: float = 0.0
    lo: float = 0.0                                 # minimum (seuil de reprise) et maximum (seuil d'arrêt) du test
    hi: float = 0.0

    @property
    def net(self) -> float:
        """Gain réel de la stratégie : solde final + retraits − dépôt initial − recharges."""
        return self.final + self.withdrawn - self.deposit - self.reloaded

    @property
    def cycles_won(self) -> int:
        """Cycles gagnés : maximum atteint, retrait jusqu'au minimum (événements BT_RETRAIT, sinon compteur de l'EA)."""
        n = sum(1 for m in self.marks if m[1] == "+")
        return n if n or not self.cycles else self.cycles

    @property
    def cycles_lost(self) -> int:
        """Cycles perdus : solde passé sous le minimum, compte rechargé au minimum."""
        return self.reloads

    @property
    def profit_factor(self) -> float | None:
        return self.gross_win / self.gross_loss if self.gross_loss > 0 else None

    def cycle_durations(self) -> list[float]:
        """Durée (s) de chaque cycle terminé (gagné ou perdu), du début du test ou de la remise au minimum précédente."""
        out, start = [], self.d_from
        for ts, *_ in self.marks:
            if ts > start:
                out.append(float(ts - start))
            start = ts
        return out

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
    r.dd_ts = int(f.get("dd_ts", 0) or 0)
    r.dd_peak = float(f.get("dd_pic", 0) or 0)
    r.dd_eq = float(f.get("dd_eq", 0) or 0)
    r.stopouts = int(f["stopouts"]) if "stopouts" in f else -1
    if "trades" in f:                                                    # EA 8.1.0
        r.trades = int(f.get("trades", 0) or 0)
        r.t_won, r.t_lost = int(f.get("gagnants", 0) or 0), int(f.get("perdants", 0) or 0)
        r.t_long, r.t_short = int(f.get("longs", 0) or 0), int(f.get("courts", 0) or 0)
        r.gross_win, r.gross_loss = float(f.get("gains_bruts", 0) or 0), float(f.get("pertes_brutes", 0) or 0)
        r.best, r.worst = float(f.get("meilleur", 0) or 0), float(f.get("pire", 0) or 0)
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
        elif t in ("BT_RETRAIT", "BT_RECHARGE"):
            r.marks.append([int(e.get("ts", 0) or 0), "+" if t == "BT_RETRAIT" else "-", float(d.get("montant", 0) or 0),
                            float(d.get("solde", 0) or 0)])
        elif t == "BT_JOUR":
            r.days.append([int(e.get("ts", 0)), float(d.get("solde", 0) or 0), float(d.get("equity", 0) or 0)])
        elif t == "BT_DEBUT" and not r.deposit:
            r.deposit = float(d.get("solde", 0) or 0)
    if not fin and r.days:
        r.final = r.days[-1][1]
    return r


def labo_rows(events: list[dict]) -> list[tuple[int, str, str, str]]:
    """(ts, kind, sc, json) des événements LABO_SERIE / LABO_REEL du backtest (objectifs virtuels de l'optimiseur)."""
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


def _h(params: dict, key: str) -> str:
    from . import presets as ps
    p = ps.BY_KEY.get(key)
    raw = params.get(key)
    if p is None or raw is None:
        return "?"
    return ps.human(p, str(raw)).replace("✅ Activé", "activé").replace("❌ Désactivé", "désactivé")


FILTER_NAMES = [("f_atr", "ATR"), ("f_ema", "Prix/EMA"), ("f_vol", "Volume"), ("f_lo", "EMA inf."), ("f_hi", "EMA sup."),
                ("f_can", "Bougie sup."), ("f_time", "Horaire"), ("news_block", "News"), ("f_ext", "Extension"), ("f_fresh", "Signal récent")]
SHOWN = ["sl_cont", "sl_pct", "sl_m00", "sl_m15", "sl_m30", "sl_m45", "ct_on", "ct_m00", "ct_m15", "ct_m30", "ct_m45", "lot", "lotmult", "tbm",
         "maxpend", "dyngrid", "gridatr", "gridmin", "gridmax", "grid", "atrmult", "tpmin", "tpmax", "emsl", "emslpct", "sesslim",
         "seuil_arret", "seuil_reprise", "freshbars", "extatr", "exttf", "freshnoise"] + [k for k, _ in FILTER_NAMES]


def settings_lines(params: dict) -> list[str]:
    """Réglages testés, en clair (3 lignes)."""
    if not params:
        return ["⚙️ <i>Réglages testés inconnus (backtest d'avant le bot 2.43.0)</i>"]
    from . import presets as ps, params as cat
    v = {k: str(x) for k, x in params.items()}
    slots = ps.slots_input(v) or "?"
    cuts = ps.slots_input(v, cat.CUT_KEYS) if v.get("ct_on") == "1" else "aucune"
    grid = (f"grille {_h(v, 'gridatr')} ({_h(v, 'gridmin')} → {_h(v, 'gridmax')})" if v.get("dyngrid") == "1"
            else f"grille fixe {_h(v, 'grid')}")
    sl = f"SL d'urgence {_h(v, 'emslpct')}" if v.get("emsl") == "1" else "SL d'urgence désactivé"
    flt = [n + (f" ({_h(v, 'freshbars')})" if k == "f_fresh" else "") for k, n in FILTER_NAMES if v.get(k) == "1"]
    when = "mode continu (filtres OK + clôture M5)" if v.get("sl_cont") == "1" else f"créneaux {slots}"
    return [f"🎯 objectif {_h(v, 'sl_pct')} · {when} · coupures {cuts or 'aucune'}",
            f"🧱 {grid} · {_h(v, 'maxpend')} ordres max · lot {v.get('lot', '?')} × {v.get('lotmult', '?')} tous les {_h(v, 'tbm')} ordres · {sl}",
            "🛡️ filtres : " + (", ".join(flt) if flt else "aucun")]


def settings_diff(old: dict, new: dict) -> list[str]:
    """Réglages qui diffèrent entre deux backtests (« objectif : 0,30 % → 0,50 % »)."""
    from . import presets as ps
    out = []
    for k in SHOWN:
        a, b = old.get(k), new.get(k)
        if a is None or b is None or str(a) == str(b):
            continue
        p = ps.BY_KEY.get(k)
        name = p[1] if p else k
        out.append(f"{name} : {_h(old, k)} → {_h(new, k)}")
    return out
