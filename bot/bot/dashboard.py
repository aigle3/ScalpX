"""Tableau de bord VIVANT : un seul message (non épinglé), ÉDITÉ en place (accueil, liste, fiche compte, confirmations).

Mis à jour toutes les `refresh_s` secondes (10 s) tant qu'il est le dernier message, seulement si le contenu a changé ;
recouvert par d'autres messages depuis `repost_s` (5 min) : republié en bas, en silence. Gère RetryAfter,
« message is not modified » et la suppression manuelle du message (recréé). L'identifiant du message est conservé
en SQLite.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time

from core.summary import FILTERS, FILTER_LABELS, SORTS, SORT_LABELS, apply_filter, apply_sort, totals
from mt5.procs import vps_load
from . import render
from .actions import CARD_CMDS, Ctx, target as act_target
from .params_ui import ParamsUI
from . import presets_ui as pui
from core.presets import PresetStore
from storage.db import MAINTENANCE
from .ui import ALL_ID, Confirmations, EditResult, Keyboard, Sender, confirm_kb, short

log = logging.getLogger(__name__)

# Choix de pause : (clé du bouton, libellé, argument de la commande PAUSE de l'EA)
PAUSE_CHOICES = [("0", "Jusqu'à ▶ Reprise", ""), ("1h", "1 h", "1h"), ("2h", "2 h", "2h"), ("4h", "4 h", "4h"),
                 ("t8", "🌙 Jusqu'à 08:00", "08:00")]


def _cut(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


class Dashboard:
    def __init__(self, ctx: Ctx, sender: Sender, chat_id: int, confirmations: Confirmations, vps=vps_load) -> None:
        self.ctx, self.sender, self.chat_id, self.conf = ctx, sender, chat_id, confirmations
        self.vps = vps
        self.store = PresetStore(ctx.cfg.paths.data / "presets.json", ctx.cfg.paths.common())   # 💾 presets centraux
        self.store.export()                              # Bot 3.14.0 : preset ⭐ pour le testeur MT5 (au démarrage aussi)
        self.preset_check: pui.Check | None = None          # dernier contrôle des robots / preset actif
        self.params = ParamsUI(ctx, self.store)
        self.filters: tuple[float, str, str] | None = None  # (heure, compte de référence, texte FILTRES de l'EA)
        self.silent = False                                  # mode silencieux (libellé du bouton de l'écran Actions)
        self.screen: tuple = ("dash",)
        self.confirm_back: tuple = ("dash",)
        self.flt, self.sort, self.page = "all", "nom", 0
        self._flash: tuple[str, float] | None = None
        self._last_sig = ""
        self._last_edit = 0.0
        self._retry_until = 0.0
        self._covered_at: float | None = None               # depuis quand un autre message est sous le tableau de bord
        self.bt = None                                      # mt5.backtester.BacktestManager (branché par le routeur)
        self.bt_wiz: tuple[str, dict] = ("login", {})       # configuration du compte du testeur (étape, données)
        self.optim = None                                   # bot.optim.Optimizer (branché par le routeur)
        self._load_state()

    # ---- état persistant (filtre / tri / page)
    def _load_state(self) -> None:
        try:
            d = json.loads(self.ctx.db.meta_get("dash_state", "{}"))
            self.flt = d.get("flt", "all") if d.get("flt") in FILTERS else "all"
            self.sort = d.get("sort", "nom") if d.get("sort") in SORTS else "nom"
            self.page = int(d.get("page", 0))
        except (ValueError, TypeError):
            pass

    def _save_state(self) -> None:
        self.ctx.db.meta_set("dash_state", json.dumps({"flt": self.flt, "sort": self.sort, "page": self.page}))

    # ---- navigation
    def goto(self, *screen) -> None:
        self.screen = tuple(screen)

    def set_filter(self, f: str) -> None:
        if f in FILTERS:
            self.flt, self.page = f, 0
            self._save_state()

    def set_sort(self, s: str) -> None:
        if s in SORTS:
            self.sort, self.page = s, 0
            self._save_state()

    def set_page(self, p: int) -> None:
        self.page = max(0, p)
        self._save_state()

    def flash(self, text: str, ttl: float = 25.0) -> None:
        """Ligne d'information temporaire (⏳ envoyé → ✅ exécuté / ❌ échec)."""
        self._flash = (text, time.time() + ttl)

    def _flash_text(self) -> str:
        if self._flash and self._flash[1] > time.time():
            return self._flash[0]
        self._flash = None
        return ""

    # ---- rendu
    def render(self, now: float | None = None) -> tuple[str, Keyboard]:
        now = now if now is not None else time.time()
        kind = self.screen[0]
        if kind == "confirm":
            p = self.conf.peek(self.screen[1])
            if p is None:
                self.screen = ("dash",)
            else:
                return p.text, confirm_kb(self.screen[1])
        if kind == "acc":
            r = self._render_card(self.screen[1], now)
            if r:
                return r
            self.screen = ("dash",)
        if kind == "cmds":
            return self._render_cmds(self.screen[1])
        if kind == "psl":
            return pui.render_list(self.store, self.preset_check, now, self._flash_text())
        if kind == "pso":
            if self.screen[1] in self.store.presets:
                return pui.render_one(self.store, self.screen[1], self.preset_check, self._flash_text())
            return pui.render_list(self.store, self.preset_check, now, self._flash_text())
        if kind == "psn":
            insts = [i for i in self.ctx.db.list() if self.ctx.mgr.is_running(i)]
            return pui.render_source(insts, self.screen[1] if len(self.screen) > 1 else "", self._flash_text())
        if kind == "sys":
            from core import update as upd
            from .system import BOT_ROOT, system_kb, system_text
            fl = self._flash_text()
            return ((f"{fl}\n\n" if fl else "") + system_text(self.ctx, self.vps(self.ctx.cfg.paths.data)),
                    system_kb(bool(self.ctx.db.meta_get("update_pending")), upd.backup_version(BOT_ROOT),
                              install=bool(self.ctx.db.meta_get("install_pending"))))
        if kind in ("prm", "prg", "prp", "prc", "prh", "prq", "puc", "puh", "puq"):
            sid = self.screen[1]
            if sid.startswith(pui.PREFIX):                  # réglages d'un preset (gardé par le bot)
                name = sid[len(pui.PREFIX):]
                inst = pui.PresetTarget(name) if name in self.store.presets else None
            else:
                inst = act_target(self.ctx, sid)
            if inst is not None:
                return self.params.render(self.screen, inst.label, self._flash_text())
            self.screen = ("dash",)
        if kind == "term":
            r = self._render_term(self.screen[1])
            if r:
                return r
            self.screen = ("dash",)
        if kind == "obj":
            r = self._render_goal(self.screen[1])
            if r:
                return r
            self.screen = ("dash",)
        if kind == "gcmd":
            return self._render_cmds(ALL_ID)
        if kind == "pz":
            return self._render_pause(self.screen[1])
        if kind == "lst":
            return self._render_list(now)
        if kind == "act":
            return self._render_actions()
        if kind == "rpt":
            return self._render_reports()
        if kind == "key":                                   # Bot 3.18.0 : 🔑 clés et jetons
            from .system import keys_screen
            return keys_screen(self.ctx, self._flash_text())
        if kind == "srv":                                   # Bot 3.18.0 : 🖥 Serveur (Linux)
            from . import server_ui as sui
            st, auto, sudo = getattr(self, "srv", (None, {}, True))
            return sui.server_screen(st, auto, sudo, self._flash_text())
        if kind == "plex":                                  # Bot 3.8.0
            from .system import plex_screen
            return plex_screen(self.ctx, self._flash_text())
        if kind == "logs":
            return self._render_logs()
        if kind == "flt":
            return self._render_filters(now)
        if kind == "sst":
            return self._render_slot_stats(now)
        if kind == "opt" and self.optim is not None:
            o = self.optim
            kb: Keyboard = [
                [("⏸ Suspendre" if o.on else "✅ Activer", "d:opton"), (f"🕐 {o.hour:02d}:00 ▸", "d:opth")],
                [("▶️ Lancer maintenant", "d:optgo"), ("📜 Journal", "d:optj")],
                [("⏹ Arrêter la série", "d:opsx") if o.series_active else ("🔁 Série d'optimisations", "d:ops")],
                [("🔑 Terminal de test", "d:btc"), ("♻️ Réinitialiser", "d:optr")],
                [("❓ Guide", "d:optg"), ("⬅ Rapports", "d:rpt"), ("🏠 Accueil", "d:home")],
            ]
            if self.bt is not None and self.bt.busy():
                kb.insert(2, [("⏹ Arrêter le test en cours", "d:btx")])
            last = next((r for r in self.ctx.db.opt_list(5) if r["status"] == "change" and not r["reverted"]), None)
            if last is not None:
                kb.insert(2, [(f"↩️ Annuler le dernier changement", f"d:opu:{last['id']}")])
            return self._with_flash(o.status_text()), kb
        if kind == "ops" and self.optim is not None:        # Bot 3.6.0 : 🔁 nombre d'optimisations de la série
            o = self.optim
            from core import optim as copt
            order = []
            gi = o.group_index()
            for k in range(6):
                order.append(copt.GROUPS[(gi + k) % len(copt.GROUPS)][1].split(" ", 1)[0])
            text = ("🔁 <b>Série d'optimisations</b>\n"
                    "Choisis combien d'optimisations enchaîner, dans l'ordre du cycle :\n"
                    + ("🛟 → " if o.safety_next else "") + " → ".join(order) + " …\n"
                    "<i>≈ 15 à 30 min chacune (50 ≈ 12 à 25 h) · l'optimisation de la nuit est sautée pendant la série.</i>")
            row1 = [(str(n), f"d:ops:{n}") for n in o.SERIES_CHOICES[:4]]
            row2 = [(str(n), f"d:ops:{n}") for n in o.SERIES_CHOICES[4:]]
            return self._with_flash(text), [row1, row2, [("⬅ Optimiseur", "d:opt"), ("🏠 Accueil", "d:home")]]
        if kind == "btw":                                   # 🔑 compte du terminal de test (optimiseur)
            from . import backtest_ui as bui
            return bui.render_wizard(self)
        if kind == "wdr":
            return self._render_withdrawals(now)
        if kind == "gterm":
            return self._render_gterm()
        return self._render_home(now)

    # ---- accueil : l'état de l'ensemble d'un coup d'œil, 4 menus
    def _with_flash(self, text: str) -> str:
        fl = self._flash_text()
        return f"{fl}\n\n{text}" if fl else text

    def _render_home(self, now: float) -> tuple[str, Keyboard]:
        from core.version import bot_version
        from mt5 import base as basemod
        views = self.ctx.views()
        pending = bool(self.ctx.db.meta_get("update_pending") or self.ctx.db.meta_get("install_pending"))
        sys_btn = ("🛠 Système" + (" 🔔" if pending else ""), "d:sys")
        if not views:
            kb: Keyboard = [[("➕ Nouveau compte", "n"), ("📄 Comptes (fichier)", "d:bulk")], [sys_btn]]
            return self._with_flash(render.welcome_empty()), kb
        t = totals(views)
        cur = next((v.st.devise for v in views if v.st and v.st.devise), "")
        vps = self.vps(self.ctx.cfg.paths.data)
        ages = [v.st.age_s(now) for v in views if v.st]
        text = render.home_text(views, t, vps, cur, bot_version(), basemod.read_version(self.ctx.cfg.paths.base),
                                pending, min(ages) if ages else None, now,
                                extra_alerts=[a for a in [pui.alert_line(self.preset_check)] if a])
        act_btn = ("⚡ Actions" + (" 🔔" if any(v.outdated for v in views) else ""), "d:act")
        ndue = len(render.due_withdrawals(views))
        kb = [[("🧭 Filtres et positions", "d:flt"), ("💸 Retraits" + (f" 🔔 {ndue}" if ndue else ""), "d:wdr")],
              [("👥 Comptes", "d:lst"), act_btn],
              [("📈 Rapports", "d:rpt"), sys_btn]]
        return self._with_flash(text), kb

    # ---- 👥 comptes : liste détaillée, filtre et tri (un bouton chacun, qui fait défiler les choix)
    def _render_list(self, now: float) -> tuple[str, Keyboard]:
        views = self.ctx.views()
        filtered = apply_sort(apply_filter(views, self.flt), self.sort)
        page_views, self.page, pages = render.paginate(filtered, self.page, self.ctx.cfg.dashboard.page_size)
        text = render.list_text(page_views, len(views), self.page, pages, self.flt, self.sort)
        kb: Keyboard = [[(f"{v.health.icon} {v.inst.label[:24]}", f"a:{short(v.inst.id)}")] for v in page_views]
        if not filtered and views:                       # filtre vide (ex. « Alertes » quand tout va bien) : retour en un geste
            kb.append([(f"👥 Voir les {len(views)} comptes", "d:fa")])
        kb.append([(f"🔎 {FILTER_LABELS[self.flt]}", "d:fc"), (f"↕ {SORT_LABELS[self.sort]}", "d:sc")])
        if pages > 1:
            kb.append([("◀", f"d:p:{self.page - 1}"), (f"{self.page + 1}/{pages}", "d:r"), ("▶", f"d:p:{self.page + 1}")])
        kb.append([("⬅ Accueil", "d:home"), ("➕ Nouveau compte", "n")])
        kb.append([("📄 Ajouter des comptes (fichier)", "d:bulk")])                 # Bot 3.20.0
        return self._with_flash(text), kb

    def _render_actions(self) -> tuple[str, Keyboard]:
        silent = self.silent
        old = sum(1 for v in self.ctx.views() if v.outdated)
        text = ("⚡ <b>Actions sur tous les comptes</b> (terminaux lancés)\n\n"
                "Objectif, réglages et commandes partent vers chaque compte, avec confirmation.\n"
                "Pour un seul compte : 👥 Comptes → le compte.")
        kb: Keyboard = [
            [("⏸ Pause (tous)", "d:pall"), ("▶ Tout reprendre", "d:rall")],
            [("🎯 Objectif (tous)", "g:all"), ("⚙️ Paramètres (tous)", "p:all")],
            [("🧰 Commandes (tous)", "d:gcmd"), ("🖥 Terminaux (tous)", "d:gterm")],
            [("💾 Presets (tous les robots)", "s:l")],
            [("🚀 Déployer l'EA" + (f" 🔔 {old}" if old else ""), "d:dep"), ("➕ Nouveau compte", "n")],
            [(("🔔 Rétablir le son" if silent else "🔕 Mode silencieux"), "d:sil")],
            [("⬅ Accueil", "d:home")],
        ]
        return self._with_flash(text), kb

    def withdraw_since(self) -> dict[str, int]:
        out = {}
        for i in self.ctx.db.list():
            try:
                t = int(self.ctx.db.meta_get(f"arret500_since_{i.id}", "") or 0)
            except ValueError:
                t = 0
            if t:
                out[i.id] = t
        return out

    def _render_withdrawals(self, now: float) -> tuple[str, Keyboard]:
        views = self.ctx.views()
        db = self.ctx.db
        hist = list(reversed(db.withdrawals_since(int(now) - 365 * 86400)))
        cur = next((v.st.devise for v in views if v.st and v.st.devise), "")
        labels = {i.id: i.label for i in db.list(include_archived=True)}
        text = render.withdraw_text(views, hist, db.total_withdrawn(), sum(r["amount"] for r in db.withdrawals_since(int(now) - 30 * 86400)),
                                    self.withdraw_since(), cur, now, labels)
        kb: Keyboard = [[(f"🏁 {v.inst.label[:24]}", f"a:{short(v.inst.id)}")] for v in render.due_withdrawals(views)[:6]]
        kb.append([("🔃 Actualiser", "d:wdr"), ("⬅ Accueil", "d:home")])
        return self._with_flash(text), kb

    SST_DAYS = (7, 30, 90)

    def _render_slot_stats(self, now: float) -> tuple[str, Keyboard]:
        from core import slotstats
        days = getattr(self, "sst_days", 30)
        db = self.ctx.db
        evs = [e for e in db.events_since(int(now) - days * 86400)
               if e["type"] in ("OBJECTIF_AUTO", "CRENEAU_SAUTE", "OBJECTIF_ATTEINT", "OBJECTIF_ANNULE", "OBJECTIF_COUPE", "CLOTURE_500_OK",
                                 "ARRET_500", "NOTIF")]
        stats = slotstats.compute(evs)
        views = self.ctx.views()
        cur = next((v.st.devise for v in views if v.st and v.st.devise), "")
        text = render.slot_stats_text(stats, days, len(views), cur)
        kb: Keyboard = [[(("• " if d == days else "") + f"{d} j", f"d:sst:{d}") for d in self.SST_DAYS],
                        [("⬅ Rapports", "d:rpt"), ("🏠 Accueil", "d:home")]]
        return self._with_flash(text), kb

    def _render_reports(self) -> tuple[str, Keyboard]:
        text = "📈 <b>Rapports</b>\n\nLe rapport quotidien arrive aussi tout seul chaque soir."
        kb: Keyboard = [
            [("📝 Rapport des 24 h", "d:rep"), ("📈 Courbe des soldes", "d:chart")],
            [("🕐 Stats par créneau", "d:sst"), ("📤 Export CSV", "d:exp")],
            [("🤖 Optimiseur automatique", "d:opt")],
            [("⬅ Accueil", "d:home")],
        ]
        return self._with_flash(text), kb

    def _render_gterm(self) -> tuple[str, Keyboard]:
        insts = self.ctx.db.list()
        on = sum(1 for i in insts if self.ctx.mgr.is_running(i))
        text = (f"🖥 <b>Terminaux — tous les comptes</b>\n\n{on} lancé(s) sur {len(insts)}.\n"
                "Les positions restent chez le broker ; un arrêt attend la fin d'une clôture à 500 en cours.\n"
                "Pour un seul compte : 👥 Comptes → le compte → 🖥 Terminal.")
        kb: Keyboard = [
            [("🔄 Redémarrer tous", "d:tall:rst")],
            [("🚀 Lancer tous", "d:tall:start"), ("🛑 Arrêter tous", "d:tall:stop")],
            [("🪟 Réduire les fenêtres", "d:win")],
            [("⬅ Actions", "d:act")],
        ]
        return self._with_flash(text), kb

    def _render_filters(self, now: float) -> tuple[str, Keyboard]:
        views = self.ctx.views()
        lines = ["🧭 <b>Filtres et positions</b>", ""]
        if self.filters is None:
            lines.append("<i>Lecture des filtres…</i>")
        else:
            ts, ref, txt = self.filters
            body = "\n".join(l for l in txt.splitlines() if "FILTRES" not in l and not l.startswith("━")).strip()
            lines.append(f"🛡️ <b>Filtres</b> (identiques sur tous les robots ; lus sur {render.esc(ref)}, "
                         f"il y a {render.ago(now - ts)})")
            lines.append(render.esc(body) if body else "<i>réponse vide</i>")
        ref = render.trading_ref(views, now)
        if ref is not None and ref.news:                 # la liste complète (l'accueil n'affiche que la prochaine)
            lines += ["", "📅 <b>Prochaines news</b>"] + ["   " + render.news_item(n) for n in ref.news[:5]]
        lines += ["", "📌 <b>Positions ouvertes</b>"]
        rows, npos, nord, flot, cur = [], 0, 0, 0.0, ""
        for v in sorted(views, key=lambda v: v.inst.label.lower()):
            st = v.st
            if st is None:
                rows.append(f"{v.health.icon} {v.inst.label[:10]:<10}  —")
                continue
            cur = cur or st.devise
            npos, nord, flot = npos + st.positions, nord + st.ordres, flot + st.flottant_ea
            rows.append(f"{v.health.icon} {v.inst.label[:10]:<10} {st.positions:>2} pos {st.ordres:>2} ord {st.flottant_ea:>+9.2f}"
                        + (" ⏸" if st.etat == "PAUSE" else ""))
        lines.append("<pre>" + render.esc("\n".join(rows) or "aucun compte") + "</pre>")
        lines.append(f"Total : <b>{npos}</b> position(s) · {nord} ordre(s) · flottant <b>{render.money(flot, cur, True)}</b>")
        kb: Keyboard = [[("🔃 Actualiser", "d:flt"), ("📋 Détail des positions", "d:pos")], [("⬅ Accueil", "d:home")]]
        return self._with_flash("\n".join(lines)), kb

    def _render_logs(self) -> tuple[str, Keyboard]:
        kb: Keyboard = [
            [("📜 Bot", "d:log"), ("🧾 Compilation", "d:clog"), ("💥 Plantages", "d:crash")],
            [("🔄 Redémarrages", "d:rlog"), ("🛡 Surveillance", "d:wlog")],
            [("⬅ Système", "d:sys")],
        ]
        return self._with_flash("📜 <b>Journaux</b>\n\nLes dernières lignes arrivent dans un message à part (✖ pour le fermer)."), kb

    def _render_term(self, iid: str) -> tuple[str, Keyboard] | None:
        inst = self.ctx.db.get(iid)
        if inst is None:
            return None
        c = short(iid)
        kb: Keyboard = [
            [("🔄 Redémarrer", f"a:{c}:restart"), ("🚀 Lancer", f"a:{c}:start"), ("🛑 Arrêter", f"a:{c}:stop")],
            [("🔑 Reconnecter", f"a:{c}:recon"), ("⬆ Mettre à jour l'EA", f"a:{c}:update")],
            [("🗑 Supprimer le compte", f"a:{c}:delete")],
            [("⬅ Fiche", f"a:{c}")],
        ]
        text = (f"🖥 <b>Terminal — {render.esc(inst.label)}</b>\n\n"
                "Gestion du terminal MT5 de ce compte (les positions restent chez le broker).")
        return self._with_flash(text), kb

    def _render_card(self, iid: str, now: float) -> tuple[str, Keyboard] | None:
        v = self.ctx.view_of(iid)
        if v is None:
            return None
        closures = sum(1 for r in self.ctx.db.closures_since(0) if r["iid"] == iid)
        text = render.account_card(v, self.ctx.db.total_withdrawn(iid), closures, now)
        fl = self._flash_text()
        if fl:
            text = f"{fl}\n\n{text}"
        c = short(iid)
        dep = self.ctx.deploy
        deploying = dep.running and dep.current_iid == iid
        in_maint = deploying or v.inst.desired_state == MAINTENANCE or bool(v.st and v.st.maintenance)
        kb: Keyboard = ([[("🔧 Retirer la maintenance" + (" (annule la mise à jour)" if deploying else ""), f"a:{c}:maintoff")]]
                        if in_maint else [])
        kb += [
            [("⏸ Pause", f"a:{c}:pause"), ("▶️ Reprise", f"a:{c}:resume")],
            [("🎯 Objectif", f"g:{c}"), ("⚙️ Paramètres", f"p:{c}")],
            [("🧰 Commandes EA", f"a:{c}:cmds"), ("🖥 Terminal", f"a:{c}:term")],
            [("📈 Graphique", f"a:{c}:chart")],
            [("⬅ Comptes", "d:lst"), ("🔃", f"a:{c}:refresh")],
        ]
        return text, kb

    def _render_pause(self, iid: str) -> tuple[str, Keyboard]:
        """⏸ Pause : tout de suite (jusqu'à ▶ Reprise) ou programmée (reprise automatique)."""
        inst = act_target(self.ctx, iid)
        c = short(iid)
        group = iid == ALL_ID
        who = "tous les comptes lancés" if group else render.esc(inst.label if inst else iid)
        text = (f"⏸ <b>Pause — {who}</b>\n\nPlus aucune nouvelle position ni ordre de grille ; les positions ouvertes restent "
                "gérées (TP, protections).\nAvec une durée, le robot <b>reprend tout seul</b> à l'heure prévue.")
        fl = self._flash_text()
        kb: Keyboard = [
            [("⏸ Jusqu'à ▶ Reprise", f"z:{c}:0")],
            [(lbl, f"z:{c}:{k}") for k, lbl, _ in PAUSE_CHOICES[1:4]],
            [(PAUSE_CHOICES[4][1], f"z:{c}:{PAUSE_CHOICES[4][0]}")],
            [("⬅ Actions", "d:act")] if group else [("⬅ Fiche", f"a:{c}")],
        ]
        return (f"{fl}\n\n{text}" if fl else text), kb

    def _render_cmds(self, iid: str) -> tuple[str, Keyboard]:
        inst = act_target(self.ctx, iid)
        c = short(iid)
        group = iid == ALL_ID
        kb: Keyboard = []
        if not group:                                       # consultations (une réponse par compte : fiche seulement)
            for i in range(0, len(CARD_CMDS), 3):
                kb.append([(lbl, f"c:{c}:{i + j}") for j, (lbl, _) in enumerate(CARD_CMDS[i:i + 3])])
        kb.append([("🏖 Vacances : activer", f"e:{c}:von"), ("🏠 Vacances : quitter", f"e:{c}:voff")])
        kb.append([("💾 Presets", "s:l"), ("✅ Valider les réglages", f"e:{c}:val")])
        kb.append([("♻️ Réglages par défaut de l'EA", f"e:{c}:rs")])
        kb.append([("🎯 Annuler l'objectif", f"e:{c}:gx"), ("🛑 Tout fermer", f"e:{c}:fx")])
        kb.append([("⬅ Actions", "d:act")] if group else [("⬅ Fiche", f"a:{c}")])
        fl = self._flash_text()
        return (f"🧰 <b>Commandes EA — {render.esc(inst.label if inst else iid)}</b>" + (f"\n\n{fl}" if fl else "")), kb

    def _render_goal(self, iid: str) -> tuple[str, Keyboard] | None:
        inst = act_target(self.ctx, iid)
        if inst is None:
            return None
        c = short(iid)
        kb: Keyboard = [
            [(f"+{x} %", f"g:{c}:p:{x}") for x in ("0.5", "1", "2")],
            [(f"+{x} %", f"g:{c}:p:{x}") for x in ("3", "5", "10")],
            [("✏️ Autre (montant, %, cible)", f"g:{c}:w")],
            [("🎯 Annuler l'objectif", f"e:{c}:gx")],
            [("⬅ Actions", "d:act")] if iid == ALL_ID else [("⬅ Fiche", f"a:{c}")],
        ]
        text = (f"🎯 <b>Objectif — {render.esc(inst.label)}</b>\n\n"
                "Lance un objectif <b>tout de suite</b>, sans attendre les créneaux horaires.\n"
                "Le robot démarre et s'arrête quand l'objectif est atteint (sur chaque compte).\n"
                "<i>✏️ Autre : 10 = +10 € · +3% · =1250 = solde cible</i>")
        return self._with_flash(text), kb

    # ---- envoi / édition
    @staticmethod
    def _sig(text: str, kb: Keyboard) -> str:
        return hashlib.sha1((text + json.dumps(kb, ensure_ascii=False)).encode()).hexdigest()

    async def ensure(self) -> int:
        """S'assure que le message du tableau de bord existe (le recrée s'il a disparu)."""
        mid = int(self.ctx.db.meta_get("dash_msg_id", "0") or 0)
        if mid:
            return mid
        return await self._create()

    async def _create(self) -> int:
        text, kb = self.render()
        mid = await self.sender.send(self.chat_id, text, kb, True)          # toujours silencieux
        self.ctx.db.meta_set("dash_msg_id", str(mid))
        self._last_sig, self._last_edit = self._sig(text, kb), time.time()
        self._covered_at = None
        return mid

    def note_message(self, message_id: int) -> None:
        """Un message (du bot ou de l'utilisateur) arrive : s'il est sous le tableau de bord, celui-ci n'est plus le dernier."""
        mid = int(self.ctx.db.meta_get("dash_msg_id", "0") or 0)
        if mid and message_id > mid and self._covered_at is None:
            self._covered_at = time.time()

    @property
    def is_last(self) -> bool:
        return self._covered_at is None

    async def bring_down(self, home: bool = True) -> int:
        """Remet le tableau de bord EN BAS de la conversation : ancien message supprimé, nouveau envoyé (silencieux)."""
        old = int(self.ctx.db.meta_get("dash_msg_id", "0") or 0)
        if home:
            self.screen = ("dash",)
        self.ctx.db.meta_set("dash_msg_id", "0")
        mid = await self._create()
        if old:
            try:
                await self.sender.delete(self.chat_id, old)
            except Exception as exc:
                log.warning("ancien tableau de bord non supprimé : %s", exc)
        return mid

    async def refresh(self, force: bool = False, retry_after: float = 0.0) -> str:
        """Met à jour le message. Retourne 'edit', 'skip', 'same', 'recreated' ou 'wait'."""
        now = time.time()
        if now < self._retry_until:
            return "wait"
        mid = int(self.ctx.db.meta_get("dash_msg_id", "0") or 0)
        if not mid:
            await self._create()
            return "recreated"
        cfg = self.ctx.cfg.dashboard
        if not force and self._covered_at is not None:
            if now - self._covered_at >= cfg.repost_s:                      # recouvert depuis 5 min : republié en bas
                # republié en bas en revenant à l'accueil : un rapport (labo, backtest…) laissé ouvert n'est pas renvoyé tout seul
                await self.bring_down(home=self.screen[0] in ("sst", "wdr", "opt"))
                return "recreated"
            return "skip"                                                   # recouvert : plus d'éditions automatiques
        if not force and now - self._last_edit < cfg.refresh_s:
            return "skip"
        text, kb = self.render(now)
        sig = self._sig(text, kb)
        if sig == self._last_sig:
            return "same"
        res = await self.sender.edit(self.chat_id, mid, text, kb)
        if res == EditResult.GONE:                         # message supprimé à la main : on le recrée
            self.ctx.db.meta_set("dash_msg_id", "0")
            await self._create()
            return "recreated"
        if res == EditResult.RETRY:
            ra = float(getattr(self.sender, "last_retry_after", 0.0) or retry_after or 5.0)
            self._retry_until = time.time() + max(ra, 5.0)
            return "wait"
        self._last_sig, self._last_edit = sig, time.time()
        return "same" if res == EditResult.SAME else "edit"
