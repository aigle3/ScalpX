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
    insts = [seed(stack, k, "Demo-Server 1") for k in (1, 2, 3)]
    ea.accounts[insts[1].id]["positions"] = 2
    publish(cfg, "5.1")
    ea.start()
    await asyncio.sleep(0.3)
    order = []
    real_launch, real_stop = mgr.launch, mgr.stop

    def launch(inst, ini=None):
        prev = [e[1] for e in order if e[0] == "launch"]
        if prev:                                                   # Bot 3.8.1 : le précédent est revenu avant
            st = status.read(prev[-1])
            assert st is not None and st.connecte, "terminal suivant relancé avant le retour du précédent"
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
@pytest.mark.asyncio
async def test_liberation_cpu_ferme_un_par_un_jusqu_au_seuil(stack):
    from mt5 import cpufree
    cfg, db, procs, status, cmdq, mgr, ea = stack
    insts = [seed(stack, k, "Demo-Server 1") for k in (1, 2, 3, 4)]
    load = {"v": 95.0}

    async def fake_stop(inst, force=False, set_desired=None):
        procs.kill(Path(inst.folder) / "terminal64.exe")
        load["v"] -= 30.0                                     # chaque terminal fermé libère 30 % de CPU
        return "kill"
    mgr.stop = fake_stop
    steps = []

    async def step(t):
        steps.append(t)
    closed = await cpufree.free_cpu(db, mgr, cpu_fn=lambda: load["v"], target_free=70, wait_s=0.05, sample_s=0.01, step=step)
    assert len(closed) == 3                                   # 95 → 65 → 35 → 5 % : 3 fermetures suffisent
    assert all(db.get(i).desired_state == MAINTENANCE for i in closed)
    left = [i for i in insts if i.id not in closed]
    assert len(left) == 1 and db.get(left[0].id).desired_state == DEMARRE and mgr.is_running(left[0])
    assert any("redémarrage du bot" in t for t in steps)
    back = cpufree.restore_after_restart(db)                  # nouveau bot : remis en DEMARRE, relancés
    assert sorted(back) == sorted(closed) and all(db.get(i).desired_state == DEMARRE for i in closed)
    assert cpufree.restore_after_restart(db) == []


@pytest.mark.asyncio
async def test_liberation_cpu_rien_a_fermer(stack):
    from mt5 import cpufree
    cfg, db, procs, status, cmdq, mgr, ea = stack
    seed(stack)
    assert await cpufree.free_cpu(db, mgr, cpu_fn=lambda: 20.0, target_free=70, wait_s=0.05, sample_s=0.01) == []


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


def test_liste_des_terminaux_arretes_pour_la_mise_a_jour(stack):
    """Bot 3.11.0 : arrêtés (ARRETE) pour la mise à jour du bot : restent arrêtés au démarrage, liste gardée jusqu'à la relance."""
    from mt5 import cpufree
    from storage.db import ARRETE
    cfg, db, procs, status, cmdq, mgr, ea = stack
    a = seed(stack, 1, "Demo-Server 1")
    db.set_desired(a.id, ARRETE)
    cpufree.remember(db, [a.id])
    assert cpufree.restore_after_restart(db, clear_list=False) == [a.id]
    assert db.get(a.id).desired_state == ARRETE
    assert cpufree.restore_after_restart(db, clear_list=False) == [a.id]     # toujours là (relance pas finie)
    cpufree.clear(db)
    assert cpufree.restore_after_restart(db) == []
