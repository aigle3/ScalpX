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


class ParamsUI:
    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx
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
        r = await send_cmd(self.ctx, getattr(inst, "ref", inst), "PARAMS", [])     # groupe : valeurs du 1er compte
        d = parse_params_text(r.message)
        if not d:
            self.last_error = "EA injoignable (terminal arrêté ?)" if r.timeout else "réponse inattendue de l'EA"
            return False
        self.vals[inst.id] = (time.time(), d)
        self.last_error = ""
        return True

    async def set_value(self, inst, idx: int, value: str) -> str:
        key = cat.PARAMS[idx][0]
        members = getattr(inst, "members", None)
        if members is not None:                                  # tous les comptes : même réglage partout
            res = await asyncio.gather(*[send_cmd(self.ctx, m, "SET", [key, value]) for m in members])
            await self.fetch(inst)
            bad = [m.label for m, r in zip(members, res) if "Appliqué" not in (r.message or "")]
            head = f"{esc(cat.PARAMS[idx][1])} = {esc(value)}"
            return (f"✅ {head} sur les {len(members)} comptes" if not bad else
                    f"⚠️ {head} : {len(members) - len(bad)}/{len(members)} ; échec : {esc(', '.join(bad[:5]))}")
        r = await send_cmd(self.ctx, inst, "SET", [key, value])
        await self.fetch(inst)                                   # relit les valeurs réelles (l'EA peut avoir borné)
        msg = r.message or ""
        if "Appliqué" in msg:                                    # succès, même si la fiche contient des « ❌ » (valeur Non)
            line = next((l for l in msg.splitlines() if "Appliqué" in l), "")
            return f"✅ {esc(cat.PARAMS[idx][1])} : " + esc(line.split(":", 1)[-1].strip())
        if r.timeout:
            return "❌ EA injoignable (terminal arrêté ?)"
        err = next((l for l in msg.splitlines() if "❌" in l), msg)
        return esc(err[:200]) if err.startswith("❌") else "❌ " + esc(err[:200])

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
        c = short(iid)
        back = ("⬅ Actions", "d:act") if c == "all" else ("⬅ Fiche", f"a:{c}")
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
            for i, p in enumerate(cat.PARAMS):
                if p[2] != gi:
                    continue
                v = vals.get(p[0], "?")
                shown = "✅" if p[3] == "bool" and "✅" in v else ("❌" if p[3] == "bool" and "❌" in v else v)
                kb.append([(f"{p[1][:30]} · {shown}"[:60], f"p:{c}:{'t' if p[3] == 'bool' else 'k'}{i}")])
            kb.append([("⬅ Catégories", f"p:{c}"), back])
            return f"{head}\n<b>{esc(cat.GROUPS[gi])}</b> — touche un paramètre pour le changer." + fl, kb
        # prp : un paramètre
        idx = int(screen[2])
        p = cat.PARAMS[idx]
        key, name, gi, typ, mn, mx, step, unit, opts = p
        cur = vals.get(key, "?")
        text = f"{head}\n<b>{esc(name)}</b> (<code>{key}</code>)\nValeur actuelle : <b>{esc(cur)}</b>"
        if typ in ("int", "dbl"):
            text += f"\nLimites : {fmt_num(p, mn)} – {fmt_num(p, mx)}{esc(unit)} · pas {fmt_num(p, step)}"
        kb = []
        if typ == "enum":
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
