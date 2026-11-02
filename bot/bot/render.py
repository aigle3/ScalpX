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
    if s >= 2 * 86400:
        return f"{s // 86400} j {(s % 86400) // 3600} h"
    return f"{s // 3600} h {(s % 3600) // 60:02d}"


def welcome_empty() -> str:
    return ("👋 <b>ScalpX — pilotage central</b>\n\nAucun compte pour le moment.\n"
            "Touche <b>➕ Nouveau compte</b> pour créer le premier terminal "
            "(numéro de compte, mot de passe, serveur).")


SEP = "━━━━━━━━━━━━━━━━━━"
ICON_ORDER = (("🟢", "actif"), ("🟠", "alerte"), ("🔴", "arrêté"), ("⚙️", "maint."), ("🔒", "clôture"), ("⚪", "à l'arrêt"))


def home_line(v: AccountView, label_w: int = 10) -> str:
    """Ligne compacte de l'accueil : icône, nom, solde, barre.
    Objectif en cours sur ce compte → barre de l'objectif (🎯) ; sinon → progression vers le seuil d'arrêt (500)."""
    name = v.inst.label[:label_w].ljust(label_w)
    if v.st is None:
        return f"{v.health.icon} {name} {'—':>9}"
    st = v.st
    if st.objectif_actif:
        frac = max(0.0, min(st.objectif_progression, 100.0)) / 100
        return f"{v.health.icon} {name} {st.solde:>9.2f} {bar(frac)} {frac * 100:>3.0f}% 🎯"
    pct = int(round(min(st.progress500(), 9.99) * 100))
    return f"{v.health.icon} {name} {st.solde:>9.2f} {bar(st.progress500())} {pct:>3}%{STATE_TAG.get(st.etat, '')}"


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


def news_item(n) -> str:
    return f"{'🔴' if n.haute else '🟠'} {_hhmm(n.heure)} {esc(n.devise)} {esc(n.nom[:40])} ({_in_min(n.dans_min)})"


def trading_ref(views: list[AccountView], now: float):
    """Statut de référence pour l'état du trading (tous les robots ont les mêmes réglages) : le plus récent."""
    fresh = [v.st for v in views if v.st is not None and v.st.age_s(now) < 120]
    return max(fresh, key=lambda s: s.ts) if fresh else None


def goal_line(views: list[AccountView], st, now: float) -> str:
    """🎯 Objectif en cours : gagné / à gagner (total des comptes en objectif) + barre ; le détail est sur chaque ligne de compte."""
    fresh = [v for v in views if v.st is not None and v.st.age_s(now) < 120]
    on = [v for v in fresh if v.st.objectif_actif]
    if not on:
        return "🎯 Aucun objectif en cours" + (f" · prochain créneau {_hhmm(st.prochain_creneau)}" if st and st.prochain_creneau else "")
    cur = on[0].st.devise
    want = sum(max(0.0, v.st.objectif_cible - v.st.objectif_depart) for v in on)
    got = sum(max(0.0, v.st.objectif_cible - v.st.objectif_depart) * max(0.0, min(v.st.objectif_progression, 100.0)) / 100 for v in on)
    frac = got / want if want > 0 else 0.0
    who = "" if len(fresh) == 1 else f" {len(on)}/{len(fresh)} comptes"
    return (f"🎯 <b>Objectif{who}</b> : {money(got, '', True)} / {money(want, cur, True)} "
            f"{bar(frac)} {frac * 100:.0f} %")


def _head(st, npos: int) -> tuple[str, bool]:
    """État du trading (titre) ; second élément : True si le robot est arrêté (pas de ligne « nouvelles séries »)."""
    if st.etat == "ARRET_500":
        return "🏁 <b>Arrêt à 500</b> : retrait attendu", True
    if st.vacances:
        return "🏖 <b>Vacances</b> : aucune nouvelle série", True
    if st.etat == "PAUSE" or (st.objectif_actif and not st.ea_demarre):
        return "⏸ <b>En pause</b>" + (f" jusqu'à {_hhmm(st.pause_jusqua)}" if st.pause_jusqua else ""), True
    if not st.objectif_actif:
        return "⚪ <b>À l'arrêt</b> : attend un objectif", True
    return ("🟢 <b>En train de trader</b>" if npos else "🟡 <b>En attente</b> : aucune position"), False


