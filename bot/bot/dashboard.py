"""Tableau de bord VIVANT : un seul message (non épinglé), ÉDITÉ en place (accueil, liste, fiche compte, confirmations).

Édition au plus toutes les `interval_s` secondes (10 à 15 s) et seulement si le contenu a changé ; gère RetryAfter,
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
from .ui import ALL_ID, Confirmations, EditResult, Keyboard, Sender, confirm_kb, short

log = logging.getLogger(__name__)


class Dashboard:
    def __init__(self, ctx: Ctx, sender: Sender, chat_id: int, confirmations: Confirmations, vps=vps_load) -> None:
        self.ctx, self.sender, self.chat_id, self.conf = ctx, sender, chat_id, confirmations
        self.vps = vps
        self.params = ParamsUI(ctx)
        self.presets: dict[str, list[str]] = {}             # noms des presets de l'EA, par compte
        self.silent = False                                  # mode silencieux (libellé du bouton de l'écran Actions)
        self.screen: tuple = ("dash",)
        self.confirm_back: tuple = ("dash",)
        self.flt, self.sort, self.page = "all", "nom", 0
        self._flash: tuple[str, float] | None = None
        self._last_sig = ""
        self._last_edit = 0.0
        self._retry_until = 0.0
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
        if kind == "pst":
            return self._render_presets(self.screen[1])
        if kind == "sys":
            from core import update as upd
            from .system import BOT_ROOT, system_kb, system_text
            fl = self._flash_text()
            return ((f"{fl}\n\n" if fl else "") + system_text(self.ctx, self.vps(self.ctx.cfg.paths.data)),
                    system_kb(bool(self.ctx.db.meta_get("update_pending")), upd.backup_version(BOT_ROOT)))
        if kind in ("prm", "prg", "prp"):
            inst = act_target(self.ctx, self.screen[1])
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
        if kind == "lst":
            return self._render_list(now)
        if kind == "act":
            return self._render_actions()
        if kind == "rpt":
            return self._render_reports()
        if kind == "logs":
            return self._render_logs()
        return self._render_home(now)

    # ---- accueil : l'état de l'ensemble d'un coup d'œil, 4 menus
    def _with_flash(self, text: str) -> str:
        fl = self._flash_text()
        return f"{fl}\n\n{text}" if fl else text

    def _render_home(self, now: float) -> tuple[str, Keyboard]:
        from core.version import bot_version
        from mt5 import base as basemod
        views = self.ctx.views()
        pending = bool(self.ctx.db.meta_get("update_pending"))
        sys_btn = ("🛠 Système" + (" 🔔" if pending else ""), "d:sys")
        if not views:
            kb: Keyboard = [[("➕ Nouveau compte", "n")], [sys_btn]]
            return self._with_flash(render.welcome_empty()), kb
        t = totals(views)
        cur = next((v.st.devise for v in views if v.st and v.st.devise), "")
        vps = self.vps(self.ctx.cfg.paths.data)
        ages = [v.st.age_s(now) for v in views if v.st]
        text = render.home_text(views, t, vps, cur, bot_version(), basemod.read_version(self.ctx.cfg.paths.base),
                                pending, min(ages) if ages else None, now)
        kb = [[("👥 Comptes", "d:lst"), ("⚡ Actions", "d:act")],
              [("📈 Rapports", "d:rpt"), sys_btn]]
        return self._with_flash(text), kb

    # ---- 👥 comptes : liste détaillée, filtre et tri (un bouton chacun, qui fait défiler les choix)
    def _render_list(self, now: float) -> tuple[str, Keyboard]:
        views = self.ctx.views()
        filtered = apply_sort(apply_filter(views, self.flt), self.sort)
        page_views, self.page, pages = render.paginate(filtered, self.page, self.ctx.cfg.dashboard.page_size)
        text = render.list_text(page_views, len(views), self.page, pages, self.flt, self.sort)
        kb: Keyboard = [[(f"{v.health.icon} {v.inst.label[:24]}", f"a:{short(v.inst.id)}")] for v in page_views]
        kb.append([(f"🔎 {FILTER_LABELS[self.flt]}", "d:fc"), (f"↕ {SORT_LABELS[self.sort]}", "d:sc")])
        if pages > 1:
            kb.append([("◀", f"d:p:{self.page - 1}"), (f"{self.page + 1}/{pages}", "d:r"), ("▶", f"d:p:{self.page + 1}")])
        kb.append([("⬅ Accueil", "d:home"), ("➕ Nouveau compte", "n")])
        return self._with_flash(text), kb

    def _render_actions(self) -> tuple[str, Keyboard]:
        silent = self.silent
        text = ("⚡ <b>Actions sur tous les comptes</b> (terminaux lancés)\n\n"
                "Objectif, réglages et commandes partent vers chaque compte, avec confirmation.\n"
                "Pour un seul compte : 👥 Comptes → le compte.")
        kb: Keyboard = [
            [("⏸ Tout en pause", "d:pall"), ("▶ Tout reprendre", "d:rall")],
            [("🎯 Objectif (tous)", "g:all"), ("⚙️ Paramètres (tous)", "p:all")],
            [("🧰 Commandes (tous)", "d:gcmd")],
            [("🚀 Déployer l'EA", "d:dep"), ("➕ Nouveau compte", "n")],
            [(("🔔 Rétablir le son" if silent else "🔕 Mode silencieux"), "d:sil")],
            [("⬅ Accueil", "d:home")],
        ]
        return self._with_flash(text), kb

    def _render_reports(self) -> tuple[str, Keyboard]:
        text = "📈 <b>Rapports</b>\n\nLe rapport quotidien arrive aussi tout seul chaque soir."
        kb: Keyboard = [
            [("📝 Rapport des 24 h", "d:rep"), ("📈 Courbe des soldes", "d:chart")],
            [("📤 Export CSV", "d:exp")],
            [("⬅ Accueil", "d:home")],
        ]
        return self._with_flash(text), kb

    def _render_logs(self) -> tuple[str, Keyboard]:
        kb: Keyboard = [
            [("📜 Bot", "d:log"), ("🧾 Compilation", "d:clog"), ("💥 Plantages", "d:crash")],
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
        kb: Keyboard = [
            [("⏸ Pause", f"a:{c}:pause"), ("▶️ Reprise", f"a:{c}:resume")],
            [("🎯 Objectif", f"g:{c}"), ("⚙️ Paramètres", f"p:{c}")],
            [("🧰 Commandes EA", f"a:{c}:cmds"), ("🖥 Terminal", f"a:{c}:term")],
            [("📈 Graphique", f"a:{c}:chart")],
            [("⬅ Comptes", "d:lst"), ("🔃", f"a:{c}:refresh")],
        ]
        return text, kb

    def _render_cmds(self, iid: str) -> tuple[str, Keyboard]:
        inst = act_target(self.ctx, iid)
        c = short(iid)
        group = iid == ALL_ID
        kb: Keyboard = []
        if not group:                                       # consultations (une réponse par compte : fiche seulement)
            for i in range(0, len(CARD_CMDS), 3):
                kb.append([(lbl, f"c:{c}:{i + j}") for j, (lbl, _) in enumerate(CARD_CMDS[i:i + 3])])
        kb.append([("🏖 Vacances : activer", f"e:{c}:von"), ("🏠 Vacances : quitter", f"e:{c}:voff")])
        kb.append([("💾 Presets", f"e:{c}:ps"), ("✅ Valider les réglages", f"e:{c}:val")])
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

    def _render_presets(self, iid: str) -> tuple[str, Keyboard]:
        inst = act_target(self.ctx, iid)
        c = short(iid)
        names = self.presets.get(iid)
        kb: Keyboard = [[(f"📥 {n}", f"e:{c}:pl:{i}"), ("🗑", f"e:{c}:pd:{i}")] for i, n in enumerate(names or [])]
        kb.append([("💾 Enregistrer sous…", f"e:{c}:pn"), ("♻️ Valeurs par défaut", f"e:{c}:rs")])
        kb.append([("🔃 Relire", f"e:{c}:ps"), ("⬅ Commandes", "d:gcmd" if iid == ALL_ID else f"a:{c}:cmds")])
        body = ("<i>lecture…</i>" if names is None else
                "Aucun preset enregistré." if not names else
                "📥 charge les réglages enregistrés (à valider ensuite) · 🗑 supprime.")
        fl = self._flash_text()
        return (f"💾 <b>Presets — {render.esc(inst.label if inst else iid)}</b>\n{body}" + (f"\n\n{fl}" if fl else "")), kb

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
        mid = await self.sender.send(self.chat_id, text, kb, True)
        self.ctx.db.meta_set("dash_msg_id", str(mid))
        self._last_sig, self._last_edit = self._sig(text, kb), time.time()
        return mid

    async def bring_down(self) -> int:
        """Remet le tableau de bord EN BAS de la conversation : ancien message supprimé, nouveau envoyé."""
        old = int(self.ctx.db.meta_get("dash_msg_id", "0") or 0)
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
        if not force and now - self._last_edit < self.ctx.cfg.dashboard.interval_s:
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
