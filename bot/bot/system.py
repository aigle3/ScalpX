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
    from core import ghbackup
    rp = ghbackup.repo(cfg)
    if rp:                                                   # Bot 3.17.0 : ☁️ sauvegarde des presets
        err, ts = db.meta_get("ghb_err"), int(float(db.meta_get("ghb_ts") or 0))
        lines.append(f"☁️ Presets sauvegardés sur GitHub ({esc(rp)}) : "
                     + (f"⚠️ {esc(err)}" if err else (f"il y a {render.ago(now - ts)}" if ts else "en attente")))
        fe, fts = db.meta_get("fbk_err"), int(float(db.meta_get("fbk_ts") or 0))
        lines.append("☁️ Sauvegarde complète (historique, optimiseur, presets) : "
                     + (f"⚠️ {esc(fe)}" if fe else (f"il y a {render.ago(now - fts)}" if fts else "chaque jour, pas encore faite")))
    else:
        lines.append("☁️ Sauvegarde GitHub : non activée (☁️ Sauvegarde GitHub ci-dessous)")
    if cfg.healthcheck_url:
        hc = db.meta_get("health_ok")
        lines.append("📡 Surveillance externe : " + ("✅ active" if hc == "1" else "⏳ en attente" if hc == "" else "❌ ping refusé (URL ?)"))
    else:
        lines.append("📡 Surveillance externe : non configurée (🔑 Clés et jetons → HEALTHCHECK_URL)")
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
        if info:
            msg = (info.get("message") or "").splitlines()[0][:80] if info.get("message") else ""
            lines.append(f"   dernière publication : <code>{esc(info.get('sha', '')[:7])}</code> {esc(msg)}")
    else:
        lines.append("⬆ Mises à jour automatiques : non configurées ([update] github)")
    if db.meta_get("update_pending"):
        lines.append("🔔 <b>Une mise à jour attend d'être installée</b> : touche ⬆ Mises à jour.")
    if db.meta_get("install_pending"):
        lines.append("🔔 <b>L'installation système a une mise à jour</b> : touche 🔧 Installation système.")
    return "\n".join(lines)


