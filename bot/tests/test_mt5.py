import asyncio
import json
import logging
import shutil
import time
from pathlib import Path

import pytest

from core.fsio import atomic_write
from core.ids import make_id
from mt5 import base as basemod
from mt5.creator import InstanceCreator
from mt5.deploy import DeployManager, plan_deploy
from mt5.ini import INI_NAME
from mt5.watchdog import Watchdog
from storage.db import ARRETE, DEMARRE, EN_CREATION, MAINTENANCE, Instance

PWD = "Sup3r-Secret-PW"


def seed(stack, login=111, server="Demo-Server 1", etat="ACTIF", cycle=0, running=True, state=DEMARRE):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    iid = make_id(login, server)
    dest = cfg.paths.instances / iid
    shutil.copytree(cfg.paths.base, dest)
    inst = Instance(iid, login, server, "", str(dest), int(time.time()), "5.0", state)
    db.add_instance(inst)
    ea.accounts[iid] = {"etat": etat, "cycle": cycle, "solde": 130.0, "positions": 0, "maintenance": False,
                        "phase500": False, "connecte": True, "version": "5.0"}
    if running:
        procs.launch(dest / "terminal64.exe")
    return inst


# ---------------------------------------------------------------- création
@pytest.mark.asyncio
async def test_creation_succes_ini_supprime_base_inchange_mdp_absent(stack, caplog):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    caplog.set_level(logging.DEBUG)
    h0 = basemod.tree_hash(cfg.paths.base)
    ea.start()
    steps = []

    async def prog(t):
        steps.append(t)

    try:
        r = await InstanceCreator(cfg, db, mgr, status).create("12345", PWD, "Demo-Server 1", "Mon démo", prog)
    finally:
        await ea.stop()
    iid = make_id(12345, "Demo-Server 1")
    assert r.ok and r.iid == iid, r.message
    inst = db.get(iid)
    assert inst.desired_state == DEMARRE and inst.alias == "Mon démo" and inst.ea_version == "5.0"
    assert not (cfg.paths.instances / iid / INI_NAME).exists()                    # .ini supprimé après succès
    assert procs.run_inis[0] is None and "Expert=ScalpX_v5" in procs.run_inis[1]        # relance : EA rouvert via [StartUp]
    assert "Password" not in procs.run_inis[1] and "Login=12345" in procs.run_inis[1]   # compte désigné, jamais de mot de passe
    assert procs.launches == [(iid, True), (iid, False)]                          # 1er avec .ini de création, relance avec .ini SANS secret
    assert any(PWD in ini for ini in ea.ini_seen)                                  # le mot de passe n'a existé que dans le .ini
    assert basemod.tree_hash(cfg.paths.base) == h0                                 # BASE inchangé
    # mot de passe introuvable dans la base SQLite, les logs, le dossier de l'instance
    assert PWD not in caplog.text
    assert PWD.encode() not in Path(db.path).read_bytes()
    for f in (cfg.paths.instances / iid).rglob("*"):
        if f.is_file():
            assert PWD.encode() not in f.read_bytes(), f


@pytest.mark.asyncio
async def test_creation_mauvais_mot_de_passe_aucun_fantome(stack, caplog):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    cfg.timing.create_timeout_s = 2
    ea.password_ok = False
    ea.start()
    try:
        r = await InstanceCreator(cfg, db, mgr, status).create("999", PWD, "Demo-Server 1", "", None or (lambda t: _aw()))
    finally:
        await ea.stop()
    iid = make_id(999, "Demo-Server 1")
    assert not r.ok and "connecté" in r.message
    assert db.get(iid) is None and db.list(include_archived=True) == []             # aucun fantôme dans le registre
    assert not (cfg.paths.instances / iid).exists()                                   # dossier déplacé
    arch = list(cfg.paths.archives.iterdir())
    assert len(arch) == 1 and "ECHEC" in arch[0].name
    assert not (arch[0] / INI_NAME).exists()                                          # .ini absent après échec
    assert not procs.running                                                          # terminal arrêté
    assert PWD not in caplog.text and PWD not in r.message


async def _aw():
    return None