def trading_block(views: list[AccountView], now: float) -> list[str]:
    """Bloc « en tout premier » de l'accueil : état, positions, nouvelles séries, filtres (résumé), news."""
    st = trading_ref(views, now)
    sts = [v.st for v in views if v.st is not None and v.st.age_s(now) < 120]
    if st is None:
        return ["🤖 <b>Trading : aucune donnée</b> (aucun robot ne répond)"]
    npos = sum(s.positions for s in sts)
    nacc = sum(1 for s in sts if s.positions > 0)
    flot = sum(s.flottant_ea for s in sts)
    # titre : un compte en objectif l'emporte ; « arrêt à 500 » seulement si tous les comptes y sont (sinon ligne 🏁 dédiée)
    pick = ([s for s in sts if s.objectif_actif] or [s for s in sts if s.etat != "ARRET_500"] or sts)
    head, stopped = _head(max(pick, key=lambda s: s.ts), npos)
    lines = [head]
    if npos:
        lines.append(f"📌 {npos} position(s)" + (f" sur {nacc} comptes" if nacc > 1 else "") + f" · flottant {money(flot, st.devise, True)}")
    else:
        lines.append("📌 Aucune position")
    cut = next((s.coupure_en_cours for s in sts if s.coupure_en_cours), "")
    if cut:                                                  # EA 7.0.0 : coupure passée pendant une série
        lines.append(f"⛔ Coupure {esc(cut)} : la série en cours finit au TP, puis l'objectif est annulé")
    if not stopped:
        blocked = st.trading_autorise is False or (st.trading_autorise is None and bool(st.raison_blocage))
        if blocked:
            lines.append(f"⛔ Nouvelles séries <b>bloquées</b> : {esc(st.raison_blocage or '?')}")
        else:
            sig = {"achat": " (achat)", "vente": " (vente)"}.get(st.signal, "")
            lines.append(f"✅ Entrée au prochain signal{sig}")
    if st.filtres is None:
        lines.append("🛡 Filtres : détail après la mise à jour de l'EA (5.5.0)")
        return lines
    on = [f for f in st.filtres if f.actif]                  # filtres désactivés : non affichés
    bad = [f for f in on if not f.ok]
    if not on:
        flt = "🛡 Aucun filtre actif"
    else:
        flt = f"🛡 Filtres {len(on) - len(bad)}/{len(on)} OK" if not bad else f"🛡 Filtres <b>{len(on) - len(bad)}/{len(on)} OK</b>"
    lines.append(flt)
    for f in bad:
        lines.append(f"   🔴 {esc(f.nom)}" + (f" : {esc(f.raison)}" if f.raison else ""))
    if not st.news_filtre:
        lines.append("📰 Filtre News <b>désactivé</b> : le robot trade pendant les annonces")
    elif st.news_bloque:
        lines.append(f"📰 <b>NEWS EN COURS</b> : {esc(st.news_raison)} → pas de nouvelle entrée")
    if st.news:
        more = f" · +{len(st.news) - 1} autre(s) : 🧭" if len(st.news) > 1 else ""
        lines.append(f"📅 {news_item(st.news[0])}{more}")
    else:
        lines.append("📅 Aucune news importante dans les 24 h")
    return lines


def withdraw_lines(views: list[AccountView], cur: str) -> list[str]:
    """Comptes arrivés au seuil (ARRET_500) : montant à retirer sur chacun (le robot reprend seul après le retrait)."""
    due = due_withdrawals(views)
    if not due:
        return []
    total = sum(v.st.to_retire() for v in due)
    out = [f"🏁 <b>{len(due)} compte(s) à 500</b> · à retirer <b>{money(total, cur)}</b> :"]
    out += [f"   💸 {esc(v.inst.label)} : <b>{money(v.st.to_retire(), v.st.devise)}</b> (laisser {v.st.seuil_reprise:.0f})"
            for v in due[:6]]
    if len(due) > 6:
        out.append(f"   … et {len(due) - 6} autre(s) : 👥 Comptes")
    return out


