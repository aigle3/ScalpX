"""Écran « 🛠 Système » : état du bot, du VPS, de BASE et des mises à jour — utile même sans aucun compte."""
from __future__ import annotations

import time
from pathlib import Path

from core import update as upd
from mt5 import base as basemod
from . import render
from .actions import Ctx
from .render import esc
from .ui import Keyboard

BOT_ROOT = Path(__file__).resolve().parent.parent


def _fmt_ts(ts: float) -> str:
    return time.strftime("%d/%m %H:%M", time.localtime(ts)) if ts else "?"


def tail(path: Path, n: int = 25, max_chars: int = 3500) -> str:
    """n dernières lignes d'un journal (UTF-8 ou UTF-16 pour MetaEditor), tronquées pour Telegram."""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return ""
    txt = basemod._decode(raw)
    lines = [l.rstrip() for l in txt.splitlines() if l.strip()]
    out = "\n".join(lines[-n:])
    return out[-max_chars:]


def actions_text(ctx: Ctx, limit: int = 25) -> str:
    """Journal des actions demandées depuis Telegram (30 jours conservés), les plus récentes d'abord.
    La même action envoyée à plusieurs comptes à quelques secondes d'intervalle ne fait qu'une ligne."""
    groups: list[dict] = []
    for r in ctx.db.actions_recent(limit * 8):
        inst = ctx.db.get(r["iid"])
        who = inst.label if inst else str(r["iid"])
        g = groups[-1] if groups else None
        if g and g["text"] == r["text"] and abs(g["ts"] - r["ts"]) <= 15:
            g["who"].append(who)
            g["ok"] += 1 if r["ok"] else 0
            continue
        if len(groups) >= limit:
            break
        groups.append({"ts": r["ts"], "text": r["text"], "who": [who], "ok": 1 if r["ok"] else 0, "result": r["result"]})
    if not groups:
        return "🧾 <b>Journal des actions</b>\n\n<i>Aucune action enregistrée.</i>"
    lines = []
    for g in groups:
        n = len(g["who"])
        icon = "✅" if g["ok"] == n else ("⚠️" if g["ok"] else "❌")
        target = g["who"][0] if n == 1 else f"{n} comptes"
        extra = "" if g["ok"] == n or n == 1 else f" ({n - g['ok']} échec(s))"
        tail_ = f" — {g['result'][:50]}" if n == 1 and not g["ok"] and g["result"] else ""
        lines.append(f"{_fmt_ts(g['ts'])} {icon} {g['text']} · {target}{extra}{tail_}")
    return "🧾 <b>Journal des actions</b> (30 jours)\n<pre>" + esc("\n".join(lines)) + "</pre>"