def system_kb(pending: bool, previous: str = "", install: bool = False) -> Keyboard:
    extra: Keyboard = [[(f"⏪ Revenir au bot {previous}", "d:rbk")]] if previous else []
    return extra + [
        [(("🔔 " if pending else "") + "⬆ Mises à jour", "d:upd"), ("🔨 Compiler l'EA", "d:bld")],
        # Bot 3.19.0 : installation système (droits administrateur), 🔔 quand le paquet en apporte une nouvelle
        [(("🔔 " if install else "") + "🔧 Installation système", "d:rup")],
        [("🖥 Serveur", "d:srv"), ("📜 Journaux", "d:logs")],
        [("🏓 Ping", "d:png"), ("🧾 Journal des actions", "d:jra")],
        [("📊 Remettre le bouton du bas", "d:menu")],
        [("🔄 Redémarrer le bot", "d:rst"), ("🔃", "d:sys")],
        [("🔑 Clés et jetons", "d:key")],
        [("☁️ Sauvegarde GitHub", "d:bkp")],                                       # Bot 4.2.0 : activation, chiffrement, restauration
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



def keys_screen(ctx: Ctx, flash: str = "", waiting: str = "") -> tuple[str, Keyboard]:
    """Bot 3.18.0 : 🔑 clés du .env saisies depuis Telegram (le message est effacé, la valeur n'est jamais réaffichée)."""
    from .router import SECRETS
    lines = ["🔑 <b>Clés et jetons</b>", "<i>Touche une clé puis envoie sa valeur en message : enregistrée dans le .env du bot, "
             "ton message effacé aussitôt.</i>", ""]
    kb: Keyboard = []
    for key, (attr, hint, _) in SECRETS.items():
        val = getattr(ctx.cfg, attr, "") or ""
        shown = ("✅ " + (val[:11] + "…" + val[-4:] if len(val) > 20 else "défini")) if val else "➖ non défini"
        lines.append(f"<b>{key}</b> : {esc(shown)}\n   <i>{esc(hint)}</i>")
        kb.append([(("✏️ " if val else "➕ ") + key, f"d:kset:{key}")])
    if waiting in SECRETS:
        lines += ["", f"✍️ <b>J'attends la valeur de {waiting}</b> : envoie-la en message (ou « effacer »). "
                  "Tout autre bouton annule."]
    kb.append([("⬅ Système", "d:sys")])
    text = "\n".join(lines)
    return (f"{flash}\n\n{text}" if flash else text), kb


def backup_screen(ctx: Ctx, flash: str = "", waiting: str = "") -> tuple[str, Keyboard]:
    """Bot 4.2.0 / 4.8.3 : ☁️ sauvegarde sur GitHub (dépôt privé de l'utilisateur, chiffrée), en étapes cochées avec
    « 👉 À faire maintenant » : on voit toujours où on en est sans remonter la conversation."""
    from core import ghbackup
    cfg, db = ctx.cfg, ctx.db
    now = time.time()
    rp = ghbackup.repo(cfg)
    err = db.meta_get("ghb_err") or db.meta_get("fbk_err")
    pts, fts = int(float(db.meta_get("ghb_ts") or 0)), int(float(db.meta_get("fbk_ts") or 0))
    conflict = bool(cfg.backup_token and rp and db.meta_get("bkp_owner") != "1" and not fts and db.meta_get("bkp_conflict_ask"))
    plain = db.meta_get("backup_plain") == "1"
    tok, repo_ok, key_ok = bool(cfg.backup_token), bool(rp), bool(cfg.backup_key) or plain
    done = bool(pts or fts)

    def mark(ok: bool, now_: bool = False) -> str:
        return "✅" if ok else ("👉" if now_ else "⬜")
    lines = ["☁️ <b>Sauvegarde sur GitHub</b>",
             "<i>Presets, historique et optimiseur, dans un dépôt PRIVÉ de ton compte GitHub, chiffré par ton mot de passe.</i>", ""]
    lines.append(f"{mark(tok, not tok)} <b>1. Jeton GitHub</b>" + ("" if tok else " — à envoyer"))
    lines.append(f"{mark(repo_ok, tok and not repo_ok)} <b>2. Dépôt privé</b> : "
                 + (f"<b>{esc(rp)}</b>" if repo_ok else ("❌ " + esc(err) if tok and err else "créé ou retrouvé tout seul")))
    lines.append(f"{mark(key_ok, repo_ok and not key_ok)} <b>3. Chiffrement</b> : "
                 + ("mot de passe ✔" if cfg.backup_key else ("désactivé" if plain else "mot de passe à envoyer")))
    if conflict:
        lines.append("⏸ <b>4. Sauvegarde</b> : une sauvegarde d'un autre serveur existe déjà, rien n'est écrasé")
    else:
        lines.append(f"{mark(done, key_ok and not done)} <b>4. Sauvegarde</b> : "
                     + ("presets " + (f"il y a {render.ago(now - pts)}" if pts else "—") + " · complète "
                        + (f"il y a {render.ago(now - fts)}" if fts else "—") if done else "pas encore"))
    if err and repo_ok:
        lines.append(f"⚠️ {esc(err)}")
    # ce qu'il faut faire maintenant (une seule chose)
    lines.append("")
    if waiting == "BACKUP_TOKEN":
        todo = "✍️ <b>J'attends ton jeton GitHub</b> (ghp_… ou github_pat_…) : envoie-le en message, il est effacé aussitôt."
    elif waiting == "BACKUP_PASSWORD":
        todo = ("✍️ <b>J'attends ton mot de passe</b> de chiffrement (8 caractères au moins) : envoie-le en message, il est "
                "effacé aussitôt. <b>Note-le</b> : sans lui, la sauvegarde est illisible.")
    elif waiting == "RESTORE_PASSWORD":
        todo = "✍️ <b>J'attends le mot de passe de la sauvegarde</b> à restaurer : envoie-le en message."
    elif not tok:
        todo = "👉 <b>À faire</b> : touche 🔑 Envoyer le jeton GitHub (comment le créer : ci-dessous)."
    elif not repo_ok:
        todo = "👉 <b>À faire</b> : vérifie le jeton (case « repo » cochée) puis 🔑 Autre jeton."
    elif not key_ok:
        todo = "👉 <b>À faire</b> : touche 🔒 Chiffrer et envoie un mot de passe."
    elif conflict:
        todo = "👉 <b>À faire</b> : ☁️ Restaurer cette sauvegarde (avec SON mot de passe), ou 🔁 Remplacer par ce serveur."
    elif not done:
        todo = "⏳ Première sauvegarde en cours…"
    else:
        todo = "✅ <b>Tout est en place</b> : sauvegarde automatique (presets dès qu'ils changent, complète chaque nuit)."
    lines.append(todo)
    kb: Keyboard = []
    if not tok:
        lines += ["", "<b>Créer le jeton</b> : github.com → ta photo → Settings → Developer settings → Personal access tokens → "
                  "<b>Tokens (classic)</b> → Generate new token (classic) ; nom « scalpx-sauvegarde », coche <b>repo</b> → "
                  "Generate token → copie-le.",
                  "<i>Plus restreint : crée toi-même un dépôt PRIVÉ « scalpx-sauvegarde » puis un jeton « Fine-grained » limité à ce "
                  "dépôt (Contents : Read and write).</i>"]
        kb.append([("🔑 Envoyer le jeton GitHub", "d:kset:BACKUP_TOKEN")])
    else:
        if conflict:
            kb.append([("☁️ Restaurer", "d:frs"), ("🔁 Remplacer", "d:bkown")])
        elif key_ok:
            kb.append([("☁️ Sauvegarder maintenant", "d:fbk"), ("☁️ Restaurer", "d:frs")])
        kb.append([("🔒 " + ("Changer le mot de passe" if cfg.backup_key else "Chiffrer"), "d:bkpw")]
                  + ([] if key_ok else [("Sans chiffrement", "d:bkplain")]))
        kb.append([("🔑 Autre jeton", "d:kset:BACKUP_TOKEN"), ("🚫 Désactiver", "d:bkoff")])
    kb.append([("⬅ Système", "d:sys")])
    text = "\n".join(lines)
    return (f"{flash}\n\n{text}" if flash else text), kb
