"""Graphiques PNG (matplotlib, backend sans écran) à partir de l'historique SQLite."""
from __future__ import annotations

import io
from datetime import datetime
from typing import Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates          # noqa: E402
import matplotlib.pyplot as plt            # noqa: E402


def _style(ax, title: str) -> None:
    ax.set_title(title, color="#e2e8f0", fontsize=11)
    ax.set_facecolor("#0e1422")
    ax.grid(True, color="#2a3650", linewidth=0.6)
    ax.tick_params(colors="#94a3b8", labelsize=8)
    for sp in ax.spines.values():
        sp.set_color("#2a3650")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %H:%M"))


def balance_chart(rows: Sequence, title: str, seuil_arret: float = 500.0, seuil_reprise: float = 125.0) -> bytes | None:
    """Solde / equity d'un compte avec les seuils 125 et 500. None si pas assez de points."""
    if len(rows) < 2:
        return None
    ts = [datetime.fromtimestamp(r["ts"]) for r in rows]
    fig, ax = plt.subplots(figsize=(8, 4), dpi=110)
    fig.patch.set_facecolor("#0e1422")
    ax.plot(ts, [r["balance"] for r in rows], color="#26c281", linewidth=1.8, label="Solde")
    ax.plot(ts, [r["equity"] for r in rows], color="#42a5f5", linewidth=1.0, alpha=0.8, label="Equity")
    ax.axhline(seuil_arret, color="#ffa726", linestyle="--", linewidth=1, label=f"Arrêt {seuil_arret:.0f}")
    ax.axhline(seuil_reprise, color="#ef5350", linestyle=":", linewidth=1, label=f"Reprise {seuil_reprise:.0f}")
    _style(ax, title)
    ax.legend(facecolor="#0e1422", edgecolor="#2a3650", labelcolor="#e2e8f0", fontsize=8)
    fig.autofmt_xdate()
    return _png(fig)


def total_chart(series: dict[str, Sequence], title: str) -> bytes | None:
    """Soldes de tous les comptes (une courbe par compte)."""
    if not any(len(v) >= 2 for v in series.values()):
        return None
    fig, ax = plt.subplots(figsize=(8, 4), dpi=110)
    fig.patch.set_facecolor("#0e1422")
    for name, rows in series.items():
        if len(rows) >= 2:
            ax.plot([datetime.fromtimestamp(r["ts"]) for r in rows], [r["balance"] for r in rows], linewidth=1.3, label=name)
    _style(ax, title)
    ax.legend(facecolor="#0e1422", edgecolor="#2a3650", labelcolor="#e2e8f0", fontsize=7, ncol=2)
    fig.autofmt_xdate()
    return _png(fig)


def backtest_chart(res, title: str, cur: str = "") -> bytes | None:
    """Bot 4.13.0 : backtest lancé depuis Telegram. En haut, solde et equity avec le minimum et le maximum du test ; chaque
    retrait au maximum (▲ cycle gagné) et chaque recharge au minimum (▼ cycle perdu) est marqué. En bas, gain net cumulé
    (retraits − recharges). None si pas assez de points."""
    from datetime import timezone
    days = [(int(d[0]), float(d[1]), float(d[2])) for d in (res.days or []) if len(d) >= 3]
    marks = [(int(m[0]), m[1], float(m[2]), float(m[3]) if len(m) > 3 else 0.0) for m in (res.marks or []) if len(m) >= 3]
    if len(days) + len(marks) < 2:
        return None

    def dt(ts: int) -> datetime:
        return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)

    pts = [(ts, b, 0) for ts, b, _ in days]                    # solde : un point par jour + avant / après chaque cycle
    for ts, kind, amount, after in marks:
        before = after + amount if kind == "+" else after - amount
        pts += [(ts, before, 1), (ts, after, 2)]
    pts.sort(key=lambda x: (x[0], x[2]))
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(9, 5.4), dpi=110, sharex=True, gridspec_kw={"height_ratios": [3, 1.2]})
    fig.patch.set_facecolor("#0e1422")
    ax.plot([dt(p[0]) for p in pts], [p[1] for p in pts], color="#26c281", linewidth=1.5, label="Solde")
    if days:
        ax.plot([dt(d[0]) for d in days], [d[2] for d in days], color="#42a5f5", linewidth=0.9, alpha=0.7, label="Equity (fin de jour)")
    if res.hi:
        ax.axhline(res.hi, color="#ffa726", linestyle="--", linewidth=1, label=f"Maximum {res.hi:.0f}")
    if res.lo:
        ax.axhline(res.lo, color="#ef5350", linestyle=":", linewidth=1, label=f"Minimum {res.lo:.0f}")
    won = [(dt(ts), after + amount) for ts, k, amount, after in marks if k == "+"]
    lost = [(dt(ts), after - amount) for ts, k, amount, after in marks if k == "-"]
    if won:
        ax.scatter([w[0] for w in won], [w[1] for w in won], marker="^", color="#26c281", s=34, zorder=5,
                   label=f"Cycle gagné ({len(won)})")
    if lost:
        ax.scatter([w[0] for w in lost], [w[1] for w in lost], marker="v", color="#ef5350", s=34, zorder=5,
                   label=f"Cycle perdu ({len(lost)})")
    _style(ax, title)
    ax.legend(facecolor="#0e1422", edgecolor="#2a3650", labelcolor="#e2e8f0", fontsize=7, loc="upper left", ncol=2)
    cum, xs, ys = 0.0, [dt(res.d_from)], [0.0]               # gain net cumulé (escalier)
    for ts, kind, amount, _ in sorted(marks):
        xs.append(dt(ts)); ys.append(cum)
        cum += amount if kind == "+" else -amount
        xs.append(dt(ts)); ys.append(cum)
    xs.append(dt(max(res.d_to, days[-1][0] if days else res.d_to))); ys.append(cum)
    ax2.plot(xs, ys, color="#ab47bc", linewidth=1.4)
    ax2.fill_between(xs, ys, 0, color="#ab47bc", alpha=0.15)
    ax2.axhline(0, color="#64748b", linewidth=0.8)
    _style(ax2, "")
    ax2.set_ylabel(f"Gain net cumulé {cur}".strip(), color="#94a3b8", fontsize=8)
    span = max(1, res.d_to - res.d_from)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %H:%M" if span <= 3 * 86400 else "%d/%m/%y" if span > 200 * 86400 else "%d/%m"))
    fig.autofmt_xdate()
    fig.tight_layout()
    return _png(fig)


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()
