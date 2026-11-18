"""Rapport quotidien et « 📝 Rapport des 24 h » : l'essentiel seulement.

Évolution du capital (global puis par compte, hors dépôts et retraits), objectifs atteints / manqués,
et une ligne d'alerte seulement s'il y a quelque chose à signaler.
"""
from __future__ import annotations

import re
import time

from .actions import Ctx
from .render import esc, money

_FLOW = re.compile(r"(DÉPÔT|RETRAIT) ([+-]?\d[\d ]*(?:\.\d+)?)")
GOAL_TYPES = ("OBJECTIF_AUTO", "OBJECTIF_ATTEINT", "OBJECTIF_ANNULE", "OBJECTIF_COUPE", "NOTIF", "CASHFLOW",
              "CONNEXION_PERDUE", "CAPITAL_INSUFFISANT")


def _flow(text: str) -> float:
    """Montant d'un événement CASHFLOW (« 💰 DÉPÔT +100.00 EUR », « 💸 RETRAIT -375.00 EUR »)."""
    m = _FLOW.search(text or "")
    if not m:
        return 0.0
    try:
        return float(m.group(2).replace(" ", ""))
    except ValueError:
        return 0.0


def day_start_ts(tz: str, now: float | None = None) -> int:
    """Minuit (fuseau des rapports) du jour en cours, en secondes Unix."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    try:
        z = ZoneInfo(tz)
    except Exception:
        z = None
    d = datetime.fromtimestamp(now if now is not None else time.time(), z)
    return int(d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def day_gains(db, views, tz: str, now: float | None = None) -> dict[str, float]:
    """Bot 4.8.11 : gain du jour par compte d'après l'historique du BOT (restauré par ☁️), et non plus d'après l'EA
    (ses compteurs du jour sont perdus quand le terminal est recréé). Dépôts / retraits exclus. Compte sans historique
    du jour : valeur de l'EA."""
    since = day_start_ts(tz, now)
    flows: dict[str, float] = {}
    for e in db.events_since(since):
        if e["type"] == "CASHFLOW":
            flows[e["iid"]] = flows.get(e["iid"], 0.0) + _flow(e["text"])
    out: dict[str, float] = {}
    for v in views:
        if v.st is None:
            continue
        start = db.balance_before(v.inst.id, since)
        if start is None:
            rows = db.balances(v.inst.id, since)
            start = rows[0]["balance"] if rows else None
        out[v.inst.id] = v.st.day_realized() if start is None else v.st.solde - start - flows.get(v.inst.id, 0.0)
    return out


def _pct(delta: float, base: float) -> str:
    return f" ({delta / base * 100:+.1f} %)" if base > 0 else ""


def build_report(ctx: Ctx, since: float, now: float | None = None, title: str = "📝 Rapport") -> str:
    now = now if now is not None else time.time()
    db = ctx.db
    views = ctx.views()
    since_i = int(since)
    head = f"<b>{title}</b> · {time.strftime('%d/%m/%Y %H:%M', time.localtime(now))}"
    if not views:
        return head + "\n\nAucun compte."
    evs = [e for e in db.events_since(since_i) if e["type"] in GOAL_TYPES]
    by: dict[str, dict] = {}
    for e in evs:
        d = by.setdefault(e["iid"], {"launched": 0, "won": 0, "missed": 0, "sl": 0, "flows": 0.0})
        t = e["type"]
        if t == "OBJECTIF_AUTO":
            d["launched"] += 1
        elif t == "OBJECTIF_ATTEINT":
            d["won"] += 1
        elif t in ("OBJECTIF_ANNULE", "OBJECTIF_COUPE"):
            d["missed"] += 1
        elif t == "NOTIF" and "STOP LOSS D'URGENCE" in (e["text"] or ""):
            d["sl"] += 1
        elif t == "CASHFLOW":
            d["flows"] += _flow(e["text"])
    cur = next((v.st.devise for v in views if v.st and v.st.devise), "")
    tot_now = tot_start = tot_perf = 0.0
    lines = []
    for v in views:
        if v.st is None:
            lines.append(f"{v.health.icon} {esc(v.inst.label)} : pas de nouvelles de l'EA")
            continue
        rows = db.balances(v.inst.id, since_i)
        start = rows[0]["balance"] if rows else v.st.solde
        d = by.get(v.inst.id, {"launched": 0, "won": 0, "missed": 0, "sl": 0, "flows": 0.0})
        perf = v.st.solde - start - d["flows"]                       # dépôts et retraits exclus
        tot_now += v.st.solde
        tot_start += start
        tot_perf += perf
        goals = f"🎯 {d['won']}/{d['launched']}" + (f" · ⌛ {d['missed']}" if d["missed"] else "") + (f" · 🚨 {d['sl']}" if d["sl"] else "")
        lines.append(f"{v.health.icon} {esc(v.inst.label)} : {money(v.st.solde, cur)} · <b>{money(perf, '', True)}</b>"
                     f"{_pct(perf, start + max(d['flows'], 0.0))} · {goals}")
    launched = sum(d["launched"] for d in by.values())
    won = sum(d["won"] for d in by.values())
    missed = sum(d["missed"] for d in by.values())
    sl = sum(d["sl"] for d in by.values())
    wd = db.withdrawals_since(since_i)
    out = [head, "",
           f"💼 <b>Capital : {money(tot_now, cur)}</b> · <b>{money(tot_perf, cur, True)}</b>{_pct(tot_perf, tot_start + sum(max(d['flows'], 0.0) for d in by.values()))} en 24 h",
           f"🎯 Objectifs : ✅ {won} atteints sur {launched} · ⌛ <b>{missed} manqués</b>" + (f" · 🚨 {sl} stop loss" if sl else "")]
    if wd:
        out.append(f"💸 Retiré : {money(sum(w['amount'] for w in wd), cur)} ({len(wd)} cycle(s) 500 terminé(s))")
    out += ["", "<b>Par compte</b> <i>(solde · évolution 24 h · objectifs ✅/lancés)</i>"] + lines
    alerts = []
    n_inc = len(db.incidents_since(since_i)) + sum(1 for e in evs if e["type"] in ("CONNEXION_PERDUE", "CAPITAL_INSUFFISANT"))
    if n_inc:
        alerts.append(f"{n_inc} incident(s) (détail : 🛠 Système → journaux)")
    old = [v.inst.label for v in views if v.outdated]
    if old:
        alerts.append("EA pas à jour : " + esc(", ".join(old)))
    if alerts:
        out += ["", "⚠️ " + " · ".join(alerts)]
    return "\n".join(out)
