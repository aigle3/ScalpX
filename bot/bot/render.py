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


SEP = "━━━━━━━━━━━━━━━━━━"
ICON_ORDER = (("🟢", "actif"), ("🟠", "alerte"), ("🔴", "arrêté"), ("⚙️", "maint."), ("🔒", "clôture"), ("⚪", "à l'arrêt"))


def home_line(v: AccountView, label_w: int = 10) -> str:
    """Ligne compacte de l'accueil : icône, nom, solde, barre vers 500."""
    name = v.inst.label[:label_w].ljust(label_w)
    if v.st is None:
        return f"{v.health.icon} {name} {'—':>9}"
    pct = int(round(min(v.st.progress500(), 9.99) * 100))
    return f"{v.health.icon} {name} {v.st.solde:>9.2f} {bar(v.st.progress500())} {pct:>3}%{STATE_TAG.get(v.st.etat, '')}"


def _hhmm(s: str) -> str:
    """« 2026.10.28 14:30 » → « 14:30 » (ou « 29/10 14:30 » si ce n'est pas aujourd'hui)."""
    if len(s) < 16:
        return s
    if s[:10] == time.strftime("%Y.%m.%d"):
        return s[11:16]
    return f"{s[8:10]}/{s[5:7]} {s[11:16]}"


def _in_min(m: int) -> str:
    if m < 0:
        return "en cours"
    return f"dans {m} min" if m < 90 else f"dans {m // 60} h {m % 60:02d}"


def trading_ref(views: list[AccountView], now: float):
    """Statut de référence pour l'état du trading (tous les robots ont les mêmes réglages) : le plus récent."""
    fresh = [v.st for v in views if v.st is not None and v.st.age_s(now) < 120]
    return max(fresh, key=lambda s: s.ts) if fresh else None


def trading_block(views: list[AccountView], now: float) -> list[str]:
    """Bloc « en tout premier » de l'accueil : le robot trade-t-il, peut-il trader, chaque filtre (dont News), prochaines news."""
    st = trading_ref(views, now)
    sts = [v.st for v in views if v.st is not None and v.st.age_s(now) < 120]
    if st is None:
        return ["🤖 <b>Trading : aucune donnée</b> (aucun robot ne répond)"]
    npos = sum(s.positions for s in sts)
    nacc = sum(1 for s in sts if s.positions > 0)
    flot = sum(s.flottant_ea for s in sts)
    pos_line = (f"📌 {npos} position(s) ouverte(s) sur {nacc} compte(s) · flottant {money(flot, st.devise, True)}"
                if npos else "📌 Aucune position ouverte")
    stopped = True
    if st.etat == "ARRET_500":
        head = "🏁 <b>ARRÊT À 500</b> : retrait attendu"
    elif st.vacances:
        head = "🏖 <b>MODE VACANCES</b> : aucune nouvelle série"
    elif st.etat == "PAUSE" or (st.objectif_actif and not st.ea_demarre):
        head = "⏸ <b>EN PAUSE</b>" + (f" jusqu'à {_hhmm(st.pause_jusqua)} (reprise automatique)" if st.pause_jusqua else "")
    elif not st.objectif_actif:
        head = "⚪ <b>À L'ARRÊT</b> : aucun objectif" + (f" · prochain créneau {_hhmm(st.prochain_creneau)}" if st.prochain_creneau else "")
    else:
        stopped = False
        head = "🟢 <b>EN TRAIN DE TRADER</b> : série en cours" if npos else "🟡 <b>EN ATTENTE</b> : aucune position"
    lines = [head, pos_line]
    if not stopped:
        blocked = st.trading_autorise is False or (st.trading_autorise is None and bool(st.raison_blocage))
        if blocked:
            lines.append(f"⛔ Nouvelles séries <b>bloquées</b> : {esc(st.raison_blocage or '?')}")
        else:
            sig = {"achat": " · signal achat", "vente": " · signal vente"}.get(st.signal, "")
            lines.append(f"✅ Nouvelles séries <b>autorisées</b> : entrée au prochain signal{sig}")
    if st.filtres is None:
        lines.append("🛡 Filtres : détail après la mise à jour de l'EA (5.5.0)")
        return lines
    # News, mis en avant
    if not st.news_filtre:
        lines.append("📰 Filtre News <b>désactivé</b> : le robot trade pendant les annonces")
    elif st.news_bloque:
        lines.append(f"📰 <b>NEWS EN COURS</b> : {esc(st.news_raison)} → pas de nouvelle entrée")
    else:
        lines.append("📰 Filtre News actif : aucune news en cours")
    on = [f for f in st.filtres if f.actif]
    off = [f.nom for f in st.filtres if not f.actif]
    lines.append("🛡 " + " · ".join(("🟢 " if f.ok else "🔴 ") + esc(f.nom) for f in on) if on else "🛡 Aucun filtre actif")
    for f in on:
        if not f.ok and f.raison:
            lines.append(f"   ↳ {esc(f.nom)} : {esc(f.raison)}")
    if off:
        lines.append("⚪ Désactivés : " + esc(", ".join(off)))
    if st.news:
        items = [f"{'🔴' if n.haute else '🟠'} {_hhmm(n.heure)} {esc(n.devise)} {esc(n.nom[:40])} ({_in_min(n.dans_min)})" for n in st.news[:3]]
        lines.append("📅 <b>Prochaines news</b> :\n   " + "\n   ".join(items))
    else:
        lines.append("📅 Aucune news importante dans les 24 h")
    return lines