def due_withdrawals(views: list[AccountView]) -> list[AccountView]:
    return sorted((v for v in views if v.st is not None and v.st.etat == "ARRET_500"), key=lambda v: v.inst.label.lower())


def withdraw_text(views: list[AccountView], history: list, total_all: float, total_30: float, since: dict[str, int],
                  cur: str, now: float, labels: dict[str, str], near_pct: float = 0.8) -> str:
    """💸 Retraits : comptes à 500 (montant, depuis quand), comptes bientôt à 500, derniers retraits."""
    lines = ["💸 <b>Retraits</b>", ""]
    due = due_withdrawals(views)
    if due:
        tot = sum(v.st.to_retire() for v in due)
        lines.append(f"🏁 <b>À retirer maintenant</b> ({len(due)} compte(s)) : <b>{money(tot, cur)}</b>")
        rows = []
        for v in due:
            t0 = since.get(v.inst.id, 0)
            wait = f" · {ago(now - t0)}" if t0 else ""
            rows.append(f"{v.inst.label[:10]:<10} {v.st.solde:>8.2f} → {v.st.to_retire():>8.2f}{wait}")
        lines.append("<pre>" + esc("\n".join(rows)) + "</pre>")
        ref = due[0].st
        lines.append(f"Retire la somme indiquée (il reste {ref.seuil_reprise:.0f}) : le robot repart <b>tout seul</b> "
                     f"dès que le solde est entre {ref.seuil_reprise:.0f} et {ref.seuil_arret:.0f}.")
    else:
        lines.append("✅ Aucun compte à 500 : rien à retirer.")
    near = sorted((v for v in views if v.st is not None and v.st.etat != "ARRET_500" and v.st.progress500() >= near_pct),
                  key=lambda v: -v.st.progress500())
    if near:
        lines += ["", f"🔜 <b>Bientôt à 500</b> (≥ {near_pct * 100:.0f} %)"]
        lines.append("<pre>" + esc("\n".join(f"{v.inst.label[:10]:<10} {v.st.solde:>8.2f} {bar(v.st.progress500())} "
                                                f"{v.st.progress500() * 100:>3.0f}%" for v in near[:8])) + "</pre>")
    lines += ["", "📜 <b>Derniers retraits</b>"]
    if history:
        rows = [f"{time.strftime('%d/%m', time.localtime(r['ts']))} {labels.get(r['iid'], r['iid'])[:10]:<10} {r['amount']:>9.2f}"
                for r in history[:10]]
        lines.append("<pre>" + esc("\n".join(rows)) + "</pre>")
    else:
        lines.append("<i>aucun retrait enregistré</i>")
    lines.append(f"Total retiré : <b>{money(total_all, cur)}</b> · 30 derniers jours : {money(total_30, cur)}")
    return "\n".join(lines)


def withdraw_reminder(views: list[AccountView], since: dict[str, int], now: float) -> str | None:
    """Rappel quotidien : None si aucun compte n'attend de retrait."""
    due = due_withdrawals(views)
    if not due:
        return None
    cur = due[0].st.devise
    tot = sum(v.st.to_retire() for v in due)
    lines = [f"💸 <b>Rappel : {len(due)} compte(s) à 500</b> attendent un retrait ({money(tot, cur)}) :"]
    for v in due:
        t0 = since.get(v.inst.id, 0)
        lines.append(f"   • {esc(v.inst.label)} : <b>{money(v.st.to_retire(), v.st.devise)}</b>" + (f" (depuis {ago(now - t0)})" if t0 else ""))
    lines.append("Le robot de ces comptes reste arrêté jusqu'au retrait. Détail : 💸 Retraits.")
    return "\n".join(lines)


