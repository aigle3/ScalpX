"""Écrans « ⚙️ Paramètres » : modification de TOUS les paramètres de l'EA par boutons.

Valeurs courantes : commande EA PARAMS (texte « • clé = valeur »), mises en cache ; changements : commande EA SET clé valeur.
Sans dépendance Telegram : renvoie (texte HTML, clavier).
"""
from __future__ import annotations

import asyncio
import re
import time
from decimal import Decimal, ROUND_HALF_UP

from core import params as cat
from . import render
from core.presets import GRID_LINK_KEYS, linked_gridmax
from .actions import Ctx, send_cmd
from .render import esc
from .ui import Keyboard, short

_LINE = re.compile(r"^\s*[•·]\s*([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$")
_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")
CACHE_S = 90.0


def parse_params_text(text: str) -> dict[str, str]:
    """« • lot = 0.01 lot » → {'lot': '0.01 lot'} (dernière occurrence gagnante)."""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        m = _LINE.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def find(key: str) -> int:
    for i, p in enumerate(cat.PARAMS):
        if p[0] == key:
            return i
    return -1


def _dec(step: float) -> int:
    s = f"{step:.6f}".rstrip("0")
    return len(s.split(".")[1]) if "." in s else 0


def fmt_num(p: tuple, v: float) -> str:
    """Valeur numérique clampée aux bornes et arrondie au pas, au format attendu par l'EA."""
    _, _, _, typ, mn, mx, step, _, _ = p
    v = max(mn, min(mx, v))
    if step:
        v = float((Decimal(str(v)) / Decimal(str(step))).quantize(Decimal(1), rounding=ROUND_HALF_UP) * Decimal(str(step)))
        v = max(mn, min(mx, v))
    return str(int(round(v))) if typ == "int" else f"{v:.{_dec(step or 0.01)}f}"


def num_of(display: str) -> float | None:
    m = _NUM.search(display or "")
    return float(m.group(0).replace(",", ".")) if m else None


def bool_of(display: str) -> bool | None:
    d = display or ""
    if "✅" in d:
        return True
    if "❌" in d:
        return False
    return None


def _code(iid: str) -> str:
    """Code du callback : compte (8 hex), « all », ou preset (P…)."""
    if iid.startswith("preset:"):
        from .presets_ui import code_of
        return code_of(iid[len("preset:"):])
    return short(iid)


