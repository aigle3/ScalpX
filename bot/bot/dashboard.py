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
from .actions import CARD_CMDS, Ctx
from .params_ui import ParamsUI
from .ui import Confirmations, EditResult, Keyboard, Sender, confirm_kb, short

log = logging.getLogger(__name__)


class Dashboard:
    def __init__(self, ctx: Ctx, sender: Sender, chat_id: int, confirmations: Confirmations, vps=vps_load) -> None:
        self.ctx, self.sender, self.chat_id, self.conf = ctx, sender, chat_id, confirmations
        self.vps = vps
        self.params = ParamsUI(ctx)
        self.presets: dict[str, list[str]] = {}             # noms des presets de l'EA, par compte
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
            inst = self.ctx.db.get(self.screen[1])
            if inst is not None:
                return self.params.render(self.screen, inst.label, self._flash_text())
            self.screen = ("dash",)
        return self._render_dash(now)

    def _render_dash(self, now: float) -> tuple[str, Keyboard]:
        views = self.ctx.views()
        pending = bool(self.ctx.db.meta_get("update_pending"))
        tools: Keyboard = [[("🛠 Système", "d:sys"), (("🔔 " if pending else "") + "⬆ Mises à jour", "d:upd")]]
        if not views:
            fl = self._flash_text()
            kb_new: Keyboard = [[("➕ Nouveau compte", "n")]] + tools + [[("📝 Rapport", "d:rep"), ("🏓 Ping", "d:png")]]
            return (f"{fl}\n\n" if fl else "") + render.welcome_empty(), kb_new
        t = totals(views)
        filtered = apply_sort(apply_filter(views, self.flt), self.sort)
        page_views, self.page, pages = render.paginate(filtered, self.page, self.ctx.cfg.dashboard.page_size)
        cur = next((v.st.devise for v in views if v.st and v.st.devise), "")
        ages = [v.st.age_s(now) for v in views if v.st]
        age = min(ages) if ages else None
        text = render.dashboard_text(views, page_views, t, self.vps(self.ctx.cfg.paths.data), self.page, pages, self.flt, self.sort, cur, None)
        stale = age is not None and age > 45
        text += "\n" + (f"⚠️ données vieilles de {render.ago(age)}" if stale and age is not None else "🟢 données fraîches")
        fl = self._flash_text()
        if fl:
            text = f"{fl}\n\n{text}"
        kb: Keyboard = []
        row: list[tuple[str, str]] = []
        for v in page_views:
            row.append((f"{v.health.icon} {v.inst.label[:14]}", f"a:{short(v.inst.id)}"))
            if len(row) == 2:
                kb.append(row)
                row = []
        if row:
            kb.append(row)
        kb.append([((("• " if f == self.flt else "") + FILTER_LABELS[f]), f"d:f:{f}") for f in ("all", "actifs", "retirer")])
        kb.append([((("• " if f == self.flt else "") + FILTER_LABELS[f]), f"d:f:{f}") for f in ("pause", "alertes", "obsolete")])
        kb.append([((("• " if s == self.sort else "") + SORT_LABELS[s]), f"d:s:{s}") for s in SORTS])
        nav: list[tuple[str, str]] = []
        if pages > 1:
            nav = [("◀", f"d:p:{self.page - 1}"), (f"{self.page + 1}/{pages}", "d:r"), ("▶", f"d:p:{self.page + 1}")]
        kb.append(nav + [("🔃", "d:r")] if nav else [("🔃 Actualiser", "d:r")])
        kb.append([("➕ Nouveau compte", "n")])
        kb.append([("⏸ Tout en pause", "d:pall"), ("▶ Tout reprendre", "d:rall")])
        kb.extend(tools)
        kb.append([("🚀 Déployer l'EA", "d:dep"), ("📝 Rapport", "d:rep")])
        kb.append([("📤 Export", "d:exp"), ("🔕 Silencieux", "d:sil"), ("🏓 Ping", "d:png")])
        return text, kb

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
            [("⏸ Pause", f"a:{c}:pause"), ("▶️ Reprise", f"a:{c}:resume"), ("🔄 Redémarrer", f"a:{c}:restart")],
            [("🛑 Arrêter", f"a:{c}:stop"), ("🚀 Lancer", f"a:{c}:start"), ("🔑 Reconnecter", f"a:{c}:recon")],
            [("⬆ Mettre à jour l'EA", f"a:{c}:update"), ("🗑 Supprimer", f"a:{c}:delete")],
            [("⚙️ Paramètres", f"p:{c}"), ("🧰 Commandes EA", f"a:{c}:cmds"), ("📈 Graphique", f"a:{c}:chart")],
            [("🔃 Actualiser", f"a:{c}:refresh"), ("⬅ Retour", "d:home")],
        ]
        return text, kb

    def _render_cmds(self, iid: str) -> tuple[str, Keyboard]:
        inst = self.ctx.db.get(iid)
        c = short(iid)
        kb: Keyboard = []
        for i in range(0, len(CARD_CMDS), 3):
            kb.append([(lbl, f"c:{c}:{i + j}") for j, (lbl, _) in enumerate(CARD_CMDS[i:i + 3])])
        kb.append([("🏖 Vacances : activer", f"e:{c}:von"), ("🏠 Vacances : quitter", f"e:{c}:voff")])
        kb.append([("💾 Presets", f"e:{c}:ps"), ("✅ Valider les réglages", f"e:{c}:val")])
        kb.append([("🎯 Annuler l'objectif", f"e:{c}:gx"), ("🛑 Tout fermer", f"e:{c}:fx")])
        kb.append([("⬅ Fiche", f"a:{c}")])
        fl = self._flash_text()
        return (f"🧰 <b>Commandes EA — {render.esc(inst.label if inst else iid)}</b>" + (f"\n\n{fl}" if fl else "")), kb

    def _render_presets(self, iid: str) -> tuple[str, Keyboard]:
        inst = self.ctx.db.get(iid)
        c = short(iid)
        names = self.presets.get(iid)
        kb: Keyboard = [[(f"📥 {n}", f"e:{c}:pl:{i}"), ("🗑", f"e:{c}:pd:{i}")] for i, n in enumerate(names or [])]
        kb.append([("💾 Enregistrer sous…", f"e:{c}:pn"), ("♻️ Valeurs par défaut", f"e:{c}:rs")])
        kb.append([("🔃 Relire", f"e:{c}:ps"), ("⬅ Commandes", f"a:{c}:cmds")])
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