def _home_order(v: AccountView):
    """Ordre de l'accueil : alertes d'abord, puis comptes en objectif, puis du plus avancé au moins avancé (barre affichée)."""
    st = v.st
    if st is None:
        frac, goal = -1.0, False
    elif st.objectif_actif:
        frac, goal = max(0.0, min(st.objectif_progression, 100.0)) / 100, True
    else:
        frac, goal = st.progress500(), False
    return (v.health.icon != "🔴", v.health.icon != "🟠", not goal, -frac, v.inst.label.lower())


def slot_stats_text(stats: dict, days: int, n_accounts: int, cur: str) -> str:
    """🕐 Statistiques par créneau : essais, taux de réussite, net, durée moyenne, pire drawdown ; verdict."""
    from core.slotstats import slot_key, total
    head = [f"🕐 <b>Statistiques par créneau</b> — {days} derniers jours",
            f"<i>{n_accounts} compte(s) · heures du fuseau de l'EA</i>", ""]
    rows = [s for s in stats.values() if s.launched or s.skipped]
    if not rows:
        return "\n".join(head + ["Aucun objectif de créneau sur la période.",
                                 "<i>Les statistiques se remplissent à chaque créneau lancé.</i>"])
    rows.sort(key=lambda s: slot_key(s.slot))
    t = total(stats)
    be = t.avg_loss / (t.avg_loss + t.avg_gain) if t.losses and t.avg_gain > 0 else None

    def line(s) -> str:
        rate = f"{s.rate * 100:>3.0f}%" if s.rate is not None else "  — "
        dur = f"{s.avg_minutes:>4.0f}m" if s.durations else "    —"
        dd = f"{s.worst_dd:>4.1f}%" if s.dds else "    —"
        warn = " ⚠" if s.lost and be is not None and s.rate is not None and s.rate < be else ""
        return f"{s.slot:>5} {s.launched:>3} {rate} {s.net:>+8.2f} {dur} {dd}{warn}"

    table = [f"{'Heure':>5} {'N':>3} {'Réus':>4} {'Net':>8} {'Durée':>5} {'DDmax':>5}"] + [line(s) for s in rows] + ["─" * 36, line(t)]
    out = head + ["<pre>" + esc("\n".join(table)) + "</pre>"]
    out.append(f"✅ {t.won} réussi(s) · 🚨 {t.lost} stop loss d'urgence · ⏳ {t.open} en cours / interrompu(s)")
    if t.gains or t.losses:
        out.append(f"Gain moyen {money(t.avg_gain, cur, True)}" +
                   (f" · perte moyenne (SL) {money(-t.avg_loss, cur, True)}" if t.losses else ""))
    if be is not None:
        ok = t.rate is not None and t.rate >= be
        out.append(f"Seuil de rentabilité : <b>{be * 100:.0f} %</b> de réussite → "
                   + ("✅ atteint" if ok else "❌ <b>pas atteint</b>") + f" ({t.rate * 100:.0f} %)")
    done = [s for s in rows if s.done >= 3]
    if done:
        best = max(done, key=lambda s: s.net)
        worst = min(done, key=lambda s: s.net)
        out.append(f"🏆 Meilleur : <b>{best.slot}</b> ({money(best.net, cur, True)})"
                   + (f" · 👎 Pire : <b>{worst.slot}</b> ({money(worst.net, cur, True)})" if worst is not best and worst.net < 0 else ""))
    extra = []
    if t.skipped:
        extra.append(f"{t.skipped} créneau(x) sauté(s)")
    if t.cancelled:
        extra.append(f"{t.cancelled} annulé(s) (sans entrée ou coupure)")
    if t.session_stops:
        extra.append(f"{t.session_stops} limite(s) de perte de session")
    if extra:
        out.append("ℹ️ " + " · ".join(extra))
    out.append("<i>N = objectifs lancés ; ⚠ = sous le seuil de rentabilité. Il faut 2 à 3 semaines pour juger un créneau.</i>")
    return "\n".join(out)