class ParamsUI:
    def __init__(self, ctx: Ctx, store=None) -> None:
        self.ctx = ctx
        self.store = store                                   # presets centraux (cible PresetTarget)
        self.on_active_change = None                         # rappel : preset actif modifié
        self.vals: dict[str, tuple[float, dict[str, str]]] = {}
        self.last_error = ""

    # ---- valeurs courantes
    def cached(self, iid: str) -> dict[str, str]:
        return self.vals.get(iid, (0.0, {}))[1]

    def fresh(self, iid: str) -> bool:
        ts = self.vals.get(iid, (0.0, {}))[0]
        return time.time() - ts < CACHE_S

    async def fetch(self, inst) -> bool:
        """Lit les valeurs via PARAMS. Le drapeau ok de l'accusé n'est PAS fiable ici : l'EA marque en échec
        tout texte contenant « ❌ », or chaque option désactivée s'affiche « ❌ ». Seul le contenu compte."""
        if inst.id.startswith("preset:"):                       # preset : valeurs gardées par le bot
            from core.presets import display_of
            vals = self.store.presets.get(inst.name) if self.store else None
            if vals is None:
                self.last_error = "preset introuvable"
                return False
            self.vals[inst.id] = (time.time(), {k: display_of(cat.PARAMS[find(k)], v) for k, v in vals.items() if find(k) >= 0})
            return True
        r = await send_cmd(self.ctx, getattr(inst, "ref", inst), "PARAMS", [])     # groupe : valeurs du 1er compte
        d = parse_params_text(r.message)
        if not d:
            self.last_error = "EA injoignable (terminal arrêté ?)" if r.timeout else "réponse inattendue de l'EA"
            return False
        self.vals[inst.id] = (time.time(), d)
        self.last_error = ""
        return True

    def _raw_link(self, iid: str) -> dict[str, str]:
        """Bot 4.16.0 : valeurs brutes utiles au lien grille max ↔ ATR max, lues sur le robot (affichage de PARAMS)."""
        vals = self.cached(iid)
        out: dict[str, str] = {}
        b = bool_of(vals.get("f_atr", ""))
        if b is not None:
            out["f_atr"] = "1" if b else "0"
        for k in ("gridatr", "atrmax", "gridmin", "gridmax"):
            n = num_of(vals.get(k, ""))
            if n is not None:
                out[k] = str(n)
        return out

    async def _follow_grid(self, inst, key: str, msg: str) -> str:
        """Bot 4.16.0 : après un changement de l'ATR max, du multiplicateur ATR de la grille ou du filtre ATR, la grille max
        suit (multiplicateur × ATR max) et le message l'indique."""
        if key not in GRID_LINK_KEYS or msg.startswith("❌"):
            return msg
        if inst.id.startswith("preset:"):
            vals = (self.store.presets.get(inst.name) if self.store else None) or {}
        else:
            vals = self._raw_link(inst.id)
        new = linked_gridmax(vals)
        if new is None:
            return msg
        sub = await self.set_value(inst, find("gridmax"), new)
        return msg + f"\n↳ Grille max → {esc(new)} pts (multiplicateur ATR × ATR max) : " + sub.split(" ", 1)[0]

    async def set_value(self, inst, idx: int, value: str, shown: str = "") -> str:
        """Applique la valeur ; « shown » remplace « libellé = valeur » dans le message (ex. « Créneau 09:30 ajouté »)."""
        return await self._follow_grid(inst, cat.PARAMS[idx][0], await self._set_value(inst, idx, value, shown))

    async def _set_value(self, inst, idx: int, value: str, shown: str = "") -> str:
        key = cat.PARAMS[idx][0]
        if inst.id.startswith("preset:"):
            return await self._set_preset(inst, idx, value, shown)
        members = getattr(inst, "members", None)
        for m in (members if members is not None else [inst]):   # Bot 4.16.1 : réglé à la main → plus d'alignement auto
            try:
                self.ctx.db.meta_set(f"preset_ok:{m.id}", "1")
            except Exception:
                pass
        if members is not None:                                  # tous les comptes : même réglage partout
            res = await asyncio.gather(*[send_cmd(self.ctx, m, "SET", [key, value]) for m in members])
            await self.fetch(inst)
            bad = [m.label for m, r in zip(members, res) if "Appliqué" not in (r.message or "")]
            head = esc(shown) if shown else f"{esc(cat.PARAMS[idx][1])} = {esc(value)}"
            return (f"✅ {head} sur les {len(members)} comptes" if not bad else
                    f"⚠️ {head} : {len(members) - len(bad)}/{len(members)} ; échec : {esc(', '.join(bad[:5]))}")
        r = await send_cmd(self.ctx, inst, "SET", [key, value])
        await self.fetch(inst)                                   # relit les valeurs réelles (l'EA peut avoir borné)
        msg = r.message or ""
        if "Appliqué" in msg:                                    # succès, même si la fiche contient des « ❌ » (valeur Non)
            if shown:
                return f"✅ {esc(shown)}"
            line = next((l for l in msg.splitlines() if "Appliqué" in l), "")
            return f"✅ {esc(cat.PARAMS[idx][1])} : " + esc(line.split(":", 1)[-1].strip())
        if r.timeout:
            return "❌ EA injoignable (terminal arrêté ?)"
        err = next((l for l in msg.splitlines() if "❌" in l), msg)
        return esc(err[:200]) if err.startswith("❌") else "❌ " + esc(err[:200])

    async def _set_preset(self, inst, idx: int, value: str, shown: str = "") -> str:
        """Change la valeur dans le preset ; preset actif : envoyée aussitôt à tous les robots lancés."""
        from core.presets import display_of
        p = cat.PARAMS[idx]
        if self.store is None or inst.name not in self.store.presets:
            return "❌ Preset introuvable"
        self.store.set_value(inst.name, p[0], value)
        await self.fetch(inst)
        if self.store.active == inst.name and self.on_active_change:
            self.on_active_change()                              # .set de BASE suit le preset actif
        head = esc(shown) if shown else f"{esc(p[1])} = {esc(display_of(p, value))}"
        if self.store.active != inst.name:
            return f"✅ {head} (preset enregistré ; 📤 Appliquer pour l'envoyer aux robots)"
        members = [i for i in self.ctx.db.list() if self.ctx.mgr.is_running(i)]
        if not members:
            return f"✅ {head} (preset enregistré ; aucun robot lancé)"
        res = await asyncio.gather(*[send_cmd(self.ctx, m, "SET", [p[0], value]) for m in members])
        bad = [m.label for m, r in zip(members, res) if "Appliqué" not in (r.message or "")]
        return (f"✅ {head} : preset + {len(members)} robot(s)" if not bad else
                f"⚠️ {head} : preset enregistré, {len(members) - len(bad)}/{len(members)} robot(s) ; échec : {esc(', '.join(bad[:5]))}")

    def masks(self, iid: str, keys: tuple = cat.SLOT_KEYS) -> list[int] | None:
        """Les 4 masques de créneaux (ou de coupures : keys = cat.CUT_KEYS) lus sur le robot (ou le preset) ; None si l'un
        d'eux est inconnu. Preset plus ancien que l'EA : valeurs par défaut de l'EA (enregistrées au premier changement)."""
        vals = self.cached(iid)
        out = []
        for k in keys:
            v = num_of(vals.get(k, "") or (cat.DEFAULTS.get(k, "") if iid.startswith("preset:") and vals else ""))
            if v is None:
                return None
            out.append(int(v))
        return out

    def current_index(self, iid: str, idx: int) -> int | None:
        p = cat.PARAMS[idx]
        disp = self.cached(iid).get(p[0], "")
        for i, o in enumerate(p[8]):
            if disp.strip() == o:
                return i
        return None

    # ---- rendu
    def render(self, screen: tuple, label: str, flash: str = "") -> tuple[str, Keyboard]:
        kind, iid = screen[0], screen[1]
        c = _code(iid)
        back = (("⬅ Actions", "d:act") if c == "all" else ("⬅ Preset", f"s:o:{c}") if c.startswith("P")
                else ("⬅ Fiche", f"a:{c}"))
        head = f"⚙️ <b>Paramètres — {esc(label)}</b>"
        fl = f"\n\n{flash}" if flash else ""
        vals = self.cached(iid)
        if kind == "prm" or not vals and kind != "prp":
            kb: Keyboard = []
            row: list[tuple[str, str]] = []
            for gi, g in enumerate(cat.GROUPS):
                row.append((g, f"p:{c}:g{gi}"))
                if len(row) == 2:
                    kb.append(row)
                    row = []
            if row:
                kb.append(row)
            kb.append([("🔃 Relire", f"p:{c}:r"), back])
            note = "" if vals else "\n\n<i>Valeurs en cours de lecture…</i>"
            return head + "\nChoisis une catégorie." + note + fl, kb
        if kind == "prg":
            gi = int(screen[2])
            kb = []
            if gi == cat.SLOT_GROUP:
                m = self.masks(iid)
                kb.append([(f"🕐 Créneaux · {cat.slots_text(m) if m is not None else '?'}"[:60], f"p:{c}:c")])
                m = self.masks(iid, cat.CUT_KEYS)
                cut_on = bool_of(vals.get("ct_on", "")) is not False
                kb.append([(f"⛔ Coupures · {(cat.slots_text(m) if cut_on else 'désactivées') if m is not None else '?'}"[:60], f"p:{c}:u")])
            for i, p in enumerate(cat.PARAMS):
                if p[2] != gi or p[0] in cat.HIDDEN:
                    continue
                v = vals.get(p[0], "?")
                shown = "✅" if p[3] == "bool" and "✅" in v else ("❌" if p[3] == "bool" and "❌" in v else v)
                kb.append([(f"{p[1][:30]} · {shown}"[:60], f"p:{c}:{'t' if p[3] == 'bool' else 'k'}{i}")])
            kb.append([("⬅ Catégories", f"p:{c}"), back])
            return f"{head}\n<b>{esc(cat.GROUPS[gi])}</b> — touche un paramètre pour le changer." + fl, kb
        if kind in ("prc", "prh", "prq", "puc", "puh", "puq"):
            return self._render_slots(screen, c, head, back, fl)
        # prp : un paramètre
        idx = int(screen[2])
        p = cat.PARAMS[idx]
        key, name, gi, typ, mn, mx, step, unit, opts = p
        cur = vals.get(key, "?")
        text = f"{head}\n<b>{esc(name)}</b> (<code>{key}</code>)\nValeur actuelle : <b>{esc(cur)}</b>"
        if cur == "?" and iid.startswith("preset:"):
            text += "\n<i>Pas encore dans ce preset (créé avant cette version de l'EA) : choisis une valeur pour l'ajouter.</i>"
        if typ in ("int", "dbl"):
            text += f"\nLimites : {fmt_num(p, mn)} – {fmt_num(p, mx)}{esc(unit)} · pas {fmt_num(p, step)}"
        kb = []
        if typ == "bool":                                    # explicite (utile quand la valeur n'existe pas encore)
            kb.append([("✅ Activer", f"p:{c}:e{idx}:1"), ("❌ Désactiver", f"p:{c}:e{idx}:0")])
        elif typ == "enum":
            cur_i = self.current_index(iid, idx)
            row = []
            for oi, o in enumerate(opts):
                row.append((("• " if oi == cur_i else "") + o, f"p:{c}:e{idx}:{oi}"))
                if len(row) == 2:
                    kb.append(row)
                    row = []
            if row:
                kb.append(row)
        else:
            kb.append([("−10", f"p:{c}:s{idx}:-10"), ("−1", f"p:{c}:s{idx}:-1"), ("+1", f"p:{c}:s{idx}:1"), ("+10", f"p:{c}:s{idx}:10")])
            kb.append([("✏️ Saisir une valeur", f"p:{c}:w{idx}")])
        kb.append([("⬅ " + cat.GROUPS[gi][:24], f"p:{c}:g{gi}"), back])
        return text + fl, kb

    # ---- ⛔ coupures de l'objectif (EA 7.0.0) : la liste
    def _render_cuts(self, screen, c, head, back, fl, vals, masks, slots) -> tuple[str, Keyboard]:
        on = bool_of(vals.get("ct_on", ""))
        lines = [head, "<b>⛔ Coupures de l'objectif</b>",
                 "Tous les jours, à l'heure du fuseau réglé dans l'EA. À chaque heure de coupure :",
                 "• <b>aucune position</b> (filtres bloquants) : l'objectif en cours est annulé ;",
                 "• <b>une série est ouverte</b> : plus de nouvelle série, la grille continue jusqu'à son TP, puis l'objectif est annulé.",
                 "Le robot attend ensuite le prochain créneau.", ""]
        if on is False:
            lines.append("⚪ <b>Coupures désactivées</b> : l'objectif n'est jamais coupé.")
        lines.append(f"<b>{len(slots)} coupure(s)</b> : {esc(cat.slots_text(masks))}" if slots else "Aucune heure de coupure.")
        if slots:
            lines.append("<i>Touche ✖ pour retirer une heure.</i>")
        kb: Keyboard = []
        row = []
        for s in slots[:cat.SLOTS_PER_PAGE]:
            row.append((f"✖ {cat.slot_text(s)}", f"p:{c}:ux{s}"))
            if len(row) == 4:
                kb.append(row)
                row = []
        if row:
            kb.append(row)
        kb.append([("➕ Ajouter une heure", f"p:{c}:ua")])
        kb.append([(("✅ Activées" if on else "❌ Désactivées") if on is not None else "Activation ?", f"p:{c}:t{find('ct_on')}")])
        kb.append([("🔃 Relire", f"p:{c}:r"), ("⬅ " + cat.GROUPS[cat.SLOT_GROUP][:24], f"p:{c}:g{cat.SLOT_GROUP}"), back])
        return "\n".join(lines) + fl, kb

    # ---- 🕐 créneaux d'objectif automatique : liste (prc), choix de l'heure (prh), choix des minutes (prq)
    def _render_slots(self, screen: tuple, c: str, head: str, back: tuple, fl: str) -> tuple[str, Keyboard]:
        """🕐 Créneaux (prc/prh/prq, boutons p:…:c…) et ⛔ Coupures (puc/puh/puq, boutons p:…:u…) : même codage, mêmes écrans."""
        kind, iid = screen[0], screen[1]
        cut = kind.startswith("pu")
        f = "u" if cut else "c"
        noun = "coupure" if cut else "créneau"
        title = "⛔ Coupures" if cut else "🕐 Créneaux"
        vals = self.cached(iid)
        masks = self.masks(iid, cat.CUT_KEYS if cut else cat.SLOT_KEYS)
        to_list = (f"⬅ {title[2:]}", f"p:{c}:{f}")
        if masks is None:
            return (head + f"\n<b>{title}</b>\n\n<i>Heures illisibles : touche 🔃 Relire "
                           f"(EA en version {'7.0.0' if cut else '6.2.0'} ou plus requis).</i>" + fl,
                    [[("🔃 Relire", f"p:{c}:r")], [("⬅ " + cat.GROUPS[cat.SLOT_GROUP][:24], f"p:{c}:g{cat.SLOT_GROUP}"), back]])
        slots = cat.slots_of(masks)
        if kind in ("prh", "puh"):
            taken = {s // 60 for s in slots}
            kb: Keyboard = []
            for r in range(4):
                kb.append([((f"• {h:02d}h" if h in taken else f"{h:02d}h"), f"p:{c}:{f}h{h}") for h in range(r * 6, r * 6 + 6)])
            kb.append([to_list, back])
            return (head + f"\n<b>➕ Nouvelle {noun} : choisis l'heure</b>\n<i>• = heure qui en a déjà une.</i>" if cut else
                    head + "\n<b>➕ Nouveau créneau : choisis l'heure</b>\n<i>• = heure qui a déjà un créneau.</i>") + fl, kb
        if kind in ("prq", "puq"):
            h = int(screen[2])
            row = []
            for m in cat.SLOT_MINUTES:
                t = cat.slot_text(h * 60 + m)
                row.append((f"✓ {t}" if h * 60 + m in slots else t, f"p:{c}:{f}m{h}:{m}"))
            kb = [row[:2], row[2:], [("⬅ Heures", f"p:{c}:{f}a"), to_list]]
            return (head + f"\n<b>➕ Nouvelle {noun} à {h:02d}h : choisis les minutes</b>" if cut else
                    head + f"\n<b>➕ Nouveau créneau à {h:02d}h : choisis les minutes</b>") + "\n<i>✓ = déjà présent.</i>" + fl, kb
        if cut:
            return self._render_cuts(screen, c, head, back, fl, vals, masks, slots)
        # prc : la liste
        on = bool_of(vals.get("sl_on", ""))
        pct, win = vals.get("sl_pct", "?"), vals.get("sl_win", "?")
        lines = [head, "<b>🕐 Créneaux d'objectif automatique</b>",
                 "Lundi à vendredi, à l'heure du fuseau réglé dans l'EA (⏰ Filtre horaire → Fuseau horaire).",
                 f"À chaque créneau libre, un objectif <b>+{esc(pct.replace(' %', ''))} %</b> est lancé. "
                 f"Fenêtre de lancement : <b>{esc(win)}</b> après l'heure du créneau ; au-delà, il est perdu (jamais rattrapé).",
                 "Un créneau est sauté si l'objectif précédent n'est pas terminé.", ""]
        if on is False:
            lines.append("⛔ <b>Objectifs automatiques désactivés</b> : aucun créneau ne sera lancé.")
        lines.append(f"<b>{len(slots)} créneau(x)</b> : {esc(cat.slots_text(masks))}" if slots else "Aucun créneau.")
        if slots:
            lines.append("<i>Touche ✖ pour retirer un créneau.</i>")
        kb = []
        per = cat.SLOTS_PER_PAGE
        pages = max(1, (len(slots) + per - 1) // per)
        page = min(max(0, int(screen[2]) if len(screen) > 2 else 0), pages - 1)
        row = []
        for s in slots[page * per:(page + 1) * per]:
            row.append((f"✖ {cat.slot_text(s)}", f"p:{c}:cx{s}"))
            if len(row) == 4:
                kb.append(row)
                row = []
        if row:
            kb.append(row)
        if pages > 1:
            kb.append([("◀", f"p:{c}:cp{(page - 1) % pages}"), (f"{page + 1}/{pages}", f"p:{c}:cp{page}"),
                       ("▶", f"p:{c}:cp{(page + 1) % pages}")])
        kb.append([("➕ Ajouter un créneau", f"p:{c}:ca")])
        i_on, i_pct, i_win = find("sl_on"), find("sl_pct"), find("sl_win")
        kb.append([(("✅ Activés" if on else "❌ Désactivés") if on is not None else "Activation ?", f"p:{c}:t{i_on}"),
                   (f"🎯 Gain {pct}"[:30], f"p:{c}:k{i_pct}"), (f"⏱ Fenêtre {win}"[:30], f"p:{c}:k{i_win}")])
        kb.append([("🔃 Relire", f"p:{c}:r"), ("⬅ " + cat.GROUPS[cat.SLOT_GROUP][:24], f"p:{c}:g{cat.SLOT_GROUP}"), back])
        return "\n".join(lines) + fl, kb
