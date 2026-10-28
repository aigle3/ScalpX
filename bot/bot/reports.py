"""Rapport quotidien et /rapport : performance, cycles, retraits, clôtures 500, incidents, versions."""
from __future__ import annotations

import time
from statistics import mean

from mt5 import base as basemod
from . import render
from .actions import Ctx
from .render import esc, money


def build_report(ctx: Ctx, since: float, now: float | None = None, title: str = "📝 Rapport") -> str:
    now = now if now is not None else time.time()
    db = ctx.db
    views = ctx.views()
    since_i = int(since)
    lines = [f"<b>{title}</b> — {time.strftime('%d/%m/%Y %H:%M', time.localtime(now))}", ""]
    if not views:
        return "\n".join(lines + ["Aucun compte."])
    lines.append("<b>Performance par compte</b>")
    for v in views:
        rows = db.balances(v.inst.id, since_i)
        if v.st is None:
            lines.append(f"{v.health.icon} {esc(v.inst.label)} : pas de statut")
            continue
        first = rows[0]["balance"] if rows else v.st.solde
        delta = v.st.solde - first
        lines.append(f"{v.health.icon} {esc(v.inst.label)} : solde {money(v.st.solde, v.st.devise)} "
                     f"({money(delta, '', True)}) · jour {money(v.st.pl_jour, '', True)} · cycle {v.st.cycle}")
    cycles = db.cycles_since(since_i)
    wd = db.withdrawals_since(since_i)
    lines += ["", "<b>Cycles et retraits</b>",
              f"Cycles terminés : {len(cycles)} · retiré sur la période : {money(sum(w['amount'] for w in wd))} · "
              f"total retiré : {money(db.total_withdrawn())}"]
    cl = db.closures_since(since_i)
    ok = [c for c in cl if c["kind"] == "OK"]
    under = [c for c in cl if c["kind"] != "OK"]
    slip = mean([c["delta"] for c in cl]) if cl else 0.0
    lines += ["", "<b>Clôtures à 500</b>",
              f"Réussies : {len(ok)} · sous le seuil : {len(under)} · glissement moyen : {money(slip, '', True)}"]
    inc = db.incidents_since(since_i)
    evs = [e for e in db.events_since(since_i) if e["type"] in ("CONNEXION_PERDUE", "CAPITAL_INSUFFISANT")]
    lines += ["", "<b>Incidents</b>"]
    if inc or evs:
        for r in inc[-8:]:
            lines.append(f"• {time.strftime('%H:%M', time.localtime(r['ts']))} {esc(r['kind'])} — {esc(str(r['text'])[:90])}")
        for r in evs[-5:]:
            lines.append(f"• {time.strftime('%H:%M', time.localtime(r['ts']))} {esc(r['type'])} ({esc(r['iid'])})")
    else:
        lines.append("Aucun incident.")
    bv = basemod.read_version(ctx.cfg.paths.base)
    old = [v.inst.label for v in views if v.outdated]
    lines += ["", f"<b>Versions de l'EA</b> : BASE {esc(bv or '?')}" +
              (f" · obsolètes : {esc(', '.join(old))}" if old else " · tous à jour")]
    return "\n".join(lines)
