"""💾 Presets centraux : rendu des écrans et application aux robots (sans dépendance Telegram).

- un preset = toutes les valeurs des réglages de l'EA, gardées par le bot (core/presets.py) ;
- 📸 créé à partir des réglages actuels d'un robot ; ⚙️ modifié réglage par réglage (mêmes écrans que les paramètres) ;
- 📤 appliqué à tous les robots lancés (tout de suite, même pendant une série) : seules les différences sont envoyées
  (SET), puis chaque robot est relu pour vérifier ;
- le preset appliqué devient le preset ⭐ actif : chaque modification lui est aussitôt envoyée à tous les robots,
  et un contrôle régulier signale 🟠 les robots dont les réglages s'en écartent.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field

from core import presets as ps
from .actions import Ctx, send_cmd
from .params_ui import parse_params_text
from .render import ago, esc
from .ui import Keyboard, short

PREFIX = "preset:"


def code_of(name: str) -> str:
    """Code court d'un preset dans les callbacks (stable même si la liste change)."""
    return "P" + hashlib.sha1(name.encode()).hexdigest()[:7]


@dataclass
class PresetTarget:
    """Cible « preset » pour les écrans ⚙️ Paramètres : les changements vont dans le preset (et aux robots s'il est actif)."""
    name: str

    @property
    def id(self) -> str:
        return PREFIX + self.name

    @property
    def label(self) -> str:
        return f"Preset « {self.name} »"


@dataclass
class Check:
    """Dernier contrôle des robots par rapport au preset actif."""
    ts: float = 0.0
    preset: str = ""
    diffs: dict[str, list[str]] = field(default_factory=dict)        # compte → clés différentes
    unreadable: list[str] = field(default_factory=list)              # comptes sans réponse


def find(store: ps.PresetStore, code: str) -> str:
    return next((n for n in store.names() if code_of(n) == code), "")


async def read_raw(ctx: Ctx, inst) -> dict[str, str] | None:
    r = await send_cmd(ctx, inst, "PARAMS", [])
    d = parse_params_text(r.message)
    return ps.raw_values(d) if d else None


async def apply_to(ctx: Ctx, inst, values: dict[str, str]) -> tuple[bool, int, list[str], str]:
    """Envoie au robot les réglages qui diffèrent, puis relit pour vérifier. (ok, nb changés, clés en échec, erreur)."""
    cur = await read_raw(ctx, inst)
    if cur is None:
        return False, 0, [], "EA injoignable"
    diff = ps.differences(values, cur)
    if diff:
        await asyncio.gather(*[send_cmd(ctx, inst, "SET", [k, v]) for k, v in diff.items()])
        after = await read_raw(ctx, inst) or {}
        left = ps.differences(diff, after)
        for k, v in left.items():                     # 2e passage, un par un : réglages liés (ex. EMA rapide < lente)
            await send_cmd(ctx, inst, "SET", [k, v])
        if left:
            after = await read_raw(ctx, inst) or {}
        bad = sorted(ps.differences(diff, after))
        return not bad, len(diff), bad, ""
    return True, 0, [], ""


async def check_all(ctx: Ctx, store: ps.PresetStore, insts) -> Check:
    chk = Check(time.time(), store.active)
    if not store.active:
        return chk
    values = store.presets[store.active]

    async def one(i):
        return i, await read_raw(ctx, i)

    for i, cur in await asyncio.gather(*[one(i) for i in insts]):
        if cur is None:
            chk.unreadable.append(i.label)
        else:
            d = sorted(ps.differences(values, cur))
            if d:
                chk.diffs[i.label] = d
    return chk


def alert_line(chk: Check | None) -> str:
    """Ligne d'alerte de l'accueil si des robots s'écartent du preset actif."""
    if chk is None or not chk.preset or not chk.diffs:
        return ""
    who = ", ".join(f"{n} ({len(k)})" for n, k in list(chk.diffs.items())[:3])
    return f"🟠 <b>Réglages ≠ preset « {esc(chk.preset)} »</b> : {esc(who)} → 💾 Presets"


def _keys_text(keys: list[str]) -> str:
    return ", ".join(ps.BY_KEY[k][1] if k in ps.BY_KEY else k for k in keys[:6]) + (" …" if len(keys) > 6 else "")