@pytest.mark.asyncio
async def test_creation_timeout_sans_heartbeat(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    cfg.timing.create_timeout_s = 1                       # pas de FakeEA : aucun heartbeat
    r = await InstanceCreator(cfg, db, mgr, status).create("5", PWD, "Demo-Server 1")
    assert not r.ok and "heartbeat" in r.message.lower() and db.list(include_archived=True) == []


@pytest.mark.asyncio
async def test_creation_refus(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    c = InstanceCreator(cfg, db, mgr, status)
    seed(stack, 12345, "Demo-Server 1", running=False)
    assert "existe déjà" in (c.precheck("12345", "Demo-Server 1") or "")           # compte déjà existant
    assert "serveur invalide" in (c.precheck("1", "Pi;rate") or "")                           # nom de serveur invalide
    assert "invalide" in (c.precheck("12a", "Demo-Server 1") or "")
    (cfg.paths.base / "Profiles" / "Charts" / "Default" / "chart01.chr").write_text("<chart>\n<expert>\nname=X\n</expert>\n</chart>")
    assert "BASE non conforme" in (c.precheck("7", "Demo-Server 1") or "")           # EA dans le profil BASE
    r = await c.create("7", PWD, "Demo-Server 1")
    assert not r.ok and db.get(make_id(7, "Demo-Server 1")) is None and not cfg.paths.instances.joinpath(make_id(7, "Demo-Server 1")).exists()


@pytest.mark.asyncio
async def test_creation_doublon_graphique_alerte(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    ea.charts = 2
    ea.start()
    try:
        r = await InstanceCreator(cfg, db, mgr, status).create("4", PWD, "Demo-Server 1")
    finally:
        await ea.stop()
    assert r.ok and any("doublon" in w for w in r.warnings)


# ---------------------------------------------------------------- watchdog
class Alerts:
    def __init__(self):
        self.items = []

    async def __call__(self, iid, kind, text, prio):
        self.items.append((iid, kind, text, prio))


@pytest.mark.asyncio
async def test_watchdog_relance_limite_3_par_heure(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    inst = seed(stack, running=False)
    al = Alerts()
    wd = Watchdog(cfg, db, mgr, status, al)
    for _ in range(3):
        await wd.tick()
        assert mgr.is_running(inst)
        procs.running.clear()                              # le terminal « plante » de nouveau
    n = len(procs.launches)
    await wd.tick()
    assert len(procs.launches) == n                         # 4e tentative refusée
    assert [k for _, k, _, _ in al.items].count("LIMITE") == 1
    await wd.tick()
    assert [k for _, k, _, _ in al.items].count("LIMITE") == 1       # alerte une seule fois
    assert wd.can_restart(inst.id, time.time() + 3601)               # fenêtre glissante d'1 h


@pytest.mark.asyncio
async def test_watchdog_ignore_arrete_maintenance_creation(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    seed(stack, 1, "Demo-Server 1", running=False, state=ARRETE)
    seed(stack, 2, "Demo-Server 1", running=False, state=MAINTENANCE)
    seed(stack, 3, "Demo-Server 1", running=False, state=EN_CREATION)
    await Watchdog(cfg, db, mgr, status, Alerts()).tick()
    assert procs.launches == []                                      # arrêt volontaire : PAS de relance


@pytest.mark.asyncio
async def test_watchdog_dossier_disparu_alerte_sans_recreer(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    inst = seed(stack, running=False)
    shutil.rmtree(inst.folder)
    al = Alerts()
    wd = Watchdog(cfg, db, mgr, status, al)
    await wd.tick(); await wd.tick()
    assert [k for _, k, _, _ in al.items] == ["DOSSIER"] and not Path(inst.folder).exists() and procs.launches == []


@pytest.mark.asyncio
async def test_demarrage_relance_echelonnee_seulement_demarre(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    for i, st in enumerate((DEMARRE, DEMARRE, ARRETE, DEMARRE), start=1):
        seed(stack, i, "Demo-Server 1", running=False, state=st)
    seed(stack, 9, "Demo-Server 1", running=True)                    # déjà lancé : non relancé
    launched = await Watchdog(cfg, db, mgr, status, Alerts()).startup_launch()
    assert sorted(launched) == sorted(make_id(i, "Demo-Server 1") for i in (1, 2, 4))
    assert all(not ini for _, ini in procs.launches)                  # relances SANS /config


@pytest.mark.asyncio
async def test_watchdog_terminal_muet_alerte(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    inst = seed(stack)
    al = Alerts()
    wd = Watchdog(cfg, db, mgr, status, al)
    wd._muet_since[inst.id] = time.time() - 300                       # muet depuis 5 min, statut absent
    await wd.tick()
    assert any(k == "MUET" for _, k, _, _ in al.items)


# ---------------------------------------------------------------- gestion des terminaux
@pytest.mark.asyncio
async def test_arret_propre_puis_kill(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    inst = seed(stack)
    ea.start()
    try:
        assert await mgr.stop(inst) == "propre"                      # FERMER_TERMINAL obéi
        assert db.get(inst.id).desired_state == ARRETE and procs.kills == 0
    finally:
        await ea.stop()
    mgr.launch(inst)
    assert await mgr.stop(inst) == "kill"                             # EA muet : kill après le délai
    assert await mgr.stop(inst) == "deja_arrete"


@pytest.mark.asyncio
async def test_arret_attend_cloture_500(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    inst = seed(stack)
    ea.accounts[inst.id]["phase500"] = True
    ea.start()
    await asyncio.sleep(0.3)
    try:
        assert await mgr.stop(inst) == "attente_cloture"              # délai max atteint : rien d'arrêté
        assert mgr.is_running(inst)
        assert await mgr.stop(inst, force=True) in ("kill", "propre")   # arrêt forcé
        assert not mgr.is_running(inst)
    finally:
        await ea.stop()


# ---------------------------------------------------------------- déploiement
def publish(cfg, version: str, content: str | None = None):
    (cfg.paths.base / "version.txt").write_text(version)
    (cfg.paths.base / "MQL5" / "Experts" / cfg.mt5.ea_file).write_text(content or version)


@pytest.mark.asyncio
async def test_deploiement_un_compte_etat_cycle_conserves(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    inst = seed(stack, etat="ARRET_500", cycle=3)
    marker = Path(inst.folder) / "Profiles" / "marker.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("profil")
    publish(cfg, "5.1")
    plan = plan_deploy(cfg, db)
    assert plan.base_version == "5.1" and plan.targets == [inst.id]
    ea.start()
    await asyncio.sleep(0.3)
    try:
        steps = []

        async def prog(view):
            steps.append(dict(view))

        out = await DeployManager(cfg, db, mgr, status, cmdq).run([inst.id], prog)
    finally:
        await ea.stop()
    assert out[0].status == "ok", out[0].message
    assert (Path(inst.folder) / "MQL5" / "Experts" / "ScalpX_v5.ex5").read_text() == "5.1"
    assert db.get(inst.id).ea_version == "5.1" and db.get(inst.id).desired_state == DEMARRE
    assert ea.accounts[inst.id]["etat"] == "ARRET_500" and ea.accounts[inst.id]["cycle"] == 3
    assert marker.read_text() == "profil"                                              # profil intouché
    assert list((Path(inst.folder) / "ex5_backup").glob("*.bak"))                      # ancien .ex5 sauvegardé
    assert procs.launches[-1] == (inst.id, False) and mgr.is_running(inst)             # relance sans /config
    assert not ea.accounts[inst.id]["maintenance"]
    assert any(v[inst.id][0] == "⏳" for v in steps) and steps[-1][inst.id][0] == "✅"
    assert db.deployments(1)[0]["status"] == "OK"


@pytest.mark.asyncio
async def test_deploiement_position_ouverte_en_attente(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    inst = seed(stack)
    ea.accounts[inst.id]["positions"] = 2
    publish(cfg, "5.1")
    ea.start()
    try:
        out = await DeployManager(cfg, db, mgr, status, cmdq).run([inst.id])
    finally:
        await ea.stop()
    assert out[0].status == "waiting"
    assert (Path(inst.folder) / "MQL5" / "Experts" / "ScalpX_v5.ex5").read_text() == "5.0"     # rien touché
    assert not ea.accounts[inst.id]["maintenance"] and db.get(inst.id).desired_state == DEMARRE   # MAINTENANCE_OFF envoyé
    assert db.deployments(1)[0]["status"] == "PARTIEL"


@pytest.mark.asyncio
async def test_deploiement_ex5_defectueux_restaure_et_stoppe(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    cfg.timing.verify_deploy_s = 2
    a = seed(stack, 1, "Demo-Server 1")
    b = seed(stack, 2, "Demo-Server 1")
    publish(cfg, "5.1", "BAD-5.1")                                   # l'EA ne démarre pas (pas de heartbeat)
    ea.start()
    try:
        out = await DeployManager(cfg, db, mgr, status, cmdq).run([a.id, b.id])
    finally:
        await ea.stop()
    assert [o.status for o in out] == ["failed"]                      # le 2e compte n'est PAS déployé
    assert (Path(a.folder) / "MQL5" / "Experts" / "ScalpX_v5.ex5").read_text() == "5.0"      # ancien .ex5 restauré
    assert (Path(b.folder) / "MQL5" / "Experts" / "ScalpX_v5.ex5").read_text() == "5.0"
    assert mgr.is_running(a) and db.get(a.id).desired_state == DEMARRE and db.get(a.id).ea_version == "5.0"
    assert db.deployments(1)[0]["status"] == "ECHEC"


@pytest.mark.asyncio
async def test_deploiement_interrompu_reprise_et_annulation(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    a = seed(stack, 1, "Demo-Server 1", state=MAINTENANCE)            # interrompu : état souhaité MAINTENANCE
    publish(cfg, "5.1")
    dep = db.start_deployment("5.0", "5.1", [a.id])
    dm = DeployManager(cfg, db, mgr, status, cmdq)
    assert dm.interrupted() is not None
    ea.start()
    try:
        out = await dm.resume()
    finally:
        await ea.stop()
    assert out[0].status == "ok" and dm.interrupted() is None and db.get(a.id).desired_state == DEMARRE
    # annulation propre
    b = seed(stack, 2, "Demo-Server 1", state=MAINTENANCE)
    db.start_deployment("5.0", "5.1", [b.id])
    dm.cancel_interrupted()
    assert db.get(b.id).desired_state == DEMARRE and dm.interrupted() is None


def test_plan_deploiement_inputs_modifies(stack):
    cfg, db, *_ = stack
    inst = seed(stack, running=False)
    publish(cfg, "5.1")
    (cfg.paths.base / "MQL5" / "Presets" / "scalpx_v5.set").write_text("A=1\nB=2\nC=3\n")
    p = plan_deploy(cfg, db)
    assert p.inputs_changed and "C" in p.inputs_diff and p.targets == [inst.id]
    p2 = plan_deploy(cfg, db, selection=["inconnu"])
    assert p2.targets == []


# ---------------------------------------------------------------- reconnexion
@pytest.mark.asyncio
async def test_reconnexion_dossier_existant(stack, caplog):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    caplog.set_level(logging.DEBUG)
    inst = seed(stack, 321, "Demo-Server 1")
    ea.start()
    await asyncio.sleep(0.2)
    try:
        r = await InstanceCreator(cfg, db, mgr, status).reconnect(inst.id, PWD)
    finally:
        await ea.stop()
    assert r.ok, r.message
    assert db.get(inst.id).desired_state == DEMARRE
    assert not (Path(inst.folder) / INI_NAME).exists() and PWD not in caplog.text
    assert list(cfg.paths.archives.glob("*")) == []                   # dossier sain : pas d'archivage


@pytest.mark.asyncio
async def test_reconnexion_mauvais_mdp_conserve_dossier(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    cfg.timing.create_timeout_s = 2
    inst = seed(stack, 322, "Demo-Server 1")
    ea.password_ok = False
    ea.start()
    await asyncio.sleep(0.2)
    try:
        r = await InstanceCreator(cfg, db, mgr, status).reconnect(inst.id, PWD)
    finally:
        await ea.stop()
    assert not r.ok and "connecté" in r.message
    assert Path(inst.folder).exists() and db.get(inst.id) is not None and not procs.running


@pytest.mark.asyncio
async def test_reconnexion_dossier_inutilisable_archive_et_recopie(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    cfg.timing.create_timeout_s = 2
    inst = seed(stack, 323, "Demo-Server 1", running=False)
    (Path(inst.folder) / "MQL5" / "Experts" / "ScalpX_v5.ex5").write_text("BAD-corrompu")   # EA qui ne démarre pas
    ea.start()
    try:
        r = await InstanceCreator(cfg, db, mgr, status).reconnect(inst.id, PWD)
    finally:
        await ea.stop()
    assert r.ok, r.message
    assert len(list(cfg.paths.archives.glob("*AVANT_RECONNEXION"))) == 1
    assert (Path(inst.folder) / "MQL5" / "Experts" / "ScalpX_v5.ex5").read_text() == "5.0"   # recopié depuis BASE


def test_profil_vide_et_ini_profil(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    inst = seed(stack, 4242, "Demo-Server 1", running=False)
    d = Path(inst.folder) / "Profiles" / "Charts" / cfg.mt5.profile
    d.mkdir(parents=True, exist_ok=True)
    (d / "chart01.chr").write_text("vieux graphique")                       # un graphique sauvegardé ferait un doublon
    mgr.launch(inst)
    assert d.is_dir() and list(d.glob("*.chr")) == []                       # profil vide recréé à chaque lancement
    assert f"ProfileLast={cfg.mt5.profile}" in procs.run_inis[-1] and "Password" not in procs.run_inis[-1]
    from mt5.ini import build_ini
    assert "ProfileLast=ScalpX" in build_ini(1, "pw", "S", "E", "s.set", "XAUUSD", "M5", "ScalpX")
    assert "[Charts]" not in build_ini(1, "pw", "S", "E", "s.set", "XAUUSD", "M5")


def test_recreation_d_un_compte_supprime(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    inst = seed(stack, 777, "Demo-Server 1", running=False)
    db.archive(inst.id)                                          # « Supprimer » : ligne ARCHIVE conservée (historique)
    assert not db.exists_active(inst.id)
    db.add_instance(Instance(inst.id, 777, "Demo-Server 1", "Neuf", str(inst.folder), 123, "", "EN_CREATION"))   # plus d'IntegrityError
    n = db.get(inst.id)
    assert n.desired_state == "EN_CREATION" and n.alias == "Neuf" and db.exists_active(inst.id)
    assert [i.id for i in db.list()].count(inst.id) == 1


# ---------------------------------------------------------------- nouvelle compilation dans BASE
def test_recompilation_meme_version_est_a_deployer(stack):
    cfg, db, *_ = stack
    inst = seed(stack, running=False)
    (cfg.paths.base / "MQL5" / "Experts" / cfg.mt5.ea_file).write_text("5.0-recompile")   # version.txt inchangée
    assert plan_deploy(cfg, db).targets == [inst.id]


def test_version_lue_dans_sx_core(stack):
    from mt5 import base as basemod
    cfg, *_ = stack
    (cfg.paths.base / "MQL5" / "Experts" / "SX_Core.mqh").write_bytes('﻿#define SX_VERSION   "5.3"\n'.encode("utf-8"))
    assert basemod.read_version(cfg.paths.base) == "5.3"


def test_detection_nouvelle_compilation_une_seule_fois(stack):
    import os, time as _t
    from mt5.deploy import detect_new_build
    cfg, db, *_ = stack
    inst = seed(stack, running=False)
    assert detect_new_build(cfg, db) is None                         # BASE identique aux comptes : rien à proposer
    ex5 = cfg.paths.base / "MQL5" / "Experts" / cfg.mt5.ea_file
    ex5.write_text("5.1")
    assert detect_new_build(cfg, db) is None                         # compilation toute fraîche : on attend qu'elle se stabilise
    old = _t.time() - 60
    os.utime(ex5, (old, old))
    nb = detect_new_build(cfg, db)
    assert nb is not None and nb.plan.targets == [inst.id]
    assert detect_new_build(cfg, db) is None                         # proposée une seule fois
    ex5.write_text("5.2")
    os.utime(ex5, (old + 1, old + 1))
    assert detect_new_build(cfg, db) is not None                     # nouvelle compilation → nouvelle proposition


def test_verrou_remplace_l_ancien_bot():
    import socket, threading, time as _t
    import main as m
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    old = m.acquire_lock(port)
    ql = m.QuitListener(old)

    def stopper():                                                   # l'ancien bot s'arrête quand on le lui demande
        ql.requested.wait(10)
        try:
            old.shutdown(socket.SHUT_RDWR)                           # réveille accept() (comme la fin du processus)
        except OSError:
            pass
        old.close()

    threading.Thread(target=stopper, daemon=True).start()
    t0 = _t.monotonic()
    new = m.acquire_lock(port, wait_s=10)
    assert new is not None and ql.requested.is_set() and _t.monotonic() - t0 < 8
    new.close()
