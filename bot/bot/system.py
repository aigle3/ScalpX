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
        [("📜 Journal du bot", "d:log"), ("🧾 Journal compilation", "d:clog")],
        [("💥 Plantages", "d:crash"), ("🏓 Ping", "d:png")],
        [("🔄 Redémarrer le bot", "d:rst")],
        [("🔃 Actualiser", "d:sys"), ("⬅ Retour", "d:home")],
    ]


def log_path(ctx: Ctx, name: str) -> Path:
    return ctx.cfg.paths.data / "logs" / name