def render_list(store: ps.PresetStore, chk: Check | None, now: float, flash: str = "") -> tuple[str, Keyboard]:
    lines = ["💾 <b>Presets</b> — gardés par le bot, communs à tous les robots", ""]
    if not store.presets:
        lines.append("Aucun preset. Règle un robot (⚙️ Paramètres), puis <b>📸 Nouveau preset</b> : ses réglages deviennent le preset.")
    lines.append(f"⭐ Preset actif : <b>{esc(store.active)}</b>" if store.active else "⭐ Aucun preset actif (📤 Appliquer en choisit un)")
    if store.active and chk is not None and chk.preset == store.active:
        if chk.diffs:
            lines.append("🟠 Robots différents du preset actif :")
            lines += [f"   • {esc(n)} : {esc(_keys_text(k))}" for n, k in chk.diffs.items()]
        else:
            lines.append("✅ Tous les robots lancés ont les réglages du preset actif")
        if chk.unreadable:
            lines.append("⚠️ Sans réponse : " + esc(", ".join(chk.unreadable)))
        lines.append(f"<i>Contrôle il y a {ago(now - chk.ts)} (toutes les 5 min)</i>")
    kb: Keyboard = [[(("⭐ " if n == store.active else "📄 ") + n, f"s:o:{code_of(n)}")] for n in store.names()]
    kb.append([("📸 Nouveau preset (réglages d'un robot)", "s:new")])
    kb.append([("🔃 Contrôler les robots", "s:chk"), ("⬅ Actions", "d:act")])
    text = "\n".join(lines)
    return (f"{flash}\n\n{text}" if flash else text), kb


def render_one(store: ps.PresetStore, name: str, chk: Check | None, flash: str = "") -> tuple[str, Keyboard]:
    c = code_of(name)
    vals = store.presets.get(name, {})
    active = name == store.active
    lines = [f"💾 <b>Preset « {esc(name)} »</b>" + (" ⭐ actif" if active else ""), f"{len(vals)} réglages enregistrés."]
    if active:
        lines.append("Toute modification est <b>envoyée tout de suite à tous les robots</b>.")
        if chk is not None and chk.preset == name and chk.diffs:
            lines.append(f"🟠 {len(chk.diffs)} robot(s) différent(s) : 📤 Appliquer pour les remettre d'aplomb.")
    else:
        lines.append("Pas encore appliqué : 📤 l'envoie à tous les robots et en fait le preset actif.")
    kb: Keyboard = [
        [("🔍 Comparer avec les robots", f"s:cmp:{c}")],
        [("📤 Appliquer à tous les robots", f"s:ap:{c}")],
        [("⚙️ Modifier les réglages", f"p:{c}")],
        [("📸 Remplacer par les réglages d'un robot", f"s:new:{c}")],
        [("🗑 Supprimer", f"s:del:{c}"), ("⬅ Presets", "s:l")],
    ]
    text = "\n".join(lines)
    return (f"{flash}\n\n{text}" if flash else text), kb


def compare_text(name: str, values: dict[str, str], results: list) -> str:
    """Ce qui changerait sur chaque robot si le preset était appliqué : réglage : robot → preset."""
    lines = [f"🔍 <b>Preset « {esc(name)} » comparé aux robots</b>", "<i>réglage : valeur du robot → valeur du preset</i>"]
    for label, cur in results:
        if cur is None:
            lines.append(f"\n⚠️ <b>{esc(label)}</b> : réglages illisibles (terminal arrêté ?)")
            continue
        diff = ps.differences(values, cur)
        if not diff:
            lines.append(f"\n✅ <b>{esc(label)}</b> : identique au preset")
            continue
        lines.append(f"\n🔸 <b>{esc(label)}</b> : {len(diff)} réglage(s) changeraient")
        for k in sorted(diff, key=lambda k: [p[0] for p in ps.cat.PARAMS].index(k) if k in ps.BY_KEY else 999):
            p = ps.BY_KEY.get(k)
            if p is None:
                continue
            old = ps.display_of(p, cur[k]) if k in cur else "?"
            lines.append(f"   • {esc(p[1])} : {esc(old)} → <b>{esc(ps.display_of(p, diff[k]))}</b>")
    text = "\n".join(lines)
    return text if len(text) <= 3900 else text[:3850] + "\n… (liste tronquée)"


def render_source(insts, overwrite: str = "", flash: str = "") -> tuple[str, Keyboard]:
    what = f"remplacer le preset « {esc(overwrite)} »" if overwrite else "créer le preset"
    text = f"📸 <b>Quel robot sert de modèle ?</b>\nSes réglages actuels servent à {what}."
    kb: Keyboard = [[(f"🤖 {i.label}", f"s:src:{short(i.id)}" + (f":{code_of(overwrite)}" if overwrite else ""))] for i in insts]
    kb.append([("⬅ Presets", "s:l")])
    return (f"{flash}\n\n{text}" if flash else text), kb