def system_text(ctx: Ctx, vps: dict[str, float]) -> str:
    cfg, db = ctx.cfg, ctx.db
    now = time.time()
    lines = ["🛠 <b>Système</b>", ""]
    # bot
    inst_c = db.meta_get("installed_commit")
    from core.version import bot_version
    lines.append(f"🤖 Bot <b>{esc(bot_version())}</b> actif depuis {render.ago(now - ctx.started_at)}"
                 + (f" · <code>{esc(inst_c.split()[0])}</code>" if inst_c else ""))
    lines.append(f"🖥 CPU {vps.get('cpu', 0):.0f}%  RAM {vps.get('ram', 0):.0f}%  Disque {vps.get('disk', 0):.0f}%"
                 + f"  ({basemod.free_disk_gb(cfg.paths.instances):.1f} Go libres)")
    if cfg.healthcheck_url:
        hc = db.meta_get("health_ok")
        lines.append("📡 Surveillance externe : " + ("✅ active" if hc == "1" else "⏳ en attente" if hc == "" else "❌ ping refusé (URL ?)"))
    else:
        lines.append("📡 Surveillance externe : non configurée (HEALTHCHECK_URL dans .env)")
    from core import backup
    lb = backup.last_backup(cfg)
    lines.append(f"💾 Sauvegarde de l'historique : " + (f"{_fmt_ts(lb.stat().st_mtime)} ({esc(str(lb.parent))})" if lb else "aucune encore (chaque nuit)"))
    # comptes
    views = ctx.views()
    running = sum(1 for v in views if ctx.mgr.is_running(v.inst))
    lines.append(f"👥 Comptes : {len(views)} · terminaux lancés : {running}")
    from mt5 import window as winmod
    for name, (ts, res) in sorted(winmod.LAST.items(), key=lambda x: -x[1][0])[:3]:
        if not res.get("window"):
            state = "❌ fenêtre introuvable"
        else:
            state = ("réduite" if res.get("minimized") else "non réduite") + (
                f", masqué : {esc(', '.join(res['hidden']))}" if res.get("hidden") else ", aucun panneau masqué")
        lines.append(f"🪟 {esc(name)} ({_fmt_ts(ts)}) : {state}")
    # BASE / EA
    probs = basemod.check_base(cfg)
    ex5 = cfg.paths.base / "MQL5" / "Experts" / cfg.mt5.ea_file
    ex5_ts = ex5.stat().st_mtime if ex5.exists() else 0
    lines.append("")
    lines.append(f"📦 BASE : {'✅ conforme' if not probs else '❌ ' + esc(' ; '.join(probs))}")
    lines.append(f"📈 EA v{esc(basemod.read_version(cfg.paths.base) or '?')} · compilé le {_fmt_ts(ex5_ts)}")
    # mises à jour
    lines.append("")
    if cfg.update.github:
        src = Path(cfg.update.source)
        info = upd.commit_info(src)
        err = db.meta_get("gh_err")
        lines.append(f"⬆ GitHub {esc(cfg.update.github)} : " + ("✅ joignable" if not err else "❌ " + esc(err)))
        if not cfg.github_token:
            lines.append("⚠️ GITHUB_TOKEN absent de .env : pas de mises à jour")
        exp = db.meta_get("gh_token_exp")
        if exp:
            from core.github import days_left
            left = days_left(exp)
            lines.append(f"🔑 Jeton GitHub valable jusqu'au {esc(exp)}" + (f" (⚠️ {left} j)" if left is not None and left <= 15 else ""))
        if info:
            msg = (info.get("message") or "").splitlines()[0][:80] if info.get("message") else ""
            lines.append(f"   dernière publication : <code>{esc(info.get('sha', '')[:7])}</code> {esc(msg)}")
    else:
        lines.append("⬆ Mises à jour automatiques : non configurées ([update] github)")
    if db.meta_get("update_pending"):
        lines.append("🔔 <b>Une mise à jour attend d'être installée</b> : touche ⬆ Mises à jour.")
    return "\n".join(lines)


def system_kb(pending: bool, previous: str = "") -> Keyboard:
    extra: Keyboard = [[(f"⏪ Revenir au bot {previous}", "d:rbk")]] if previous else []
    return extra + [
        [(("🔔 " if pending else "") + "⬆ Mises à jour", "d:upd"), ("🔨 Compiler l'EA", "d:bld")],
        [("📜 Journaux", "d:logs"), ("🪟 Réduire les terminaux", "d:win")],
        [("🏓 Ping", "d:png"), ("🧾 Journal des actions", "d:jra")],
        [("📊 Remettre le bouton du bas", "d:menu")],
        [("🔄 Redémarrer le bot", "d:rst"), ("🔃", "d:sys")],
        [("⬅ Accueil", "d:home")],
    ]


def crash_report(ctx: Ctx, now: float | None = None) -> tuple[str, bool]:
    """Plantages datés, du plus récent au plus ancien (3 max). (texte, il y a des plantages)."""
    import re
    try:
        raw = log_path(ctx, "crash.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        raw = ""
    parts = re.split(r"(?m)^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)$", raw)
    entries = [(parts[i], parts[i + 1].strip()) for i in range(1, len(parts) - 1, 2)]
    if not entries:
        return "💥 <b>Plantages</b>\n\n✅ Aucun plantage enregistré.", False
    now = now or time.time()
    lines = [f"💥 <b>Plantages</b> · {len(entries)} enregistré(s)"]
    for ts, tb in reversed(entries[-3:]):
        try:
            age = render.ago(now - time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S")))
        except ValueError:
            age = "?"
        last = [l for l in tb.splitlines() if l.strip()][-1:] or [""]
        lines.append(f"\n<b>{esc(ts)}</b> (il y a {age})\n<code>{esc(last[0][:200])}</code>")
    started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ctx.started_at))
    if entries[-1][0] < started:
        lines.append(f"\n✅ Aucun plantage depuis le démarrage du bot ({started[5:16]}).")
    return "\n".join(lines), True


def log_path(ctx: Ctx, name: str) -> Path:
    return ctx.cfg.paths.data / "logs" / name
