"""Bot 3.10.0 : ⚡ Optimiser le VPS (🛠 Système).

Le bot lance vps_optimize.ps1 (réglages Windows qui libèrent du CPU, sans toucher aux comptes) et mesure le CPU
avant / après. Tout est idempotent : relancer l'optimisation ne refait que ce qui manque.
- exclusions de l'antivirus Defender (dossiers MT5 / bot / fichiers d'échange, programmes MT5 et Python) ;
- services inutiles sur un VPS de trading arrêtés et désactivés (indexation, télémétrie, Xbox, cartes, géolocalisation) ;
- Gestionnaire de serveur plus ouvert à la connexion ; mode d'alimentation « Performances élevées » ; effets visuels réduits ;
- tâche « ScalpX Bot » passée en « privilèges les plus élevés » (le bot tourne alors en administrateur).
Les réglages Windows qui exigent les droits administrateur sont signalés si le bot ne les a pas.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from core.config import Config

log = logging.getLogger(__name__)
BOT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = BOT_ROOT / "vps_optimize.ps1"
EXCLUDED_PROCS = ("terminal64.exe", "metatester64.exe", "metaeditor64.exe", "python.exe", "pythonw.exe")

LABELS = {
    "admin": "Droits administrateur du bot",
    "defender": "Antivirus : dossiers et programmes MT5 / bot exclus",
    "wsearch": "Indexation Windows Search",
    "diagtrack": "Télémétrie Windows",
    "dmwappush": "Service de routage push (télémétrie)",
    "maps": "Cartes téléchargées",
    "geo": "Géolocalisation",
    "xbox": "Services Xbox",
    "servermanager": "Gestionnaire de serveur à la connexion",
    "power": "Alimentation « Performances élevées »",
    "visual": "Effets visuels réduits (prochaine connexion)",
    "task": "Tâche « ScalpX Bot » en administrateur",
}
ICONS = {"ok": "✅", "deja": "✔️", "absent": "➖", "admin": "🔒", "echec": "❌", "non": "🔒"}


@dataclass
class Item:
    key: str
    state: str          # ok (fait), deja (déjà fait), absent (rien à faire), admin (droits manquants), echec
    detail: str = ""


@dataclass
class Report:
    items: list[Item] = field(default_factory=list)
    cpu_before: float = 0.0
    cpu_after: float = 0.0
    top: list[tuple[str, float, float]] = field(default_factory=list)   # (processus, CPU %, RAM Mo) hors MT5 / bot
    error: str = ""

    @property
    def admin(self) -> bool:
        return any(i.key == "admin" and i.state == "ok" for i in self.items)


def is_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())   # type: ignore[attr-defined]
    except Exception:
        return False


def exclusion_paths(cfg: Config) -> list[str]:
    """Dossiers lus et écrits sans arrêt par MT5 et le bot (chemins absolus, sans doublon)."""
    out: list[str] = []
    common = cfg.paths.common()
    # dossier MetaQuotes de l'utilisateur (Common\Files : statuts, commandes, événements ; agents du testeur)
    mq = common.parent.parent.parent if common.name.lower() == "files" else common
    data = cfg.paths.data if cfg.paths.data.is_absolute() else BOT_ROOT / cfg.paths.data
    for p in (cfg.paths.base, cfg.paths.instances, mq, BOT_ROOT, data):
        s = str(Path(p))
        if s and s not in out:
            out.append(s)
    return out


def parse_output(text: str) -> list[Item]:
    """Une ligne JSON par réglage : {"k": clé, "s": état, "d": détail} ; les autres lignes sont ignorées."""
    items: list[Item] = []
    for line in (text or "").splitlines():
        line = line.strip().lstrip("﻿")
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and d.get("k"):
            items.append(Item(str(d["k"]), str(d.get("s") or ""), str(d.get("d") or "")))
    return items


def run_script(cfg: Config, timeout: float = 240.0) -> tuple[list[Item], str]:
    """Lance vps_optimize.ps1 (Windows seulement). (réglages, erreur)."""
    if os.name != "nt":
        return [], "réservé à Windows"
    if not SCRIPT.is_file():
        return [], f"{SCRIPT.name} absent"
    args = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
            "-Paths", ";".join(exclusion_paths(cfg)), "-Procs", ";".join(EXCLUDED_PROCS)]
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"script non lancé : {exc}"
    out = r.stdout.decode("utf-8", errors="replace")
    items = parse_output(out)
    err = "" if items else (r.stderr.decode("utf-8", errors="replace").strip()[-300:] or f"code {r.returncode}")
    return items, err


def cpu_avg(seconds: float) -> float:
    import psutil
    return float(psutil.cpu_percent(interval=max(0.5, seconds)))


def top_processes(n: int = 6, sample_s: float = 3.0) -> list[tuple[str, float, float]]:
    """Plus gros consommateurs de CPU hors terminaux MT5 et bot : (nom, CPU % du VPS, RAM Mo), cumulés par nom."""
    import psutil
    ignore = {p.lower() for p in EXCLUDED_PROCS} | {"system idle process", "idle", ""}
    procs = []
    for p in psutil.process_iter(["name"]):
        try:
            if (p.info.get("name") or "").lower() in ignore:
                continue
            p.cpu_percent(None)
            procs.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    time.sleep(sample_s)
    ncpu = psutil.cpu_count() or 1
    agg: dict[str, list[float]] = {}
    for p in procs:
        try:
            cpu = p.cpu_percent(None) / ncpu
            mem = p.memory_info().rss / 1048576
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        a = agg.setdefault(p.info.get("name") or "?", [0.0, 0.0])
        a[0] += cpu
        a[1] += mem
    rows = sorted(((k, v[0], v[1]) for k, v in agg.items()), key=lambda r: (-r[1], -r[2]))
    return [r for r in rows if r[1] >= 0.5][:n]


def optimize(cfg: Config, measure_s: float = 10.0, settle_s: float = 15.0) -> Report:
    """CPU moyen avant, réglages Windows, CPU moyen après (mesure indicative : le marché fait varier la charge)."""
    rep = Report()
    try:
        rep.cpu_before = cpu_avg(measure_s)
    except Exception:
        log.warning("mesure du CPU impossible", exc_info=True)
    rep.items, rep.error = run_script(cfg)
    for i in rep.items:
        log.info("optimisation du VPS : %s → %s %s", i.key, i.state, i.detail)
    if rep.error:
        log.warning("optimisation du VPS : %s", rep.error)
    try:
        time.sleep(max(0.0, settle_s))
        rep.cpu_after = cpu_avg(measure_s)
        rep.top = top_processes()
    except Exception:
        log.warning("mesure du CPU impossible", exc_info=True)
    return rep


def report_text(rep: Report, esc=lambda s: s) -> str:
    lines = ["⚡ <b>Optimisation du VPS</b>", ""]
    if rep.error and not rep.items:
        lines.append(f"❌ {esc(rep.error)}")
    for i in rep.items:
        label = LABELS.get(i.key, i.key)
        if i.key == "admin":
            lines.append(("✅ " if i.state == "ok" else "🔒 ") + label + (" : oui" if i.state == "ok" else " : non"))
            continue
        extra = {"deja": " (déjà fait)", "absent": " (non installé)", "admin": " : droits administrateur requis"}.get(i.state, "")
        det = f" — {esc(i.detail)}" if i.detail and i.state in ("ok", "echec") else ""
        lines.append(f"{ICONS.get(i.state, '•')} {esc(label)}{extra}{det}")
    lines.append("")
    lines.append("📈 MT5 : 5 000 barres max par graphique (bot) et Market Watch limité à l'or (EA 7.11.0) : "
                 "pris en compte au prochain lancement de chaque terminal (🖥 Terminaux → 🔄 Redémarrer tous).")
    if rep.cpu_before or rep.cpu_after:
        lines.append(f"🖥 CPU moyen : {rep.cpu_before:.0f} % avant → {rep.cpu_after:.0f} % après "
                     "<i>(indicatif : la charge varie avec le marché)</i>")
    if rep.top:
        lines.append("")
        lines.append("🔎 <b>Autres processus qui consomment</b> (hors MT5 et bot) :")
        lines += [f"   • {esc(n)} : {c:.0f} % CPU, {m:.0f} Mo" for n, c, m in rep.top]
    if not rep.admin and rep.items:
        lines.append("")
        lines.append("🔒 <b>Le bot n'est pas administrateur.</b> Une seule fois, dans PowerShell <b>ouvert en administrateur</b> "
                     "sur le VPS, colle :\n<code>" + esc(ADMIN_CMD) + "</code>\npuis 🔄 Redémarrer le bot et relance ⚡ Optimiser le VPS.")
    return "\n".join(lines)


ADMIN_CMD = ("$t = Get-ScheduledTask -TaskName 'ScalpX Bot'; "
             "Set-ScheduledTask -TaskName 'ScalpX Bot' -Principal (New-ScheduledTaskPrincipal -UserId $t.Principal.UserId "
             "-LogonType Interactive -RunLevel Highest)")
