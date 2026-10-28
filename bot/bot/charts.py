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


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()