def home_text(views: list[AccountView], t: Totals, vps: dict[str, float], cur: str, bot_v: str, ea_v: str,
              update_pending: bool, data_age_s: float | None, now: float, max_lines: int = 8) -> str:
    """Accueil : état de l'ensemble (comptes, argent, alertes, VPS), puis une ligne par compte."""
    c = "" if t.currency_mixed else cur
    counts = "   ".join(f"{icon} {t.by_icon.get(icon, 0)}" for icon, _ in ICON_ORDER
                        if t.by_icon.get(icon, 0) or icon in ("🟢", "🟠", "🔴"))
    lines = [f"📊 <b>ScalpX</b> — {time.strftime('%H:%M', time.localtime(now))}", SEP]
    lines += trading_block(views, now)
    lines += [SEP, counts,
             f"💰 Solde <b>{money(t.balance, c)}</b> · jour {money(t.pl_jour, c, True)}"]
    if t.pl_latent:
        lines.append(f"📉 Latent {money(t.pl_latent, c, True)}")
    lines.append(f"💸 À retirer : <b>{money(t.to_withdraw, c)}</b>" if t.to_withdraw > 0 else f"💸 À retirer : {money(0, c)}")
    alerts = [v for v in views if v.health.icon in ("🟠", "🔴")]
    if alerts:
        for v in alerts[:3]:
            why = "; ".join(v.health.reasons[:2]) if v.health.reasons else "à vérifier"
            lines.append(f"{v.health.icon} <b>{esc(v.inst.label)}</b> : {esc(why)}")
        if len(alerts) > 3:
            lines.append(f"… et {len(alerts) - 3} autre(s) alerte(s)")
    else:
        lines.append("✅ Aucune alerte")
    hot = [f"{k.upper()} {vps.get(k, 0):.0f}%" for k, lim in (("cpu", 85), ("ram", 85), ("disk", 90)) if vps.get(k, 0) >= lim]
    lines.append(("⚠️ VPS chargé : " + " · ".join(hot) if hot else "🖥 VPS OK") + f" · Bot {esc(bot_v)} · EA {esc(ea_v or '?')}")
    if update_pending:
        lines.append("🔔 Mise à jour disponible : 🛠 Système")
    if data_age_s is not None and data_age_s > 45:
        lines.append(f"⚠️ données vieilles de {ago(data_age_s)}")
    lines.append(SEP)
    shown = sorted(views, key=lambda v: (v.health.icon != "🔴", v.health.icon != "🟠", v.inst.label.lower()))[:max_lines]
    lines.append("<pre>" + "\n".join(esc(home_line(v)) for v in shown) + "</pre>")
    if len(views) > max_lines:
        lines.append(f"… et {len(views) - max_lines} autre(s) : 👥 Comptes")
    return "\n".join(lines)


def list_text(page_views: list[AccountView], total: int, page: int, pages: int, flt: str, sort: str) -> str:
    out = [f"👥 <b>Comptes</b> · {total}", ""]
    if page_views:
        out.append("<pre>" + "\n".join(esc(account_line(v)) for v in page_views) + "</pre>")
    else:
        out.append("<i>Aucun compte pour ce filtre.</i>")
    out.append(f"🔎 {FILTER_LABELS.get(flt, flt)} · ↕ {SORT_LABELS.get(sort, sort)}" + (f" · page {page + 1}/{pages}" if pages > 1 else ""))
    out.append("<i>Touche un compte pour sa fiche.</i>")
    return "\n".join(out)


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
