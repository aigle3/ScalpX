"""Bot 4.10.0 : terminaux répartis en plusieurs environnements Wine (un wineserver chacun)."""
import os
from pathlib import Path

import pytest

from mt5 import groups


def test_repartition_par_4_quel_que_soit_le_nombre():
    for total, want in ((12, [4, 4, 4]), (13, [4, 4, 4, 1]), (27, [4] * 6 + [3]), (31, [4] * 7 + [3]), (30, [4] * 7 + [2])):
        cnt: dict[int, int] = {}
        for _ in range(total):
            g = groups.choose(cnt, 4)
            cnt[g] = cnt.get(g, 0) + 1
        assert sorted(cnt.values(), reverse=True) == want
    assert groups.choose({1: 4, 2: 2, 3: 4}, 4) == 2               # place libérée : reprise en premier
    assert groups.choose({1: 4}, 4, {1, 2}) == 2                    # environnement déjà créé mais vide


def _prefix(p: Path, user: str) -> Path:
    (p / "drive_c" / "windows").mkdir(parents=True)
    (p / "system.reg").write_text("x")
    (p / "dosdevices").mkdir()
    os.symlink("../drive_c", p / "dosdevices" / "c:")
    os.symlink(str(p / "drive_c"), p / "dosdevices" / "d:")          # lien absolu vers le principal
    files = groups.common_dir(p, user) / "Files"
    files.mkdir(parents=True)
    (files / "statut.json").write_text("{}")
    (p / "drive_c" / "users" / user / "Temp").mkdir(parents=True)
    (p / "drive_c" / "users" / user / "Temp" / "gros.tmp").write_text("x")
    return p


def test_copie_du_prefixe_common_partage(tmp_path):
    main = _prefix(tmp_path / ".wine", "u")
    g1 = groups.ensure_prefix(main, 1, "u")
    assert g1 == tmp_path / ".wine-g1" and groups.prefix_ready(g1)
    link = groups.common_dir(g1, "u")
    assert link.is_symlink() and (link / "Files" / "statut.json").is_file()      # même dossier d'échange bot ↔ EA
    (link / "Files" / "nouveau.json").write_text("1")
    assert (groups.common_dir(main, "u") / "Files" / "nouveau.json").is_file()
    assert os.readlink(g1 / "dosdevices" / "c:") == "../drive_c"
    assert os.readlink(g1 / "dosdevices" / "d:") == str(g1 / "drive_c")         # repointé vers la copie
    assert not (g1 / "drive_c" / "users" / "u" / "Temp").exists()
    assert groups.ensure_prefix(main, 1, "u") == g1                              # déjà prêt : rien recopié
    assert groups.existing_groups(main) == {1}


def test_prefixe_du_terminal_et_commande_wine(tmp_path):
    from mt5.procs import PsutilProcesses
    from mt5.wine import WineOptions
    main = _prefix(tmp_path / ".wine", os.environ.get("USER") or os.environ.get("LOGNAME") or "scalpx")
    term = tmp_path / "inst" / "a"
    term.mkdir(parents=True)
    exe = term / "terminal64.exe"
    procs = PsutilProcesses(WineOptions(prefix=main))
    assert procs.command(exe, ["/portable"])[1]["WINEPREFIX"] == str(main)       # pas de groupe : principal
    groups.set_group(term, 2)
    assert procs.command(exe, ["/portable"])[1]["WINEPREFIX"] == str(main)       # groupe pas encore prêt : principal
    groups.ensure_prefix(main, 2)
    assert procs.command(exe, ["/portable"])[1]["WINEPREFIX"] == str(tmp_path / ".wine-g2")
    groups.set_group(term, 0)
    assert groups.group_of(term) == 0


@pytest.mark.asyncio
async def test_assign_un_nouveau_compte(tmp_path):
    from types import SimpleNamespace as NS
    from mt5.wine import WineOptions
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or "scalpx"
    main = _prefix(tmp_path / ".wine", user)
    folders = []
    for k in range(5):
        f = tmp_path / "inst" / str(k)
        f.mkdir(parents=True)
        folders.append(f)
    insts = [NS(id=str(k), folder=str(f)) for k, f in enumerate(folders)]
    cfg = NS(wine=NS(group_size=2, options=lambda: WineOptions(prefix=main)))
    db = NS(list=lambda: insts)
    got = [await groups.assign(cfg, db, f, str(k)) for k, f in enumerate(folders)]
    assert sorted(got) == [1, 1, 2, 2, 3]
    assert await groups.assign(cfg, db, folders[0], "0") == got[0]               # garde son groupe
    assert "3 environnement(s)" in groups.summary(cfg, db)
    cfg.wine.group_size = 0
    assert await groups.assign(cfg, db, tmp_path, "x") == 0 and groups.summary(cfg, db) == ""


@pytest.mark.asyncio
async def test_repartition_initiale_un_par_un_et_retour_si_echec(tmp_path):
    import time
    from types import SimpleNamespace as NS
    from mt5.wine import WineOptions
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or "scalpx"
    main = _prefix(tmp_path / ".wine", user)
    insts = []
    for k in range(3):
        f = tmp_path / "inst" / f"{k}_S"
        f.mkdir(parents=True)
        insts.append(NS(id=f"{k}_S", folder=str(f), label=str(k), desired_state="DEMARRE"))
    running = {i.id for i in insts}
    launched = []

    async def stop(inst, force=False, set_desired=None):
        running.discard(inst.id)
        return "propre"

    def launch(inst, ini=None, by_bot=True):
        running.add(inst.id)
        launched.append((inst.id, groups.group_of(Path(inst.folder))))

    mgr = NS(is_running=lambda i: i.id in running, folder_ok=lambda i: True, stop=stop, launch=launch)
    ok = NS(connecte=True, age_s=lambda: 1)
    status = NS(read=lambda iid: None if iid == "2_S" else ok)          # le 3e ne se connecte pas
    cfg = NS(wine=NS(group_size=2, options=lambda: WineOptions(prefix=main)))
    ctx = NS(cfg=cfg, db=NS(list=lambda: insts), mgr=mgr, status=status)
    said = []

    async def say(t):
        said.append(t)
    rep = await groups.rebalance(ctx, say, wait_s=0.1)
    assert launched[:2] == [("0_S", 1), ("1_S", 1)] and launched[-1] == ("2_S", 0)
    assert groups.group_of(Path(insts[2].folder)) == 0 and running == {"0_S", "1_S", "2_S"}
    assert rep[0].startswith("✅") and rep[2].startswith("↩️") and any("1/3" in t for t in said)


