"""Écrans Telegram du backtest (📈 Rapports → 🧪 Backtest) : réglages du test, suivi, historique, configuration du compte."""
from __future__ import annotations

import time

from core import backtest as bt
from . import render
from .render import esc
from .ui import Keyboard


def _cut(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


def source_label(dash, src: str) -> str:
    """Réglages utilisés : un compte (ses réglages actuels, lus dans l'EA), un preset, ou le .set de BASE."""
    if src.startswith("preset:"):
        return f"preset {src[7:]}"
    if src == "base":
        return "réglages de BASE (.set)"
    inst = dash.ctx.db.get(src) if src else None
    if inst is None:
        insts = dash.ctx.db.list()
        inst = insts[0] if insts else None
    return f"compte {inst.label} (réglages actuels)" if inst is not None else "réglages de BASE (.set)"


def sources(dash) -> list[str]:
    return [i.id for i in dash.ctx.db.list()] + [f"preset:{n}" for n in dash.store.names()] + ["base"]


def render_main(dash, now: float) -> tuple[str, Keyboard]:
    mgr = dash.bt
    acc = mgr.account() if mgr else None
    lines = ["🧪 <b>Backtest</b>",
             "<i>Le robot complet (créneaux, objectifs, coupures, seuil 500, filtres et news) rejoué dans le testeur MT5 "
             "d'un terminal à part, sur les ticks réels du broker. Aucun ordre réel.</i>", ""]
    kb: Keyboard = []
    if mgr is None or not mgr.configured():
        lines += ["⚙️ <b>Terminal de backtest à configurer</b>",
                  "Je copie BASE dans un dossier à part et je connecte un compte du broker (démo ou réel : "
                  "le testeur ne passe aucun ordre). Il me faut le numéro du compte, le serveur et le mot de passe."]
        if mgr is not None and mgr.busy():
            lines.append("\n⏳ Configuration en cours…")
        else:
            kb.append([("⚙️ Configurer le terminal de backtest", "d:btc")])
        kb.append([("⬅ Rapports", "d:rpt"), ("🏠 Accueil", "d:home")])
        return dash._with_flash("\n".join(lines)), kb
    if mgr.busy():
        r = mgr.run
        frac = bt.progress_frac(r.progress, r.d_from, r.d_to)
        lines += [f"⏳ <b>Backtest #{r.rid} en cours</b> : {render.bar(frac, 10)} {frac * 100:.0f} %", esc(r.stage),
                  "<i>Le suivi détaillé est dans le message du backtest.</i>"]
        kb.append([("⏹ Arrêter le backtest", "d:btx")])
    else:
        days = dash.bt_days
        d_from, d_to = bt.date_range(days, now)
        lines += [f"📅 Période : <b>{bt.period_label(days)}</b> ({bt.human_date(d_from)} → {bt.human_date(d_to)})",
                  f"🎚 Modèle : <b>{esc(bt.MODELS[dash.bt_model][1])}</b>",
                  f"⚙️ Réglages : <b>{esc(source_label(dash, dash.bt_src))}</b>",
                  f"🔑 Compte du testeur : {acc['login']} · {esc(acc['server'])}"]
        if dash.bt_model == "reel" and days > 90:
            lines.append("<i>⚠️ Ticks réels sur plus de 3 mois : long (souvent plus d'une heure) et gros téléchargement la première fois.</i>")
        pr = [(("• " if d == days else "") + lbl, f"d:bt:{d}") for d, lbl in bt.PERIODS]
        kb += [pr[:4], pr[4:], [("📅 Autre date de début", "d:btd")],
               [("🎚 " + ("Ticks réels" if dash.bt_model == "reel" else "OHLC M1") + " ▸", "d:btm"),
                ("⚙️ " + _cut(source_label(dash, dash.bt_src), 22) + " ▸", "d:bts")],
               [("▶️ Lancer le backtest", "d:btgo")]]
    kb.append([("🧪 Tests rapides par thème", "d:qt")])
    kb.append([("🗂 Derniers backtests", "d:btl"), ("🔑 Changer de compte", "d:btc")])
    kb.append([("⬅ Rapports", "d:rpt"), ("🏠 Accueil", "d:home")])
    return dash._with_flash("\n".join(lines)), kb


STATUS = {"ok": "✅", "en_cours": "⏳", "erreur": "❌", "arrete": "⏹", "interrompu": "⚠️"}


def render_list(dash) -> tuple[str, Keyboard]:
    rows = dash.ctx.db.bt_list(8)
    lines = ["🗂 <b>Derniers backtests</b>", ""]
    kb: Keyboard = []
    for r in rows:
        days = max(1, round((r["d_to"] - r["d_from"]) / 86400))
        lab = f"#{r['id']} · {bt.period_label(days)} · {time.strftime('%d/%m %H:%M', time.localtime(r['created']))}"
        lines.append(f"{STATUS.get(r['status'], '•')} {lab} · {esc(r['source'])}" + (" · labo" if r["kind"] == "labo" else "")
                     + (f"\n   ↳ {esc(r['error'])}" if r["error"] else ""))
        if r["status"] == "ok":
            kb.append([(f"📊 #{r['id']}", f"d:btr:{r['id']}")])
    if not rows:
        lines.append("<i>Aucun backtest pour l'instant.</i>")
    kb.append([("⬅ Backtest", "d:bt"), ("🏠 Accueil", "d:home")])
    return dash._with_flash("\n".join(lines)), kb


def render_wizard(dash) -> tuple[str, Keyboard]:
    step, data = dash.bt_wiz
    kb: Keyboard = []
    if step == "login":
        text = ("🔑 <b>Compte du terminal de backtest</b> (1/3)\n\nEnvoie le <b>numéro du compte</b> (login) chez le broker.\n"
                "<i>Démo ou réel : le testeur ne passe aucun ordre.</i>")
    elif step == "server":
        text = f"🔑 <b>Compte {data.get('login')}</b> (2/3)\n\nChoisis le <b>serveur</b> du broker :"
        for i, s in enumerate(data.get("servers", [])[:8]):
            kb.append([(s, f"d:btsv:{i}")])
        kb.append([("✏️ Autre serveur…", "d:btsv:new")])
    elif step == "server_text":
        text = f"🔑 <b>Compte {data.get('login')}</b> (2/3)\n\nEnvoie le <b>nom exact du serveur</b> (ex. Broker-Live)."
    else:
        text = (f"🔑 <b>Compte {data.get('login')} · {esc(data.get('server', ''))}</b> (3/3)\n\nEnvoie le <b>mot de passe</b>.\n"
                "<i>Ton message est effacé aussitôt ; MT5 mémorise le mot de passe, le bot ne le garde pas.</i>")
    kb.append([("✖ Annuler", "d:btq")])
    return dash._with_flash(text), kb


QT_DAYS = (7, 30, 90, 182)


def render_quick(dash, now: float) -> tuple[str, Keyboard]:
    """🧪 Tests rapides : un bouton = les scénarios d'un thème installés + un backtest lancé."""
    from core import labo
    mgr = dash.bt
    days = dash.qt_days
    d_from, d_to = bt.date_range(days, now)
    lines = ["🧪 <b>Tests rapides</b>",
             "Un bouton = je remplace tes scénarios perso du labo par ceux du thème, puis je lance <b>un backtest</b> qui joue, "
             "sur la même période et les mêmes prix, tes réglages actuels + les 13 scénarios automatiques + ceux du thème. "
             "Le résultat les classe, avec un verdict pour chacun.", "",
             f"📅 Période : <b>{bt.period_label(days)}</b> ({bt.human_date(d_from)} → {bt.human_date(d_to)}) · "
             f"{esc(bt.MODELS[dash.bt_model][1])}",
             f"⚙️ Réglages de base : {esc(source_label(dash, dash.bt_src))}", ""]
    for key, (title, desc, items) in labo.THEMES.items():
        lines.append(f"<b>{esc(title)}</b> : {esc(desc)}")
        if items:
            lines.append("   ↳ " + esc(" · ".join(n for n, _, _ in items)))
    kb: Keyboard = [[(("• " if d == days else "") + bt.period_label(d), f"d:qtd:{d}") for d in QT_DAYS]]
    if mgr is not None and mgr.busy():
        lines += ["", f"⏳ Backtest #{mgr.run.rid} en cours : attends sa fin (ou ⏹ dans le message de suivi)."]
    elif mgr is None or not mgr.configured():
        lines += ["", "⚙️ Configure d'abord le terminal de backtest (🧪 Backtest)."]
        kb.append([("⚙️ Configurer le terminal de backtest", "d:btc")])
    else:
        for key, (title, _, _) in labo.THEMES.items():
            kb.append([(f"▶️ {title}", f"d:qtgo:{key}")])
    lines += ["", "<i>Plus la période est longue, plus le verdict est fiable (7 j ≈ 25 objectifs : indicatif). "
                  "Les scénarios du thème restent ensuite dans le labo en direct.</i>"]
    kb.append([("🧪 Labo", "d:lab"), ("🧪 Backtest", "d:bt"), ("🏠 Accueil", "d:home")])
    return dash._with_flash("\n".join(lines)), kb
