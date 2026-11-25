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
async def test_creation_refus_detecte_tot_puis_nouvel_essai(stack, caplog):
    """Mot de passe refusé : détecté après refused_s (pas le délai complet), dossier gardé, nouvel essai sans recopie."""
    cfg, db, procs, status, cmdq, mgr, ea = stack
    cfg.timing.create_timeout_s = 30
    cfg.timing.refused_s = 1
    ea.password_ok = False
    ea.start()
    cr = InstanceCreator(cfg, db, mgr, status)
    iid = make_id(999, "Demo-Server 1")
    try:
        t0 = time.monotonic()
        r = await cr.create("999", PWD, "Demo-Server 1", "A", lambda t: _aw(), keep_on_refusal=True)
        assert time.monotonic() - t0 < 10                                             # n'attend pas les 30 s
        assert not r.ok and r.refused and r.iid == iid and "connecté" in r.message
        inst = db.get(iid)
        assert inst is not None and inst.desired_state == "EN_CREATION"               # gardé pour le nouvel essai
        assert (cfg.paths.instances / iid).is_dir() and not (cfg.paths.instances / iid / INI_NAME).exists()
        assert not procs.running and json.loads(db.meta_get("creations_refusees")) == [iid]
        marker = cfg.paths.instances / iid / "marqueur.txt"
        marker.write_text("x")                                                        # le dossier n'est pas recopié
        r2 = await cr.retry(iid, PWD + "x", lambda t: _aw())                          # encore faux
        assert r2.refused and db.get(iid) is not None
        ea.password_ok = True
        r3 = await cr.retry(iid, PWD, lambda t: _aw())
        assert r3.ok, r3.message
        assert marker.exists() and db.get(iid).desired_state == "DEMARRE"
        assert json.loads(db.meta_get("creations_refusees")) == []
        assert list(cfg.paths.archives.iterdir()) == [] if cfg.paths.archives.exists() else True
    finally:
        await ea.stop()
    assert PWD not in caplog.text


