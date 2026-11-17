"""Bot 3.17.0 : ♻️ Réinitialiser le serveur (🛠 Système).

Repart de zéro SANS toucher au bot, à BASE ni aux presets :
- tous les terminaux arrêtés (comptes et terminal de backtest) ;
- dossiers des comptes, archives et terminal de backtest supprimés ;
- historique effacé (comptes, soldes, cycles, retraits, événements, déploiements, labo, optimiseur, backtests, journal) ;
- fichiers d'échange bot ↔ EA (Common\\Files) supprimés.
Les positions et ordres restent chez le broker (TP en place) : seuls les terminaux de CE serveur disparaissent.
Les comptes se recréent ensuite par ➕ (mot de passe redemandé). Le bot redémarre à la fin (mémoire remise à zéro).
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from core.presets import EXPORT_NAME

log = logging.getLogger(__name__)

TABLES = ("instances", "balances", "events", "cycles", "closures500", "withdrawals", "versions", "deployments",
          "incidents", "actions", "labo", "opt_runs", "backtests")
# réglages du bot lui-même gardés (mises à jour, tableau de bord, compilation, serveurs mémorisés, sauvegarde GitHub)
KEEP_META = ("installed_commit", "update_pending", "update_offered", "dash_msg_id", "gh_err", "gh_token_exp",
             "gh_exp_alert", "compile_s", "autobuild_stamp", "build_notified", "servers_extra", "preset_conseille_v",
             "health_ok", "ghb_hash", "ghb_ts", "ghb_err", "install_pending", "install_offered", "keys_asked", "fbk_ts", "fbk_err", "fbk_size", "webview")


@dataclass
class ResetReport:
    folders: list[str] = field(default_factory=list)
    files: int = 0
    rows: int = 0
    errors: list[str] = field(default_factory=list)


def _clear_dir(d: Path, rep: ResetReport) -> None:
    """Contenu du dossier supprimé (le dossier lui-même reste)."""
    if not d.is_dir():
        return
    for p in d.iterdir():
        try:
            shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()
            rep.folders.append(str(p))
        except OSError as exc:
            rep.errors.append(f"{p.name} : {exc}")


def wipe_files(instances: Path, archives: Path, backtest: Path, common: Path, base: Path) -> ResetReport:
    """Fichiers : comptes, archives, terminal de backtest, Common\\Files. Garde-fou : jamais BASE ni un dossier qui la contient."""
    rep = ResetReport()
    base = Path(base).resolve()
    for d in (instances, archives):
        d = Path(d)
        if d.resolve() == base or base.is_relative_to(d.resolve()):
            rep.errors.append(f"{d} contient BASE : non vidé")
            continue
        _clear_dir(d, rep)
    bt = Path(backtest)
    if bt.is_dir() and bt.resolve() != base and not base.is_relative_to(bt.resolve()):
        try:
            shutil.rmtree(bt)
            rep.folders.append(str(bt))
        except OSError as exc:
            rep.errors.append(f"backtest : {exc}")
    if Path(common).is_dir():
        for p in Path(common).iterdir():
            if p.is_file() and p.name != EXPORT_NAME:
                try:
                    p.unlink()
                    rep.files += 1
                except OSError as exc:
                    rep.errors.append(f"{p.name} : {exc}")
    return rep


def wipe_db(db, rep: ResetReport) -> None:
    """Historique et comptes effacés ; seuls les réglages du bot lui-même (KEEP_META) restent."""
    for t in TABLES:
        try:
            rep.rows += db._exec(f"DELETE FROM {t}").rowcount or 0
        except Exception as exc:                                   # table absente (ancienne base)
            rep.errors.append(f"{t} : {exc}")
    marks = ",".join("?" * len(KEEP_META))
    rep.rows += db._exec(f"DELETE FROM meta WHERE key NOT IN ({marks})", KEEP_META).rowcount or 0