def home_text(views: list[AccountView], t: Totals, vps: dict[str, float], cur: str, bot_v: str, ea_v: str,
              update_pending: bool, data_age_s: float | None, now: float, max_lines: int = 8,
              extra_alerts: list[str] | None = None) -> str:
    """Accueil compact : état du trading, argent, alertes, puis une ligne par compte (objectif 🎯 ou vers 500)."""
    c = "" if t.currency_mixed else cur
    tb = trading_block(views, now)
    lines = [f"📊 <b>ScalpX</b> {time.strftime('%H:%M', time.localtime(now))} · {tb[0]}", SEP]
    lines += tb[1:]
    lines.append(SEP)
    money_line = f"💰 <b>{money(t.balance, c)}</b> · jour {money(t.pl_jour, c, True)}"
    if t.pl_latent:
        money_line += f" · latent {money(t.pl_latent, c, True)}"
    lines.append(money_line)
    lines += withdraw_lines(views, c)
    alerts = [v for v in views if v.health.icon in ("🟠", "🔴")]
    lines += extra_alerts or []
    if alerts:
        for v in alerts[:3]:
            why = "; ".join(v.health.reasons[:2]) if v.health.reasons else "à vérifier"
            lines.append(f"{v.health.icon} <b>{esc(v.inst.label)}</b> : {esc(why)}")
        if len(alerts) > 3:
            lines.append(f"… et {len(alerts) - 3} autre(s) alerte(s)")
    others = "   ".join(f"{icon} {t.by_icon[icon]}" for icon, _ in ICON_ORDER if t.by_icon.get(icon, 0) and icon != "🟢")
    ok_line = f"🟢 {t.by_icon.get('🟢', 0)}/{t.count}" + (f"   {others}" if others else "")
    if not alerts and not extra_alerts:
        ok_line += " · ✅ aucune alerte"
    hot = [f"{k.upper()} {vps.get(k, 0):.0f}%" for k, lim in (("cpu", 85), ("ram", 85), ("disk", 90)) if vps.get(k, 0) >= lim]
    lines.append(ok_line + (" · ⚠️ VPS " + " ".join(hot) if hot else " · 🖥 VPS OK"))
    if update_pending:
        lines.append("🔔 Mise à jour disponible : 🛠 Système")
    old = sum(1 for v in views if v.outdated)
    if old:                                  # nouvel EA compilé dans BASE, pas encore déployé partout (rappel permanent)
        lines.append(f"🔔 EA {esc(ea_v or '?')} à déployer sur {old} compte(s) : ⚡ Actions → 🚀 Déployer l'EA")
    if data_age_s is not None and data_age_s > 45:
        lines.append(f"⚠️ données vieilles de {ago(data_age_s)}")
    lines.append(SEP)
    ref = trading_ref(views, now)
    if any(v.st is not None and v.st.objectif_actif for v in views):
        lines.append(goal_line(views, ref, now))
    else:
        lim = f"{ref.seuil_arret:.0f}" if ref is not None and ref.seuil_arret > 0 else "500"
        nxt = f" · prochain créneau {_hhmm(ref.prochain_creneau)}" if ref is not None and ref.prochain_creneau else ""
        lines.append(f"🏁 <b>Progression vers {lim}</b>{nxt}")
    shown = sorted(views, key=_home_order)[:max_lines]
    lines.append("<pre>" + "\n".join(esc(home_line(v)) for v in shown) + "</pre>")
    if len(views) > max_lines:
        lines.append(f"… et {len(views) - max_lines} autre(s) : 👥 Comptes")
    lines.append(f"<i>Bot {esc(bot_v)} · EA {esc(ea_v or '?')}</i>")
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
        f"📊 Flottant EA {money(st.flottant_ea, cur, True)} · Jour {money(st.day_realized(), cur, True)}",
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


def _dur(sec: float) -> str:
    s = int(sec)
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min"
    return f"{s // 3600} h {(s % 3600) // 60:02d}"


