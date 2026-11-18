"""Bot 4.10.0 : terminaux répartis en plusieurs environnements Wine (« guichets »).

Un préfixe Wine = un seul processus wineserver, qui ne travaille que sur UN processeur : avec 12 terminaux il saturait
(≈ 68 % d'un cœur) et les terminaux faisaient la queue. Chaque groupe a désormais son propre préfixe (et son wineserver) :
    groupe 1 → ~/.wine-g1, groupe 2 → ~/.wine-g2, …   (le préfixe principal ~/.wine garde BASE, MetaEditor et le testeur)

- au plus `group_size` terminaux par groupe (réglage [wine] group_size, 4 par défaut ; 0 = un seul préfixe comme avant) ;
- un nouveau compte va dans le groupe qui en a le moins ; tous pleins → nouveau groupe ;
- un compte n'est jamais déplacé tout seul, sauf la répartition initiale des comptes encore dans le préfixe principal ;
- le groupe d'un compte est noté dans son dossier (wine_groupe.txt) ;
- un préfixe de groupe est une COPIE du préfixe principal (mêmes clés de chiffrement Windows : MT5 y retrouve les mots
  de passe qu'il a mémorisés) ; son dossier Common (fichiers d'échange bot ↔ EA) est un lien vers celui du préfixe
  principal : le bot lit et écrit au même endroit pour tous les terminaux.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from pathlib import Path

log = logging.getLogger(__name__)
GROUP_FILE = "wine_groupe.txt"
_LOCK: asyncio.Lock | None = None


def _lock() -> asyncio.Lock:
    global _LOCK
    if _LOCK is None:
        _LOCK = asyncio.Lock()
    return _LOCK


def group_prefix(main: Path, n: int) -> Path:
    main = Path(main)
    return main.with_name(f"{main.name}-g{int(n)}")


def group_of(folder: Path) -> int:
    """Groupe noté dans le dossier du terminal (0 = préfixe principal)."""
    try:
        return max(0, int((Path(folder) / GROUP_FILE).read_text(encoding="utf-8").strip() or 0))
    except (OSError, ValueError):
        return 0


def set_group(folder: Path, n: int) -> None:
    p = Path(folder) / GROUP_FILE
    if n <= 0:
        p.unlink(missing_ok=True)
        return
    tmp = p.with_suffix(".tmp")
    tmp.write_text(str(int(n)), encoding="utf-8")
    tmp.replace(p)


def prefix_ready(pre: Path) -> bool:
    return (Path(pre) / "system.reg").is_file() and (Path(pre) / "drive_c").is_dir()


def prefix_for(folder: Path, main: Path) -> Path:
    """Préfixe Wine d'un terminal : celui de son groupe s'il est prêt, sinon le principal."""
    n = group_of(folder)
    if n > 0:
        pre = group_prefix(main, n)
        if prefix_ready(pre):
            return pre
    return Path(main)


def common_dir(prefix: Path, user: str | None = None) -> Path:
    user = user or os.environ.get("USER") or os.environ.get("LOGNAME") or "scalpx"
    return Path(prefix) / "drive_c" / "users" / user / "AppData" / "Roaming" / "MetaQuotes" / "Terminal" / "Common"


def ensure_prefix(main: Path, n: int, user: str | None = None) -> Path:
    """Préfixe du groupe n (copié du principal s'il n'existe pas). Bloquant : à appeler hors de la boucle du bot."""
    main = Path(main)
    dst = group_prefix(main, n)
    if prefix_ready(dst):
        _link_common(main, dst, user)
        return dst
    if not prefix_ready(main):
        raise RuntimeError(f"préfixe principal introuvable : {main}")
    tmp = dst.with_name(dst.name + ".copie")
    shutil.rmtree(tmp, ignore_errors=True)
    src_common = common_dir(main, user).resolve()

    def ignore(d: str, names: list[str]) -> list[str]:
        out = []
        for nm in names:
            full = Path(d) / nm
            if full == src_common or (nm == "Temp" and "users" in Path(d).parts):
                out.append(nm)                             # Common : remplacé par un lien ; Temp : inutile
        return out

    t0 = time.monotonic()
    shutil.copytree(main, tmp, symlinks=True, ignore=ignore)
    # liens absolus vers le préfixe principal (dosdevices…) : repointés vers la copie
    for p in tmp.rglob("*"):
        try:
            if p.is_symlink():
                target = os.readlink(p)
                if target.startswith(str(main) + "/") or target == str(main):
                    p.unlink()
                    p.symlink_to(str(tmp) + target[len(str(main)):])
        except OSError:
            continue
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    tmp.rename(dst)
    # les liens repointés vers « .copie » → vers le nom final
    for p in dst.rglob("*"):
        try:
            if p.is_symlink() and os.readlink(p).startswith(str(tmp)):
                t = os.readlink(p)
                p.unlink()
                p.symlink_to(str(dst) + t[len(str(tmp)):])
        except OSError:
            continue
    _link_common(main, dst, user)
    log.warning("environnement Wine n°%d créé (%s) en %.0f s", n, dst, time.monotonic() - t0)
    return dst


def _link_common(main: Path, dst: Path, user: str | None = None) -> None:
    """Common du groupe = lien vers Common du principal (échanges bot ↔ EA au même endroit)."""
    src = common_dir(main, user)
    src.mkdir(parents=True, exist_ok=True)
    link = common_dir(dst, user)
    if link.is_symlink() and Path(os.readlink(link)) == src:
        return
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.is_file():
        link.unlink()
    elif link.is_dir():
        shutil.rmtree(link, ignore_errors=True)
    link.symlink_to(src, target_is_directory=True)


def counts(folders: list[Path]) -> dict[int, int]:
    """Nombre de terminaux par groupe (≥ 1) parmi ces dossiers."""
    out: dict[int, int] = {}
    for f in folders:
        n = group_of(f)
        if n > 0:
            out[n] = out.get(n, 0) + 1
    return out


def choose(cnt: dict[int, int], size: int, existing: set[int] | None = None) -> int:
    """Groupe pour un nouveau terminal : le moins rempli parmi ceux qui ont de la place, sinon le premier numéro libre.
    Ex. taille 4 : 27 comptes → 7 groupes (6 × 4 + 3) ; 31 → 8 (7 × 4 + 3)."""
    groups = set(cnt) | set(existing or ())
    room = [g for g in groups if cnt.get(g, 0) < size]
    if room:
        return min(room, key=lambda g: (cnt.get(g, 0), g))
    n = 1
    while n in groups:
        n += 1
    return n


def existing_groups(main: Path) -> set[int]:
    main = Path(main)
    out = set()
    for p in main.parent.glob(f"{main.name}-g*"):
        tail = p.name[len(main.name) + 2:]
        if tail.isdigit() and prefix_ready(p):
            out.add(int(tail))
    return out


async def assign(cfg, db, folder: Path, exclude_id: str = "", say=None) -> int:
    """Place un terminal (pas encore lancé) dans un groupe ; crée l'environnement si besoin. 0 = groupes désactivés.
    Un terminal qui a déjà un groupe le garde."""
    size = int(getattr(cfg.wine, "group_size", 0) or 0)
    if size <= 0:
        return 0
    cur = group_of(folder)
    main = cfg.wine.options().prefix_path()
    if not prefix_ready(main):                             # pas de Wine ici (tests, Windows…) : rien à répartir
        return 0
    async with _lock():
        if cur > 0:
            await asyncio.to_thread(ensure_prefix, main, cur)
            return cur
        folders = [Path(i.folder) for i in db.list() if Path(i.folder) != Path(folder) and i.id != exclude_id]
        n = choose(counts(folders), size, existing_groups(main))
        if say and not prefix_ready(group_prefix(main, n)):
            await say(f"🍷 Préparation de l'environnement Wine n°{n} (1 à 3 min, une seule fois)…")
        await asyncio.to_thread(ensure_prefix, main, n)
        set_group(folder, n)
        return n


def summary(cfg, db) -> str:
    """« 3 environnements : 4 · 4 · 4 » (+ comptes encore dans le principal)."""
    size = int(getattr(cfg.wine, "group_size", 0) or 0)
    if size <= 0:
        return ""
    folders = [Path(i.folder) for i in db.list()]
    cnt = counts(folders)
    rest = len(folders) - sum(cnt.values())
    if not cnt:
        return f"🍷 Wine : un seul environnement ({len(folders)} terminal(aux)) · répartition par {size} à venir"
    parts = " · ".join(str(cnt[g]) for g in sorted(cnt))
    return (f"🍷 Wine : {len(cnt)} environnement(s) ({parts}, {size} max chacun)"
            + (f" · {rest} encore dans le principal" if rest else ""))


async def rebalance(ctx, say=None, wait_s: float = 600.0) -> list[str]:
    """Répartition initiale : chaque compte encore dans le préfixe principal passe dans un groupe, UN PAR UN (arrêt propre,
    relance dans son environnement, attente de l'EA connecté). Échec → remis dans le principal et relancé.
    Renvoie les lignes du compte rendu."""
    from storage.db import DEMARRE, MAINTENANCE
    cfg, db, mgr, status = ctx.cfg, ctx.db, ctx.mgr, ctx.status
    if int(getattr(cfg.wine, "group_size", 0) or 0) <= 0:
        return []
    todo = [i for i in db.list() if group_of(Path(i.folder)) == 0 and mgr.folder_ok(i) and i.desired_state != "EN_CREATION"]
    report: list[str] = []
    for k, inst in enumerate(todo, 1):
        running = mgr.is_running(inst)
        want_run = running or inst.desired_state in (DEMARRE, MAINTENANCE)
        if say:
            await say(f"🍷 Répartition des terminaux : {k}/{len(todo)} · {inst.label}…")
        try:
            n = await assign(cfg, db, Path(inst.folder), inst.id, say)   # environnement prêt AVANT l'arrêt du terminal
        except Exception as exc:
            log.exception("répartition %s", inst.id)
            report.append(f"❌ {inst.label} : {exc}")
            continue
        if not running:
            if want_run and not mgr.is_running(inst):
                mgr.launch(inst)
            report.append(f"✅ {inst.label} → n°{n}" + ("" if want_run else " (arrêté)"))
            continue
        r = await mgr.stop(inst, force=False, set_desired=None)
        if r == "attente_cloture":
            report.append(f"⏳ {inst.label} : clôture à 500 en cours, passera dans le n°{n} à sa prochaine relance")
            continue
        if not mgr.is_running(inst):                       # le watchdog a pu le relancer entre-temps (déjà dans son groupe)
            mgr.launch(inst)
        if await _connected(status, inst.id, wait_s):
            report.append(f"✅ {inst.label} → n°{n}")
            continue
        # échec : retour dans le préfixe principal
        log.warning("répartition : %s ne se connecte pas dans l'environnement %d : retour au principal", inst.id, n)
        set_group(Path(inst.folder), 0)
        await mgr.stop(inst, force=True, set_desired=None)
        if not mgr.is_running(inst):
            mgr.launch(inst)
        report.append(f"↩️ {inst.label} : pas connecté dans le n°{n}, remis dans le principal")
    return report


async def _connected(status, iid: str, wait_s: float) -> bool:
    end = time.monotonic() + wait_s
    while time.monotonic() < end:
        st = status.read(iid)
        if st is not None and st.connecte and st.age_s() < 60:
            return True
        await asyncio.sleep(3)
    return False