@pytest.mark.asyncio
async def test_creation_refusee_abandon_et_nettoyage_au_demarrage(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    cfg.timing.create_timeout_s = 30
    cfg.timing.refused_s = 1
    ea.password_ok = False
    ea.start()
    cr = InstanceCreator(cfg, db, mgr, status)
    try:
        r = await cr.create("111", PWD, "Demo-Server 1", "", lambda t: _aw(), keep_on_refusal=True)
        r2 = await cr.create("222", PWD, "Demo-Server 1", "", lambda t: _aw(), keep_on_refusal=True)
    finally:
        await ea.stop()
    assert r.refused and r2.refused
    await cr.abandon(r.iid)
    assert db.get(r.iid) is None and not (cfg.paths.instances / r.iid).exists()
    assert any("ECHEC" in a.name for a in cfg.paths.archives.iterdir())
    assert await cr.cleanup_refused() == 1                                            # bot redémarré : l'autre est abandonnée
    assert db.get(r2.iid) is None and db.list(include_archived=True) == []
    assert json.loads(db.meta_get("creations_refusees")) == []
    r3 = await cr.retry(r2.iid, PWD, lambda t: _aw())
    assert not r3.ok and "introuvable" in r3.message


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


@pytest.mark.asyncio
async def test_watchdog_muet_relance_forcee_puis_limite(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    inst = seed(stack)
    al = Alerts()
    wd = Watchdog(cfg, db, mgr, status, al)
    wd._muet_since[inst.id] = time.time() - 100                       # muet depuis 100 s : alerte pas encore, relance non plus
    await wd.tick()
    assert procs.kills == 0 and not any(k in ("MUET", "RELANCE") for _, k, _, _ in al.items)
    for n in range(1, 4):                                              # muet depuis 6 min : terminal tué puis relancé
        wd._muet_since[inst.id] = time.time() - 360
        await wd.tick()
        assert procs.kills == n and mgr.is_running(inst)
        assert db.get(inst.id).desired_state == DEMARRE
    assert [k for _, k, _, _ in al.items].count("RELANCE") == 3
    wd._muet_since[inst.id] = time.time() - 360
    await wd.tick(); await wd.tick()                                   # 4e : limite de 3 relances par heure
    assert procs.kills == 3
    assert [k for _, k, _, _ in al.items].count("LIMITE") == 1         # signalée une seule fois


@pytest.mark.asyncio
async def test_watchdog_muet_relance_desactivable(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    cfg.timing.muet_restart_s = 0
    inst = seed(stack)
    wd = Watchdog(cfg, db, mgr, status, Alerts())
    wd._muet_since[inst.id] = time.time() - 3600
    await wd.tick()
    assert procs.kills == 0 and mgr.is_running(inst)


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
    # chaque étape est visible en direct, le résultat dit quand le compte est revenu ; trace dans le journal des actions
    texts = [v[inst.id][1] for v in steps]
    for key in ("mise à jour immédiate", "sauvegardé", "arrêt du terminal", "terminal fermé", "copié", "terminal relancé"):
        assert any(key in t for t in texts), (key, texts)
    assert "en ligne, connecté" in out[0].message and "terminal revenu en" in out[0].message
    assert "en ligne" in steps[-1][inst.id][1]
    acts = db.actions_recent(5)
    assert acts and acts[0]["text"] == "DÉPLOIEMENT EA v5.1" and acts[0]["ok"] == 1


@pytest.mark.asyncio
async def test_deploiement_immediat_sans_attendre_la_fin_de_la_serie(stack):
    """Par défaut (deploy_wait_flat = false) : série ouverte, mise à jour tout de suite ; positions laissées chez le broker."""
    cfg, db, procs, status, cmdq, mgr, ea = stack
    inst = seed(stack)
    ea.accounts[inst.id]["positions"] = 2
    publish(cfg, "5.1")
    ea.start()
    try:
        for _ in range(100):                                   # premier statut de l'EA (positions connues)
            if status.read(inst.id) is not None:
                break
            await asyncio.sleep(0.05)
        dm = DeployManager(cfg, db, mgr, status, cmdq)
        steps = []

        async def progress(view):
            steps.extend(t for _, t in view.values())
        out = await dm.run([inst.id], progress)
    finally:
        await ea.stop()
    assert out[0].status == "ok"
    assert (Path(inst.folder) / "MQL5" / "Experts" / "ScalpX_v5.ex5").read_text() == "5.1"
    assert db.get(inst.id).desired_state == DEMARRE
    assert any("mise à jour immédiate : 2 position(s)" in t for t in steps) and not any("attente de la fin" in t for t in steps)


@pytest.mark.asyncio
async def test_deploiement_position_ouverte_en_attente(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    cfg.timing.deploy_wait_flat = True                         # option : attendre la fin de la série
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
async def test_deploiement_ex5_defectueux_laisse_le_nouvel_ea(stack):
    """Bot 3.10.2 : plus de retour à l'ancien EA (demandé) : nouvel EA laissé en place, compte « démarré », à redémarrer à la main."""
    cfg, db, procs, status, cmdq, mgr, ea = stack
    cfg.timing.wait_online_limit_s = 2
    a = seed(stack, 1, "Demo-Server 1")
    b = seed(stack, 2, "Demo-Server 1")
    publish(cfg, "5.1", "BAD-5.1")                                   # l'EA ne démarre pas (pas de heartbeat)
    ea.start()
    try:
        out = await DeployManager(cfg, db, mgr, status, cmdq).run([a.id, b.id])
    finally:
        await ea.stop()
    assert [o.status for o in out] == ["failed", "failed"]
    for i in (a, b):
        assert (Path(i.folder) / "MQL5" / "Experts" / "ScalpX_v5.ex5").read_text() == "BAD-5.1"   # pas de restauration
        assert mgr.is_running(i) and db.get(i.id).desired_state == DEMARRE
    assert all("nouvel EA laissé en place" in o.message for o in out)
    assert db.deployments(1)[0]["status"] == "ECHEC"


@pytest.mark.asyncio
async def test_deploiement_groupe_ferme_tout_puis_relance(stack):
    """Bot 3.7.0 : plusieurs comptes → tous les terminaux fermés AVANT la première relance, puis nouvel EA partout."""
    cfg, db, procs, status, cmdq, mgr, ea = stack
    cfg.timing.launch_stagger_s = 0
    cfg.timing.launch_group = 2
    insts = [seed(stack, k, "Demo-Server 1") for k in (1, 2, 3)]
    ea.accounts[insts[1].id]["positions"] = 2
    publish(cfg, "5.1")
    ea.start()
    await asyncio.sleep(0.3)
    order = []
    real_launch, real_stop = mgr.launch, mgr.stop

    def launch(inst, ini=None):
        prev = [e[1] for e in order if e[0] == "launch"]
        waiting = [iid for iid in prev if status.read(iid) is None]   # Bot 3.14.1 : au plus 2 terminaux en chargement
        assert len(waiting) < 2, "plus de 2 terminaux en chargement en même temps"
        order.append(("launch", inst.id))
        return real_launch(inst, ini)

    async def stop(inst, force=False, set_desired=None):
        order.append(("stop", inst.id))
        r = await real_stop(inst, force=force, set_desired=set_desired)
        order.append(("stopped", inst.id))
        return r
    mgr.launch, mgr.stop = launch, stop
    try:
        steps = []

        async def prog(view):
            steps.append(dict(view))
        out = await DeployManager(cfg, db, mgr, status, cmdq).run([i.id for i in insts], prog)
    finally:
        await ea.stop()
    assert [o.status for o in out] == ["ok"] * 3, [o.message for o in out]
    first_launch = next(k for k, e in enumerate(order) if e[0] == "launch")
    assert all(e[0] != "stopped" for e in order[first_launch:])               # aucune fermeture après la 1re relance
    assert sum(1 for e in order if e[0] == "stopped") == 3
    for i in insts:
        assert (Path(i.folder) / "MQL5" / "Experts" / "ScalpX_v5.ex5").read_text() == "5.1"
        assert db.get(i.id).ea_version == "5.1" and db.get(i.id).desired_state == DEMARRE and mgr.is_running(i)
    assert all("groupe terminé" in o.message for o in out)
    assert any("fermeture de tous les terminaux (3)" in t for v in steps for _, t in v.values())
    assert db.deployments(1)[0]["status"] == "OK" and len(db.actions_recent(5)) >= 3


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
    assert p.inputs_changed and p.inputs_diff == "C" and p.targets == [inst.id] and "tes réglages actuels ne changent pas" in p.inputs_note
    (cfg.paths.base / "MQL5" / "Presets" / "scalpx_v5.set").write_text("A=1\n")              # inputs supprimés : rien à signaler
    assert not plan_deploy(cfg, db).inputs_changed and plan_deploy(cfg, db).inputs_note == ""
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


@pytest.mark.asyncio
async def test_deploiement_annule_en_retirant_la_maintenance(stack):
    """Série ouverte : pendant l'attente, 🔧 retirer la maintenance annule la mise à jour ; le compte repart, les suivants ne sont pas faits."""
    cfg, db, procs, status, cmdq, mgr, ea = stack
    cfg.timing.deploy_wait_flat = True
    a = seed(stack, login=71)
    b = seed(stack, login=72)
    ea.accounts.setdefault(a.id, {})["positions"] = 2
    publish(cfg, "5.1")
    ea.start()
    await asyncio.sleep(0.3)
    dm = DeployManager(cfg, db, mgr, status, cmdq)
    try:
        task = asyncio.create_task(dm.run([a.id, b.id]))
        for _ in range(100):
            await asyncio.sleep(0.05)
            if dm.phase == "attente":
                break
        assert dm.current_iid == a.id and not dm.abort(b.id)            # seulement le compte en cours
        assert dm.abort(a.id)
        out = await asyncio.wait_for(task, 20)
    finally:
        await ea.stop()
    assert out[0].status == "waiting" and "annulé" in out[0].message and len(out) == 1
    assert db.get(a.id).desired_state == DEMARRE and not ea.accounts[a.id]["maintenance"]
    assert (Path(a.folder) / "MQL5" / "Experts" / "ScalpX_v5.ex5").read_text() != "5.1"   # ancien EA gardé
    assert not dm.running and dm.current_iid == "" and not dm.abort(a.id)


# ---------------------------------------------------------------- Bot 3.7.2 : libération du CPU avant redémarrage
def test_attente_sans_limite_par_defaut():
    from core.config import Timing
    assert Timing().wait_online_limit_s == 0                       # Bot 3.11.0 : le temps qu'il faut


@pytest.mark.asyncio
async def test_attente_abandonnee_si_terminal_ferme():
    from mt5.manager import Gone

    class M:
        up = True

        def is_running(self, inst):
            return self.up
    m = M()
    g = Gone(m, object(), grace_s=0.05, every_s=0.0)
    assert not await g.check()
    m.up = False
    assert not await g.check()                                     # fermé depuis moins de grace_s
    await asyncio.sleep(0.06)
    assert await g.check()


def test_reglages_par_defaut_fermeture_et_lancement():
    from core.config import Timing
    t = Timing()
    assert t.kill_after_s == 10 and t.launch_group == 2              # Bot 3.12.0


@pytest.mark.asyncio
async def test_file_glissante_place_liberee_des_ea_charge():
    """Bot 3.14.1 : 2 places ; un terminal très lent (jamais chargé) ne bloque pas les suivants au-delà de free_after_s."""
    from mt5.manager import Slots
    slots = Slots(2, free_after_s=0.05)
    a, b = await slots.take(), await slots.take()
    a.check(True)                                                  # EA de a chargé : une place libre
    c = await asyncio.wait_for(slots.take(), 0.5)
    await asyncio.sleep(0.06)
    b.check(False)                                                 # b toujours pas chargé, mais délai dépassé
    d = await asyncio.wait_for(slots.take(), 0.5)
    for s in (a, b, c, d):
        s.free()
        s.free()                                                   # libérer deux fois : sans effet
    assert slots.sem._value == 2


class _St:
    def __init__(self, age, connecte=True, hb=0, at=None):
        self.tick_age_s, self.connecte, self.ts = age, connecte, int(at if at is not None else time.time()) - hb
        self.symbole, self.serveur = "XAUUSD", "Demo-Server 1"

    def age_s(self, now=None):
        return (now if now is not None else time.time()) - self.ts


def _fake_restart(monkeypatch, mgr):
    """Relances « sans prix » enregistrées (valeur de force) sans attendre un vrai terminal."""
    calls = []

    async def fake(inst, force=False):
        calls.append(force)
        return "relance"
    monkeypatch.setattr(mgr, "restart", fake)
    return calls


LIVE = time.time() - 3600                                              # flux vivant depuis 1 h


@pytest.mark.asyncio
async def test_watchdog_sans_prix_relance_puis_confirme(stack, monkeypatch):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    rs = _fake_restart(monkeypatch, mgr)
    inst = seed(stack)
    al = Alerts()
    wd = Watchdog(cfg, db, mgr, status, al)
    now = time.time()
    assert not await wd._no_price(inst, _St(600, at=now), False, now, LIVE)    # aucun autre compte du même flux n'a de prix : rien
    assert not await wd._no_price(inst, _St(200, at=now), True, now, LIVE)     # sous 5 min : rien
    assert not await wd._no_price(inst, _St(600, False, at=now), True, now, LIVE)   # déconnecté : autre problème
    assert not await wd._no_price(inst, _St(None, at=now), True, now, LIVE)    # ancien EA (pas de tick_age_s)
    assert not await wd._no_price(inst, _St(600, hb=120, at=now), True, now, LIVE)  # statut ancien (EA arrêté / planté) : ignoré
    assert len(rs) == 0
    assert await wd._no_price(inst, _St(3243, at=now), True, now, LIVE)        # cas du 24/11 : relance forcée
    assert len(rs) == 1 and rs[-1] is False
    assert [k for _, k, _, _ in al.items] == ["SANS_PRIX"]
    assert not await wd._no_price(inst, _St(3243, at=now + 10), True, now + 10, LIVE)    # délai de grâce après la relance
    assert not await wd._no_price(inst, _St(3, at=now + 400), True, now + 400, LIVE)      # prix revenus : confirmation
    assert [k for _, k, _, _ in al.items] == ["SANS_PRIX", "RETABLI"]
    assert not await wd._no_price(inst, _St(3, at=now + 420), True, now + 420, LIVE)      # confirmée une seule fois
    assert len(al.items) == 2


@pytest.mark.asyncio
async def test_watchdog_sans_prix_limite_collante_et_desactivable(stack, monkeypatch):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    rs = _fake_restart(monkeypatch, mgr)
    inst = seed(stack)
    al = Alerts()
    wd = Watchdog(cfg, db, mgr, status, al)
    now = time.time()
    for k in range(3):
        assert await wd._no_price(inst, _St(900, at=now + k * 400), True, now + k * 400, LIVE)
    assert not await wd._no_price(inst, _St(900, at=now + 1300), True, now + 1300, LIVE)   # 4e en moins d'1 h : refusée, signalée
    assert not await wd._no_price(inst, _St(900, at=now + 1700), True, now + 1700, LIVE)
    assert not await wd._no_price(inst, _St(900, at=now + 9000), True, now + 9000, LIVE)   # heures suivantes : toujours pas de relance
    assert len(rs) == 3 and [k for _, k, _, _ in al.items].count("LIMITE") == 1
    assert not await wd._no_price(inst, _St(2, at=now + 9100), True, now + 9100, LIVE)     # prix revenus : limite levée, confirmé
    assert al.items[-1][1] == "RETABLI"
    cfg.timing.sans_prix_restart_s = 0
    wd2 = Watchdog(cfg, db, mgr, status, Alerts())
    assert not await wd2._no_price(inst, _St(9999, at=now), True, now, LIVE)


@pytest.mark.asyncio
async def test_watchdog_sans_prix_pas_de_relance_a_la_reouverture(stack, monkeypatch):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    rs = _fake_restart(monkeypatch, mgr)
    inst = seed(stack)
    wd = Watchdog(cfg, db, mgr, status, Alerts())
    now = time.time()
    assert not await wd._no_price(inst, _St(172800, at=now), False, now, None)       # week-end : aucun prix sur ce flux
    assert not await wd._no_price(inst, _St(172800, at=now), True, now, now - 20)    # lundi : 1ers prix ailleurs il y a 20 s : PAS relancé
    assert len(rs) == 0
    assert await wd._no_price(inst, _St(172800, at=now + 400), True, now + 400, now - 20)  # flux vivant depuis 7 min, toujours rien : relancé
    await wd.tick()                                                    # passage réel sans aucun statut : aucun flux vivant
    assert wd._live_since == {}


@pytest.mark.asyncio
async def test_watchdog_sans_prix_passages_reels(stack, monkeypatch):
    """tick() complet : référence = même symbole / même serveur, statuts anciens ignorés, flux vivant depuis 5 min exigé."""
    import mt5.watchdog as wmod
    from core.health import Health
    cfg, db, procs, status, cmdq, mgr, ea = stack
    rs = _fake_restart(monkeypatch, mgr)
    a, b = seed(stack, 1), seed(stack, 2)
    monkeypatch.setattr(wmod, "diagnose", lambda *x, **k: Health("ok", "OK", []))
    wd = Watchdog(cfg, db, mgr, status, Alerts())
    t0 = time.time()
    sts = {}
    monkeypatch.setattr(wd.status, "read", lambda iid: sts.get(iid))
    clock = [t0]
    monkeypatch.setattr(wmod.time, "time", lambda: clock[0])

    def run(at, sa, sb):
        clock[0] = at
        sts[a.id], sts[b.id] = sa, sb
        return wd.tick()

    # le compte A reçoit des prix mais SON statut est ancien (terminal planté) : ne prouve rien → B jamais relancé
    for k in range(5):
        await run(t0 + k * 200, _St(2, hb=120, at=t0 + k * 200), _St(5000, at=t0 + k * 200))
    assert len(rs) == 0
    # A sur un autre symbole : pas une référence pour B
    for k in range(5):
        at = t0 + 2000 + k * 200
        sa = _St(2, at=at); sa.symbole = "EURUSD"
        await run(at, sa, _St(5000, at=at))
    assert len(rs) == 0
    # A reçoit des prix sur le même flux : B relancé seulement après 5 min de flux vivant
    t1 = t0 + 5000
    await run(t1, _St(2, at=t1), _St(5000, at=t1))
    await run(t1 + 200, _St(2, at=t1 + 200), _St(5000, at=t1 + 200))
    assert len(rs) == 0
    await run(t1 + 320, _St(2, at=t1 + 320), _St(5000, at=t1 + 320))
    assert len(rs) == 1


def test_journal_joint_a_l_alerte(tmp_path):
    """Bot 4.14.4 : fin des journaux du jour (EA en UTF-16 avec accents, terminal) jointe à l'alerte, HTML échappé."""
    from mt5.watchdog import journal_tail
    (tmp_path / "MQL5" / "logs").mkdir(parents=True)
    (tmp_path / "logs").mkdir()
    ea_lines = "\r\n".join(f"RK 0 12:0{i}:00 ScalpX_v5 (XAUUSD,M5) Réglages <{i}>" for i in range(9))
    (tmp_path / "MQL5" / "logs" / "20261124.log").write_bytes(b"\xff\xfe" + ea_lines.encode("utf-16-le"))
    (tmp_path / "logs" / "20261124.log").write_bytes("CE 0 09:40 Trades: deal sell".encode("utf-16-le"))
    (tmp_path / "logs" / "metaeditor.log").write_text("ignoré")
    t = journal_tail(str(tmp_path))
    assert "— EA —" in t and "Réglages <8>" in t and "Réglages <2>" not in t      # 6 dernières lignes, texte brut
    assert "— Terminal —" in t and "deal sell" in t and "ignoré" not in t
    assert journal_tail(str(tmp_path / "absent")) == ""


class _P:
    """Statut minimal pour le garage à 500."""
    def __init__(self, etat="ARRET_500", solde=500.36, positions=0, ordres=0, ts=None):
        self.etat, self.solde, self.positions, self.ordres = etat, solde, positions, ordres
        self.phase_cloture500, self.seuil500_actif, self.seuil_arret, self.seuil_reprise = False, True, 500.0, 200.0
        self.devise, self.ts = "EUR", int(ts if ts is not None else time.time())

    def age_s(self, now=None):
        return (now if now is not None else time.time()) - self.ts


@pytest.mark.asyncio
async def test_garage_a_500_puis_relance_apres_retrait(stack, monkeypatch):
    """Bot 4.15.0 : 500 atteint et tout fermé → terminal arrêté (CPU) ; vérification périodique ; retrait → relancé, confirmé."""
    from storage.db import ARRETE
    cfg, db, procs, status, cmdq, mgr, ea = stack
    inst = seed(stack)
    al = Alerts()
    wd = Watchdog(cfg, db, mgr, status, al)
    sts = {}
    monkeypatch.setattr(wd.status, "read", lambda iid: sts.get(iid))
    run = {inst.id: True}
    monkeypatch.setattr(mgr, "is_running", lambda i: run.get(i.id, False))
    stops, starts = [], []

    async def stop(i, force=False, set_desired=ARRETE):
        stops.append(i.id); run[i.id] = False; db.set_desired(i.id, set_desired); return "propre"

    def start(i, set_desired=True):
        starts.append(i.id); run[i.id] = True; db.set_desired(i.id, DEMARRE); return True
    monkeypatch.setattr(mgr, "stop", stop)
    monkeypatch.setattr(mgr, "start", start)
    t0 = time.time()
    sts[inst.id] = _P(positions=1, ts=t0)                         # position encore ouverte : rien
    await wd.park_tick(t0)
    await wd.park_tick(t0 + 500)
    assert not stops
    sts[inst.id] = _P(ts=t0 + 500)                                 # tout fermé : délai de stabilité
    await wd.park_tick(t0 + 500)
    sts[inst.id] = _P(ts=t0 + 560)
    await wd.park_tick(t0 + 560)
    assert not stops
    sts[inst.id] = _P(ts=t0 + 630)
    await wd.park_tick(t0 + 630)                                   # 130 s : garé
    assert stops == [inst.id] and wd.parked(inst.id) and [k for _, k, _, _ in al.items] == ["PARK"]
    await wd.park_tick(t0 + 1000)                                  # avant l'heure : pas de vérification
    assert not starts
    await wd.park_tick(t0 + 630 + 3600)                            # vérification horaire : relancé
    assert starts == [inst.id]
    await wd.park_tick(t0 + 4240)                                  # ancien statut (ts avant la relance) : ignoré
    sts[inst.id] = _P(ts=t0 + 4250)
    await wd.park_tick(t0 + 4250)
    sts[inst.id] = _P(ts=t0 + 4290)
    await wd.park_tick(t0 + 4290)                                  # toujours à 500 : regaré, sans nouveau message
    assert stops == [inst.id, inst.id] and wd.parked(inst.id) and len(al.items) == 1
    assert wd.check_parked_now() == 1                              # bouton « J'ai retiré »
    await wd.park_tick(t0 + 4300)
    assert len(starts) == 2
    sts[inst.id] = _P(etat="ACTIF", solde=200.0, ts=t0 + 4320)     # l'EA a constaté le retrait
    await wd.park_tick(t0 + 4320)
    assert not wd.parked(inst.id) and al.items[-1][1] == "UNPARK" and len(stops) == 2


@pytest.mark.asyncio
async def test_garage_trop_retire_et_desactivable(stack, monkeypatch):
    from storage.db import ARRETE
    cfg, db, procs, status, cmdq, mgr, ea = stack
    inst = seed(stack)
    al = Alerts()
    wd = Watchdog(cfg, db, mgr, status, al)
    sts = {}
    monkeypatch.setattr(wd.status, "read", lambda iid: sts.get(iid))
    run = {inst.id: False}
    monkeypatch.setattr(mgr, "is_running", lambda i: run.get(i.id, False))
    stops = []

    async def stop(i, force=False, set_desired=ARRETE):
        stops.append(i.id); run[i.id] = False; db.set_desired(i.id, set_desired); return "propre"

    def start(i, set_desired=True):
        run[i.id] = True; db.set_desired(i.id, DEMARRE); return True
    monkeypatch.setattr(mgr, "stop", stop)
    monkeypatch.setattr(mgr, "start", start)
    db.set_desired(inst.id, ARRETE)
    db.meta_set("park:" + inst.id, "1")
    t0 = time.time()
    await wd.park_tick(t0)                                         # garé depuis longtemps : vérification
    assert run[inst.id]
    sts[inst.id] = _P(solde=150.0, ts=t0 + 28)                     # tout retiré : sous le minimum
    await wd.park_tick(t0 + 30)
    assert not stops
    sts[inst.id] = _P(solde=150.0, ts=t0 + 79)
    await wd.park_tick(t0 + 80)                                    # 1 min après la relance : regaré, signalé une fois
    assert stops == [inst.id] and wd.parked(inst.id) and "sous le minimum" in al.items[-1][2]
    cfg.timing.park_at_500 = False
    wd2 = Watchdog(cfg, db, mgr, status, Alerts())
    run[inst.id] = False
    await wd2.park_tick(t0 + 99999)
    assert not run[inst.id]


@pytest.mark.asyncio
async def test_garage_verifications_une_a_la_fois_sauf_bouton(stack, monkeypatch):
    """Bot 4.15.2 : vérification toutes les 15 min, un seul terminal garé relancé à la fois ; le bouton les relance tous."""
    from storage.db import ARRETE
    cfg, db, procs, status, cmdq, mgr, ea = stack
    a, b = seed(stack, 1), seed(stack, 2)
    assert cfg.timing.park_probe_s == 900
    wd = Watchdog(cfg, db, mgr, status, Alerts())
    monkeypatch.setattr(wd.status, "read", lambda iid: None)
    run = {a.id: False, b.id: False}
    monkeypatch.setattr(mgr, "is_running", lambda i: run.get(i.id, False))
    starts = []

    def start(i, set_desired=True):
        starts.append(i.id); run[i.id] = True; db.set_desired(i.id, DEMARRE); return True
    monkeypatch.setattr(mgr, "start", start)
    t0 = time.time()
    for i in (a, b):
        db.set_desired(i.id, ARRETE)
        db.meta_set("park:" + i.id, "1")
        db.meta_set("parkchk:" + i.id, str(int(t0)))
    await wd.park_tick(t0 + 600)
    assert not starts                                              # moins de 15 min
    await wd.park_tick(t0 + 910)
    assert len(starts) == 1                                        # un seul à la fois
    await wd.park_tick(t0 + 930)
    assert len(starts) == 1                                        # l'autre attend la fin de la vérification en cours
    run[starts[0]] = False
    db.set_desired(starts[0], ARRETE)
    await wd.park_tick(t0 + 1000)
    assert len(starts) == 2
    for i in (a, b):                                               # bouton « J'ai retiré » : tous tout de suite
        run[i.id] = False
        db.set_desired(i.id, ARRETE)
    wd.check_parked_now()
    await wd.park_tick(t0 + 1010)
    assert len(starts) == 4