def test_annonce_news_une_seule_fois_pour_tous_les_comptes():
    from core import service
    from core.models import Evenement
    service._NEWS_SENT.clear()
    body = "\n━━━━\n🔴 USD · EIA Crude Oil Stocks Change\n🕐 16:30 (heure de France)"
    a = service.event_to_notice(Evenement(id="1_S", type="NOTIF", texte="📰 NEWS DANS 16 min" + body), "1", Path("."))
    b = service.event_to_notice(Evenement(id="2_S", type="NOTIF", texte="📰 NEWS DANS 15 min" + body), "2", Path("."))
    c = service.event_to_notice(Evenement(id="2_S", type="NOTIF", texte="📰 NEWS DANS 15 min\n━━━━\n🔴 USD · NFP"), "2", Path("."))
    assert a is not None and a.label == "Tous les comptes" and b is None and c is not None


@pytest.mark.asyncio
async def test_retour_a_un_seul_environnement(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS
    from mt5.wine import WineOptions
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or "scalpx"
    main = _prefix(tmp_path / ".wine", user)
    groups.ensure_prefix(main, 1)
    groups.ensure_prefix(main, 2)
    insts = []
    for k in range(3):
        f = tmp_path / "inst" / f"{k}_S"
        f.mkdir(parents=True)
        groups.set_group(f, 1 + k % 2)
        insts.append(NS(id=f"{k}_S", folder=str(f), label=str(k), desired_state="DEMARRE"))
    running = {"0_S", "1_S"}
    launched = []

    async def stop(inst, force=False, set_desired=None):
        running.discard(inst.id)
        return "propre"

    def launch(inst, ini=None, by_bot=True):
        running.add(inst.id)
        launched.append((inst.id, groups.group_of(Path(inst.folder))))

    async def fast_sleep(_):
        return None
    monkeypatch.setattr(groups.asyncio, "sleep", fast_sleep)
    cfg = NS(wine=NS(group_size=0, options=lambda: WineOptions(prefix=main)))
    db = NS(list=lambda include_archived=False: insts)
    ctx = NS(cfg=cfg, db=db, mgr=NS(is_running=lambda i: i.id in running, stop=stop, launch=launch),
             status=NS(read=lambda iid: NS(connecte=True, age_s=lambda: 1)))
    assert len(groups.leftovers(cfg, db)[0]) == 3
    rep = await groups.regroup(ctx, None, wait_s=1)
    assert all(g == 0 for _, g in launched) and running == {"0_S", "1_S", "2_S"}
    assert not (tmp_path / ".wine-g1").exists() and not (tmp_path / ".wine-g2").exists()
    assert groups.leftovers(cfg, db) == ([], []) and any("2 environnement(s)" in r for r in rep)


def test_terminal_attache_a_un_processeur(tmp_path):
    """Bot 4.12.0 : tous les fils d'un terminal sur UN processeur (ici une copie de « sleep » nommée terminal64.exe)."""
    import shutil
    import subprocess
    from mt5.procs import PsutilProcesses, norm_path, pin_terminals
    if (os.cpu_count() or 1) < 2 or not hasattr(os, "sched_setaffinity"):
        pytest.skip("un seul processeur")
    exe = tmp_path / "inst" / "terminal64.exe"
    exe.parent.mkdir()
    import sys
    os.symlink(sys.executable, exe)                         # (« sleep » peut être un binaire multi-outils selon le système)
    p = subprocess.Popen([str(exe), "-c", "import time; time.sleep(20)"])
    try:
        import time
        time.sleep(0.5)
        n = pin_terminals(PsutilProcesses(), {norm_path(exe): 5}, ncpu=2)
        assert n >= 1 and os.sched_getaffinity(p.pid) == {1}
        assert pin_terminals(PsutilProcesses(), {norm_path(exe): 5}, ncpu=2) == 0       # déjà réglé : rien à refaire
    finally:
        p.kill()


def test_repartition_processeurs_equilibree_et_stable():
    """Bot 4.14.5 : 12 terminaux en marche sur 4 cœurs → 3 par cœur ; l'ordre ne dépend que des numéros de compte."""
    from types import SimpleNamespace as NS
    from collections import Counter
    from mt5.procs import pin_plan
    insts = [NS(id=f"{n}_X", login=n) for n in (52040335, 37344723, 52030694, 52030697, 52030842, 52031042, 52033030,
                                                 52034237, 52034926, 52036631, 52037679, 52039799)]
    plan = pin_plan(insts, ncpu=4)
    assert sorted(Counter(plan.values()).values()) == [3, 3, 3, 3]
    assert pin_plan(list(reversed(insts)), ncpu=4) == plan                         # ordre de la base sans effet
    p11 = pin_plan(insts[:-1], ncpu=4)                                               # 11 terminaux : le cœur 0 en a le moins
    assert Counter(p11.values())[0] == 2 and max(Counter(p11.values()).values()) == 3
