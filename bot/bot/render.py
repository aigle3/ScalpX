"""Rendu des messages Telegram (HTML) : tableau de bord, fiche compte, déploiement. Fonctions pures, testables."""
from __future__ import annotations

import html
import time
from typing import Iterable

from core.summary import FILTER_LABELS, SORT_LABELS, AccountView, Totals

STATE_TAG = {"ACTIF": "", "ARRET_500": " 🏁500", "PAUSE": " ⏸"}


def esc(s: object) -> str:
    return html.escape(str(s), quote=False)


def bar(frac: float, n: int = 5) -> str:
    """Barre de progression ▰▰▰▱▱ (frac 0..1, plafonnée)."""
    f = 0.0 if frac != frac else max(0.0, min(1.0, frac))
    full = int(round(f * n))
    return "▰" * full + "▱" * (n - full)


def money(v: float, cur: str = "", sign: bool = False) -> str:
    s = f"{v:+,.2f}" if sign else f"{v:,.2f}"
    s = s.replace(",", " ")
    return f"{s} {cur}".strip()


def ago(seconds: float) -> str:
    s = int(max(0, seconds))
    if s < 90:
        return f"{s} s"
    if s < 5400:
        return f"{s // 60} min"
    return f"{s // 3600} h {(s % 3600) // 60:02d}"


def welcome_empty() -> str:
    return ("👋 <b>ScalpX — pilotage central</b>\n\nAucun compte pour le moment.\n"
            "Touche <b>➕ Nouveau compte</b> pour créer le premier terminal "
            "(numéro de compte, mot de passe, serveur).")


def summary_header(t: Totals, vps: dict[str, float], cur: str) -> str:
    states = " ".join(f"{icon} {n}" for icon, n in sorted(t.by_icon.items()))
    c = "" if t.currency_mixed else cur
    lines = [
        f"<b>ScalpX</b> · {t.count} compte{'s' if t.count > 1 else ''}   {states}",
        f"💰 Solde {money(t.balance, c)}   Equity {money(t.equity, c)}",
        f"📊 Latent {money(t.pl_latent, c, True)}   Jour {money(t.pl_jour, c, True)}",
    ]
    if t.to_withdraw > 0:
        lines.append(f"💸 <b>À retirer : {money(t.to_withdraw, c)}</b>")
    if t.currency_mixed:
        lines.append("⚠️ devises mélangées : totaux indicatifs")
    lines.append(f"🖥 CPU {vps.get('cpu', 0):.0f}%  RAM {vps.get('ram', 0):.0f}%  Disque {vps.get('disk', 0):.0f}%")
    return "\n".join(lines)


def account_line(v: AccountView, label_w: int = 10) -> str:
    """Une ligne du bloc <pre> : icône, nom, solde, positions, barre vers 500, cycle, version obsolète."""
    name = v.inst.label[:label_w].ljust(label_w)
    if v.st is None:
        return f"{v.health.icon} {name} {'—':>9}"
    st = v.st
    pct = int(round(min(st.progress500(), 9.99) * 100))
    flag = "⬆" if v.outdated else " "
    return (f"{v.health.icon} {name} {st.solde:>9.2f} {st.positions:>2}p "
            f"{bar(st.progress500())} {pct:>3}% c{st.cycle}{flag}{STATE_TAG.get(st.etat, '')}")


def dashboard_text(views: list[AccountView], page_views: list[AccountView], t: Totals, vps: dict[str, float],
                   page: int, pages: int, flt: str, sort: str, cur: str, data_age_s: float | None) -> str:
    if t.count == 0:
        return welcome_empty()
    out = [summary_header(t, vps, cur), ""]
    if page_views:
        out.append("<pre>" + "\n".join(esc(account_line(v)) for v in page_views) + "</pre>")
    else:
        out.append("<i>Aucun compte pour ce filtre.</i>")
    out.append(f"Filtre : <b>{FILTER_LABELS.get(flt, flt)}</b> · tri : <b>{SORT_LABELS.get(sort, sort)}</b> · page {page + 1}/{pages}")
    if data_age_s is not None:
        out.append(f"🕒 données vieilles de {ago(data_age_s)}")
    return "\n".join(out)


def account_card(v: AccountView, withdrawn: float, closures: int, now: float | None = None) -> str:
    now = now if now is not None else time.time()
    i, st = v.inst, v.st
    head = f"{v.health.icon} <b>{esc(i.label)}</b> · {i.login}\n<code>{esc(i.server)}</code>"
    lines = [head, ""]
    lines.append(f"État souhaité : <b>{i.desired_state}</b>" + (f" · EA : <b>{st.etat}</b>" if st else ""))
    if v.health.reasons:
        lines.append("⚠️ " + " ; ".join(esc(r) for r in v.health.reasons))
    if st is None:
        lines.append("Pas de statut de l'EA.")
        return "\n".join(lines)
    cur = st.devise
    lines += [
        f"💰 Solde {money(st.solde, cur)} · Equity {money(st.equity, cur)}",
        f"📊 Flottant EA {money(st.flottant_ea, cur, True)} · Jour {money(st.pl_jour, cur, True)}",
        f"🎯 Vers {st.seuil_arret:.0f} : {bar(st.progress500(), 10)} {st.progress500() * 100:.0f}% "
        f"(solde+flottant {money(st.solde_plus_flottant_ea, cur)})",
        f"📌 {st.positions} position(s) · {st.ordres} ordre(s) · cycle {st.cycle}",
    ]
    if st.etat == "ARRET_500":
        lines.append(f"💸 <b>À retirer : {money(st.to_retire(), cur)}</b> (laisser {st.seuil_reprise:.0f})")
    if st.objectif_actif:
        lines.append(f"🎯 Objectif {st.objectif_progression:.0f}% ({money(st.objectif_depart, cur)} → {money(st.objectif_cible, cur)})")
    elif st.prochain_creneau:
        lines.append(f"🕐 Prochain créneau : {esc(st.prochain_creneau)}")
    if st.raison_blocage:
        lines.append(f"⛔ {esc(st.raison_blocage)}")
    lines.append(f"Marge libre {money(st.marge_libre, cur)} · niveau {st.niveau_marge:.0f}%")
    lines.append(f"EA v{esc(st.version)}" + (" ⬆ <b>obsolète</b>" if v.outdated else "") +
                 f" · {esc(st.symbole)} · heartbeat il y a {ago(st.age_s(now))}")
    lines.append(f"Retiré au total : {money(withdrawn, cur)} · clôtures 500 : {closures}")
    return "\n".join(lines)


def deploy_view(view: dict[str, tuple[str, str]], labels: dict[str, str], title: str = "🔄 Déploiement") -> str:
    lines = [f"<b>{title}</b>", ""]
    for iid, (icon, txt) in view.items():
        lines.append(f"{icon} {esc(labels.get(iid, iid))} — {esc(txt)}")
    return "\n".join(lines)


def paginate(items: list, page: int, size: int) -> tuple[list, int, int]:
    """(éléments de la page, page corrigée, nombre de pages)."""
    pages = max(1, (len(items) + size - 1) // size)
    page = max(0, min(page, pages - 1))
    return items[page * size:(page + 1) * size], page, pages


def join_notices(lines: Iterable[str], limit: int = 3800) -> list[str]:
    """Regroupe des blocs de texte en messages ≤ limit caractères (Telegram : 4096)."""
    out: list[str] = []
    cur = ""
    for ln in lines:
        if len(cur) + len(ln) + 2 > limit and cur:
            out.append(cur)
            cur = ""
        cur += ("\n\n" if cur else "") + ln
    if cur:
        out.append(cur)
    return out