def labo_text(real, scen: dict, days: int, scope: str, cur: str, reset_ts: int = 0, active: bool = True,
              max_len: int = 3900) -> str:
    """🧪 Labo des filtres : qui bloque les objectifs réels, et ce qu'auraient donné les scénarios virtuels."""
    from core import labo
    head = [f"🧪 <b>Labo des filtres</b> · {days} j · {esc(scope)}"]
    if reset_ts:
        head.append(f"<i>Depuis la remise à zéro du {time.strftime('%d/%m %H:%M', time.localtime(reset_ts))}</i>")
    if not active:
        head.append("⏸ <b>Labo en pause</b> : séries virtuelles figées (le journal des blocages réels continue).")
    head.append("<i>Séries virtuelles sur les vrais prix (spread, slippage et commission mesurés). Aucun ordre réel.</i>")
    out = head + [""]
    if real.goals:
        out.append(f"<b>Réel</b> : {real.goals} objectif(s) · ✅ {real.won} · ⌛ {real.no_entry} sans série"
                   + (f" (dont {real.no_entry_filters} bloqué(s) par les filtres)" if real.no_entry else "")
                   + f" · {money(real.net, cur, True)}")
        if real.signal_s:
            out.append(f"Signal présent sans série : {_dur(real.signal_s)}, dont <b>{_dur(real.blocked_s)}</b> bloqué(s) par les filtres")
        blk = sorted((b for b in real.filters.values() if b.seconds > 0), key=lambda b: -b.seconds)
        if blk:
            out += ["", "<b>⏱ Qui bloque ?</b> <i>(signal présent, aucune série)</i>"]
            for b in blk[:8]:
                i = labo.CODES.index(b.code)
                out.append(f"• {labo.LABELS[i]} : {_dur(b.seconds)} (seul : {_dur(b.sole)})"
                           + (f" · <b>{b.goals_missed} objectif(s) raté(s)</b>" if b.goals_missed else ""))
    else:
        out.append("<i>Aucun objectif réel terminé sur la période (le journal se remplit à chaque objectif).</i>")

    ref = scen.get("miroir")
    rows = [s for s in scen.values() if s.launched or s.skipped]
    if not rows:
        out += ["", "<i>Aucun objectif virtuel terminé pour l'instant.</i>"]
        return "\n".join(out)
    order = {"miroir": 0, "aucun": 2}
    rows.sort(key=lambda s: (order.get(s.key, 3 if s.key.startswith("p") else 1),
                             -(s.net - (ref.net if ref else 0))))
    out += ["", "<b>🧬 Scénarios virtuels</b> <i>(✅ atteints · 💀 SL / stop-out · net · drawdown max)</i>"]
    for s in rows:
        v = labo.verdict(s, ref)
        name = s.name
        if s.key.startswith("p") and ref is not None:
            name = labo.spec_diff_text(s.spec, ref.spec) or s.name
        rate = f" ({s.rate * 100:.0f} %)" if s.rate is not None else ""
        d_won = f" ({s.won - ref.won:+d})" if ref is not None and s is not ref else ""
        d_net = f" ({s.net - ref.net:+.2f})" if ref is not None and s is not ref else ""
        line = (f"{v.icon + ' ' if v.icon else ''}<b>{esc(name)}</b> : {s.launched} obj · ✅ {s.won}{rate}{d_won}"
                f" · 💀 {s.lost} · {money(s.net, cur, True)}{d_net} · DD {s.dd_max:.1f} %")
        if v.text:
            line += f"\n   ↳ {v.text}"
        out.append(line)
    out += ["", "<i>🟢 trop restrictif / meilleur · 🛡️ évite des pertes · 🔴 moins bon · ⚪ équivalent · "
                f"⏳ moins de {labo.MIN_GOALS} objectifs. Juger sur 2 semaines au moins.</i>"]
    text = "\n".join(out)
    if len(text) > max_len:                         # trop long pour Telegram : verdicts en abrégé (icônes), puis lignes en moins
        out = [l.split("\n   ↳")[0] for l in out]
        while len("\n".join(out)) > max_len and len(out) > 3:
            out.pop(-2)
        text = "\n".join(out)
    return text
