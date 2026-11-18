"""Écrans Telegram du terminal de test : compte (🤖 Optimiseur → 🔑 Terminal de test) et 🧪 Backtest MT5 (📈 Rapports)."""
from __future__ import annotations

import time

from core import backtest as bt
from . import render
from .render import esc
from .ui import Keyboard


def _cut(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


def render_wizard(dash) -> tuple[str, Keyboard]:
    step, data = dash.bt_wiz
    kb: Keyboard = []
    if step == "login":
        text = ("🔑 <b>Compte du terminal de test</b> (1/3)\n\nEnvoie le <b>numéro du compte</b> (login) chez le broker.\n"
                "<i>Démo ou réel : le testeur de l'optimiseur ne passe aucun ordre.</i>")
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


# ------------------------------------------------------------------ 🧪 Backtest MT5 lancé depuis Telegram (Bot 4.13.0)
MANUAL_KIND = "manuel"
MANUAL_PERIODS = [(1, "1 j"), (7, "1 sem"), (30, "1 mois"), (90, "3 mois"), (182, "6 mois"), (274, "9 mois"), (365, "12 mois")]
META_STATE = "bm_state"


def _num(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


class ManualSettings:
    """Réglages du prochain backtest : preset, date de début, minimum et maximum (None = valeurs du preset)."""

    def __init__(self, dash) -> None:
        self.dash = dash
        self.preset, self.days, self.lo, self.hi = "", 30, None, None
        try:
            import json
            d = json.loads(dash.ctx.db.meta_get(META_STATE, "") or "{}")
            self.preset = str(d.get("preset", "") or "")
            self.days = int(d.get("days", 30) or 30)
            self.lo = float(d["lo"]) if d.get("lo") is not None else None
            self.hi = float(d["hi"]) if d.get("hi") is not None else None
        except (ValueError, TypeError, KeyError):
            pass
        self.days = max(1, min(bt.MAX_DAYS, self.days))

    def save(self) -> None:
        import json
        self.dash.ctx.db.meta_set(META_STATE, json.dumps({"preset": self.preset, "days": self.days, "lo": self.lo, "hi": self.hi}))

    # ---- preset (⭐ actif par défaut)
    def preset_name(self) -> str:
        store = self.dash.store
        if self.preset in store.presets:
            return self.preset
        return store.active if store.active in store.presets else (store.names()[0] if store.names() else "")

    def next_preset(self) -> None:
        names = self.dash.store.names()
        if not names:
            return
        cur = self.preset_name()
        self.preset = names[(names.index(cur) + 1) % len(names)] if cur in names else names[0]
        self.lo = self.hi = None                            # nouveau preset : ses propres minimum / maximum
        self.save()

    def values(self) -> dict[str, str]:
        return dict(self.dash.store.presets.get(self.preset_name(), {}))

    # ---- minimum / maximum
    def preset_lo_hi(self) -> tuple[float, float]:
        v = self.values()
        try:
            lo = float(v.get("seuil_reprise", "") or 0)
        except ValueError:
            lo = 0.0
        try:
            hi = float(v.get("seuil_arret", "") or 0)
        except ValueError:
            hi = 0.0
        return (lo if lo > 0 else 200.0), (hi if hi > 0 else 500.0)

    def lo_hi(self) -> tuple[float, float]:
        plo, phi = self.preset_lo_hi()
        return (self.lo if self.lo is not None else plo), (self.hi if self.hi is not None else phi)

    def set_limit(self, which: str, text: str) -> str:
        """Saisie du minimum (« lo ») ou du maximum (« hi ») : message d'erreur, ou "" si accepté."""
        t = text.strip().replace(" ", "").replace("€", "").replace("$", "").replace(",", ".")
        try:
            v = round(float(t), 2)
        except ValueError:
            return "❌ Montant non reconnu (ex. 200 ou 500)."
        lo, hi = self.lo_hi()
        if which == "lo":
            if not 1 <= v < hi:
                return f"❌ Le minimum doit être entre 1 et le maximum ({_num(hi)})."
            self.lo = v
        else:
            if not lo < v <= 1_000_000:
                return f"❌ Le maximum doit être au-dessus du minimum ({_num(lo)})."
            self.hi = v
        self.save()
        return ""

    def reset_limits(self) -> None:
        self.lo = self.hi = None
        self.save()

    def params(self) -> dict[str, str]:
        """Réglages envoyés à l'EA du testeur : le preset, avec le minimum et le maximum du test, sans le labo."""
        lo, hi = self.lo_hi()
        v = self.values()
        v.update({"seuil_on": "1", "seuil_reprise": _num(lo), "seuil_arret": _num(hi), "bt_sans_labo": "1"})
        return v


def render_manual(dash, now: float) -> tuple[str, Keyboard]:
    mgr, s = dash.bt, dash.bm
    store = dash.store
    lines = ["🧪 <b>Backtest MT5</b>",
             "<i>Le vrai testeur de stratégie MT5 rejoue ScalpX sur les ticks réels du broker, avec la stratégie complète "
             "(objectifs, mode continu ou créneaux, coupures, filtres, news). Aucun ordre réel.</i>", ""]
    kb: Keyboard = []
    if mgr is None or not mgr.configured():
        lines += ["⚙️ <b>Terminal de test à configurer</b>",
                  "Il me faut un compte du broker (démo ou réel : le testeur ne passe aucun ordre) : numéro, serveur, mot de passe."]
        if mgr is not None and mgr.busy():
            lines.append("\n⏳ Configuration en cours…")
        else:
            kb.append([("🔑 Configurer le terminal de test", "d:btc")])
        kb.append([("⬅ Rapports", "d:rpt"), ("🏠 Accueil", "d:home")])
        return dash._with_flash("\n".join(lines)), kb
    if mgr.busy():
        r = mgr.run
        frac = bt.progress_frac(r.progress, r.d_from, r.d_to)
        what = "Optimisation de nuit" if r.kind == "opt" else f"Backtest #{r.rid}"
        lines += [f"⏳ <b>{what} en cours</b> : {render.bar(frac, 10)} {frac * 100:.0f} %", esc(r.stage),
                  "<i>Un seul test à la fois : attends sa fin.</i>"]
        if r.kind == MANUAL_KIND:
            kb.append([("⏹ Arrêter le backtest", "d:btx")])
    name = s.preset_name()
    if not name:
        lines.append("❌ Aucun preset : crée d'abord un preset (⚙️ Réglages → 💾 Presets).")
        kb.append([("⬅ Rapports", "d:rpt"), ("🏠 Accueil", "d:home")])
        return dash._with_flash("\n".join(lines)), kb
    lo, hi = s.lo_hi()
    plo, phi = s.preset_lo_hi()
    cur = next((v.st.devise for v in dash.ctx.views() if v.st and v.st.devise), "")
    d_from, d_to = bt.date_range(s.days, now)
    star = "⭐ " if name == store.active else ""
    acc = mgr.account() or {}
    lines += [f"⚙️ Preset : <b>{star}{esc(name)}</b>",
              f"📅 Du <b>{bt.human_date(d_from)}</b> au {bt.human_date(d_to)} ({bt.period_label(s.days)})",
              f"⬇️ Minimum : <b>{render.money(lo, cur)}</b>" + ("" if lo == plo else " <i>(changé pour ce test)</i>")
              + " · départ, et recharge après un cycle perdu",
              f"⬆️ Maximum : <b>{render.money(hi, cur)}</b>" + ("" if hi == phi else " <i>(changé pour ce test)</i>")
              + " · retrait jusqu'au minimum = cycle gagné",
              f"🎚 Ticks réels · 🔑 testeur : compte {acc.get('login', '?')} · {esc(acc.get('server', '?'))}"]
    if s.days > 90:
        lines.append("<i>⚠️ Plus de 3 mois en ticks réels : long (souvent plus d'une heure) et gros téléchargement la première fois.</i>")
    pr = [(("• " if d == s.days else "") + lbl, f"d:bmd:{d}") for d, lbl in MANUAL_PERIODS]
    if s.days not in [d for d, _ in MANUAL_PERIODS]:
        pr.append((f"• {bt.human_date(d_from)}", "d:bmdt"))
    kb += [[("⚙️ " + _cut(star + name, 26) + " ▸", "d:bmp")], pr[:4], pr[4:] + [("📅 Autre date", "d:bmdt")],
           [(f"⬇️ Min {_num(lo)}", "d:bmlo"), (f"⬆️ Max {_num(hi)}", "d:bmhi")]]
    if lo != plo or hi != phi:
        kb.append([("↺ Min / max du preset", "d:bmrs")])
    if not mgr.busy():
        kb.append([("▶️ Lancer le backtest", "d:bmgo")])
    kb.append([("🗂 Derniers backtests", "d:bml")])
    kb.append([("⬅ Rapports", "d:rpt"), ("🏠 Accueil", "d:home")])
    return dash._with_flash("\n".join(lines)), kb


STATUS = {"ok": "✅", "en_cours": "⏳", "erreur": "❌", "arrete": "⏹", "interrompu": "⚠️"}


def render_list(dash) -> tuple[str, Keyboard]:
    rows = [r for r in dash.ctx.db.bt_list(60) if r["kind"] == MANUAL_KIND][:8]
    lines = ["🗂 <b>Derniers backtests</b>", ""]
    kb: Keyboard = []
    for r in rows:
        days = max(1, round((r["d_to"] - r["d_from"]) / 86400))
        lines.append(f"{STATUS.get(r['status'], '•')} #{r['id']} · {time.strftime('%d/%m %H:%M', time.localtime(r['created']))} · "
                     f"{bt.period_label(days)} · {esc(r['source'])}" + (f"\n   ↳ {esc(r['error'])}" if r["error"] else ""))
        if r["status"] == "ok":
            kb.append([(f"📊 Résultat #{r['id']}", f"d:bmr:{r['id']}"), (f"📈 Graphique #{r['id']}", f"d:bmg:{r['id']}")])
    if not rows:
        lines.append("<i>Aucun backtest pour l'instant.</i>")
    kb.append([("⬅ Backtest", "d:bm"), ("🏠 Accueil", "d:home")])
    return dash._with_flash("\n".join(lines)), kb
