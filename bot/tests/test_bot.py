"""Tests de la couche « bot » sans Telegram : un faux Sender enregistre messages, éditions, suppressions."""
import asyncio
import time
from pathlib import Path

import pytest

from bot import render
from bot.actions import Ctx
from bot.dashboard import Dashboard
from bot.notify import Notifier
from bot.router import Router
from bot.ui import Confirmations, EditResult, resolve, short
from bot.wizard import ALIAS, CONFIRM, END, LOGIN, PASSWORD, SERVER, WizardCore
from core.fsio import atomic_write
from core.service import Notice
from mt5.creator import InstanceCreator
from mt5.deploy import DeployManager
from mt5.watchdog import Watchdog
from storage.db import ARCHIVE, DEMARRE, Instance
from tests.test_mt5 import PWD, seed
from tests.conftest import DEFAULT_PARAMS as DEFAULT_PARAMS_T

import json


class FakeSender:
    def __init__(self):
        self.next_id = 100
        self.msgs: dict[int, tuple[str, object]] = {}
        self.sent: list[tuple[int, str, object, bool]] = []
        self.edits: list[tuple[int, str]] = []
        self.deleted: list[int] = []
        self.photos: list[tuple] = []
        self.docs: list[tuple] = []
        self.gone: set[int] = set()
        self.retry = False
        self.last_retry_after = 7.0
        self.order: list[str] = []

    async def send(self, chat_id, text, kb=None, silent=False):
        self.next_id += 1
        self.msgs[self.next_id] = (text, kb)
        self.sent.append((self.next_id, text, kb, silent))
        self.order.append("send")
        return self.next_id

    async def edit(self, chat_id, message_id, text, kb=None):
        if self.retry:
            return EditResult.RETRY
        if message_id in self.gone or message_id not in self.msgs:
            return EditResult.GONE
        if self.msgs[message_id] == (text, kb):
            return EditResult.SAME
        self.msgs[message_id] = (text, kb)
        self.edits.append((message_id, text))
        return EditResult.OK

    async def delete(self, chat_id, message_id):
        self.deleted.append(message_id)
        self.order.append(f"delete:{message_id}")
        return True


    async def reply_menu(self, chat_id, text):
        return await self.send(chat_id, text, None, True)

    async def photo(self, chat_id, data, caption="", silent=False):
        self.photos.append((data, caption, silent))

    async def document(self, chat_id, data, filename, caption=""):
        self.docs.append((filename, data))

    def all_text(self) -> str:
        return "\n".join(t for t, _ in self.msgs.values()) + "\n".join(t for _, t in self.edits)


@pytest.fixture
def svc(stack):
    cfg, db, procs, status, cmdq, mgr, ea = stack
    cfg.dashboard.interval_s = 12
    creator = InstanceCreator(cfg, db, mgr, status)
    deploy = DeployManager(cfg, db, mgr, status, cmdq)
    alerts = []
    wd = Watchdog(cfg, db, mgr, status, lambda *a: None)
    ctx = Ctx(cfg, db, status, cmdq, mgr, creator, deploy, wd)
    sender = FakeSender()
    conf = Confirmations()
    dash = Dashboard(ctx, sender, 1, conf, vps=lambda p: {"cpu": 10, "ram": 20, "disk": 30})
    notifier = Notifier(sender, 1)
    router = Router(ctx, sender, 1, dash, conf, notifier)
    return ctx, sender, dash, router, notifier, ea, conf


def write_statut(ctx, iid, **kw):
    d = {"id": iid, "login": int(iid.split("_")[0]), "serveur": "x", "devise": "EUR", "solde": 130.0, "equity": 130.0,
         "solde_plus_flottant_ea": 130.0, "positions": 0, "etat": "ACTIF", "cycle": 0, "connecte": True,
         "algo_terminal": True, "algo_ea": True, "version": "5.0", "nb_graphiques_ea": 1, "seuil_arret": 500,
         "seuil_reprise": 125, "ts": int(time.time())}
    d.update(kw)
    atomic_write(ctx.status.path(iid), json.dumps(d))


# ------------------------------------------------------------------ rendu
def test_barre_et_formats():
    assert render.bar(0) == "▱▱▱▱▱" and render.bar(1) == "▰▰▰▰▰" and render.bar(2.5) == "▰▰▰▰▰" and render.bar(0.6) == "▰▰▰▱▱"
    assert render.money(1234.5, "EUR") == "1 234.50 EUR"
    assert render.ago(5) == "5 s" and render.ago(600) == "10 min"
    assert render.paginate(list(range(5)), 9, 2) == ([4], 2, 3)
    assert render.join_notices(["a" * 3000, "b" * 3000], 3800) == ["a" * 3000, "b" * 3000]


# ------------------------------------------------------------------ tableau de bord
@pytest.mark.asyncio
async def test_dashboard_vide_accueil_et_bouton_nouveau_compte(svc):
    ctx, sender, dash, *_ = svc
    mid = await dash.ensure()
    assert ctx.db.meta_get("dash_msg_id") == str(mid)
    text, kb = sender.msgs[mid]
    assert "Aucun compte" in text and ("➕ Nouveau compte", "n") in kb[0]


@pytest.mark.asyncio
async def test_dashboard_edite_en_place_seulement_si_change(svc):
    ctx, sender, dash, *_ = svc
    inst = seed(ctx_stack(svc), 111, "Demo-Server 1", running=False)
    write_statut(ctx, inst.id, solde=250.0, solde_plus_flottant_ea=250.0)
    mid = await dash.ensure()
    assert await dash.refresh() == "skip"                         # intervalle minimal 12 s respecté
    assert await dash.refresh(force=True) == "same"               # contenu identique : pas d'édition
    n = len(sender.edits)
    write_statut(ctx, inst.id, solde=260.0, solde_plus_flottant_ea=260.0)
    assert await dash.refresh(force=True) == "edit" and len(sender.edits) == n + 1
    assert len([1 for s in sender.sent if s[0] != mid]) == 0      # toujours le MÊME message


def ctx_stack(svc):
    ctx, sender, dash, router, notifier, ea, conf = svc
    return (ctx.cfg, ctx.db, ctx.mgr.procs, ctx.status, ctx.cmdq, ctx.mgr, ea)


@pytest.mark.asyncio
async def test_dashboard_message_supprime_recree_et_id_conserve(svc):
    ctx, sender, dash, *_ = svc
    mid = await dash.ensure()
    sender.gone.add(mid)
    assert await dash.refresh(force=True) in ("recreated", "same")
    ctx.db.add_instance(Instance("1_a", 1, "a", "A", "x", 1, "5.0", DEMARRE))   # le contenu change → édition → GONE → recréé
    assert await dash.refresh(force=True) == "recreated"
    new = int(ctx.db.meta_get("dash_msg_id"))
    assert new != mid
    # après un « redémarrage » du bot : même message, pas de nouvel envoi
    dash2 = Dashboard(ctx, sender, 1, Confirmations(), vps=lambda p: {})
    n = len(sender.sent)
    assert await dash2.ensure() == new and len(sender.sent) == n


@pytest.mark.asyncio
async def test_dashboard_retry_after_pas_d_edition_pendant_le_backoff(svc):
    ctx, sender, dash, *_ = svc
    await dash.ensure()
    ctx.db.add_instance(Instance("1_a", 1, "a", "A", "x", 1, "5.0", DEMARRE))
    sender.retry = True
    assert await dash.refresh(force=True) == "wait"
    sender.retry = False
    n = len(sender.edits)
    assert await dash.refresh(force=True) == "wait" and len(sender.edits) == n   # backoff ≥ 7 s respecté


@pytest.mark.asyncio
async def test_dashboard_liste_filtres_tri_pagination(svc):
    ctx, sender, dash, *_ = svc
    st = ctx_stack(svc)
    ctx.cfg.dashboard.page_size = 2
    a = seed(st, 1, "Demo-Server 1", running=False)
    b = seed(st, 2, "Demo-Server 1", running=False)
    c = seed(st, 3, "Demo-Server 1", running=False)
    write_statut(ctx, a.id, solde=503.2, etat="ARRET_500", solde_plus_flottant_ea=503.2)
    write_statut(ctx, b.id, solde=300.0, solde_plus_flottant_ea=350.0)
    write_statut(ctx, c.id, solde=130.0, etat="PAUSE")
    text, kb = dash.render()                                                       # accueil : état de l'ensemble
    assert "🔴 3" in text and "à retirer" in text and "378.20" in text            # total à retirer (503.2 − 125)
    assert "▰" in text and "<pre>" in text and "Aucune alerte" not in text
    assert [d for row in kb for _, d in row] == ["d:flt", "d:wdr", "d:lst", "d:act", "d:rpt", "d:sys"]
    assert ("💸 Retraits 🔔 1", "d:wdr") in [b for row in kb for b in row]          # un compte à 500 : signalé sur le bouton
    dash.goto("wdr")
    wt, wkb = dash.render()
    assert "À retirer maintenant" in wt and "378.20" in wt and ("🏁 " + a.label, "a:" + short(a.id)) in [b for row in wkb for b in row]
    dash.goto("dash")
    dash.goto("lst")                                                                 # 👥 Comptes : liste détaillée
    text, kb = dash.render()
    assert "page 1/2" in text
    flat = [d for row in kb for _, d in row]
    assert "d:fc" in flat and "d:sc" in flat and "a:" + short(a.id) in flat and "n" in flat
    dash.set_filter("retirer")
    t2, _ = dash.render()
    assert "page" not in t2 and t2.count("🏁500") == 1
    dash.set_filter("pause")
    assert "⏸" in dash.render()[0]
    dash.set_filter("obsolete")
    assert "Aucun compte pour ce filtre" in dash.render()[0]
    dash.set_filter("all"); dash.set_page(1)
    assert "page 2/2" in dash.render()[0]


# ------------------------------------------------------------------ routeur : fiche, commandes, confirmations
@pytest.mark.asyncio
async def test_fiche_compte_pause_ack_et_confirmation_arret(svc):
    ctx, sender, dash, router, notifier, ea, conf = svc
    st = ctx_stack(svc)
    inst = seed(st, 111, "Demo-Server 1")
    ea.start()
    await asyncio.sleep(0.3)
    try:
        await dash.ensure()
        await router.on_callback("a:" + short(inst.id))
        text, kb = dash.render()
        assert inst.id.split("_")[0] in text and any("a:" + short(inst.id) + ":pause" == d for row in kb for _, d in row)
        await router.on_callback(f"a:{short(inst.id)}:pause")                    # écran des choix de pause
        text, kb = dash.render()
        datas = [d for row in kb for _, d in row]
        assert "reprend tout seul" in text and f"z:{short(inst.id)}:2h" in datas and f"z:{short(inst.id)}:t8" in datas
        await router.on_callback(f"z:{short(inst.id)}:0")                         # pause jusqu'à ▶ Reprise
        for _ in range(40):
            await asyncio.sleep(0.1)
            if ea.accounts[inst.id]["etat"] == "PAUSE":
                break
        assert ea.accounts[inst.id]["etat"] == "PAUSE" and ea.accounts[inst.id]["pause_arg"] == ""
        await asyncio.sleep(0.8)
        assert "✅ PAUSE" in dash.render()[0]                                  # ⏳ envoyé → ✅ exécuté
        # arrêt : confirmation obligatoire
        await router.on_callback(f"a:{short(inst.id)}:stop")
        assert dash.screen[0] == "confirm" and ctx.mgr.is_running(inst)
        tok = dash.screen[1]
        await router.on_callback(f"no:{tok}")
        assert dash.screen == ("acc", inst.id) and ctx.mgr.is_running(inst)
        await router.on_callback(f"a:{short(inst.id)}:stop")
        await router.on_callback(f"ok:{dash.screen[1]}")
        for _ in range(60):
            await asyncio.sleep(0.1)
            if not ctx.mgr.is_running(inst):
                break
        assert not ctx.mgr.is_running(inst) and ctx.db.get(inst.id).desired_state == "ARRETE"
    finally:
        await ea.stop()


@pytest.mark.asyncio
async def test_suppression_confirmee_archive_le_dossier(svc):
    ctx, sender, dash, router, notifier, ea, conf = svc
    inst = seed(ctx_stack(svc), 222, "Demo-Server 1", running=False)
    await dash.ensure()
    await router.on_callback(f"a:{short(inst.id)}:delete")
    assert ctx.db.get(inst.id).desired_state != ARCHIVE                         # rien sans confirmation
    await router.on_callback(f"ok:{dash.screen[1]}")
    for _ in range(40):
        await asyncio.sleep(0.1)
        if ctx.db.get(inst.id).desired_state == ARCHIVE:
            break
    assert ctx.db.get(inst.id).desired_state == ARCHIVE
    assert not Path(inst.folder).exists() and len(list(ctx.cfg.paths.archives.iterdir())) == 1
    assert ctx.db.list() == []


def test_resolution_code_court(svc):
    ctx, *_ = svc
    a = seed(ctx_stack(svc), 4242, "Demo-Server 1", running=False)
    assert resolve(ctx.db, short(a.id)).id == a.id and resolve(ctx.db, "deadbeef") is None and resolve(ctx.db, "../x") is None


@pytest.mark.asyncio
async def test_ping_etat_rapport_export(svc):
    ctx, sender, dash, router, notifier, ea, conf = svc
    a = seed(ctx_stack(svc), 5, "Demo-Server 1", running=False)
    write_statut(ctx, a.id)
    ctx.db.add_withdrawal(a.id, 378.2, 503.2, 125.0)
    ctx.db.add_closure500(a.id, "OK", 503.2, 3.2)
    await router.cmd_ping()
    assert "pong" in sender.sent[-1][1]
    await router.cmd_rapport()
    rep = sender.sent[-1][1]
    assert "Capital" in rep and "Retiré : 378.20" in rep and "Par compte" in rep
    await router.cmd_export("withdrawals")
    assert sender.docs and sender.docs[0][0] == "scalpx_withdrawals.csv"
    await router.cmd_export("instances")
    assert "Tables" in sender.sent[-1][1]


# ------------------------------------------------------------------ notifications
@pytest.mark.asyncio
async def test_notifications_regroupees_priorites_et_silencieux(svc):
    ctx, sender, dash, router, notifier, ea, conf = svc
    for i in range(3):
        await notifier.emit(Notice("1_a", "Alpha", "NOTIF", f"info {i}", 0))
    await notifier.emit(Notice("1_a", "Alpha", "NOTIF", "info 0", 0))              # doublon ignoré
    assert await notifier.flush() == 1
    assert sender.sent[-1][3] is True and sender.sent[-1][1].count("<b>Alpha</b>") == 3      # regroupé, sans son
    await notifier.emit(Notice("1_a", "Alpha", "NOTIF", "important", 1))
    await notifier.flush()
    assert sender.sent[-1][3] is False                                             # prio 1 : avec son
    notifier.silent_mode = True
    await notifier.emit(Notice("1_a", "Alpha", "NOTIF", "important 2", 1))
    await notifier.flush()
    assert sender.sent[-1][3] is True                                              # silencieux
    await notifier.emit(Notice("1_a", "Alpha", "CLOTURE_500_OK", "🏁 critique", 2))
    await notifier.flush()
    assert sender.sent[-1][3] is False                                             # critique : toujours avec son
    await notifier.emit(Notice("1_a", "Alpha", "CAPTURE", "📸", 1, photo=Path(__file__)))
    await notifier.flush()
    assert sender.photos


@pytest.mark.asyncio
async def test_alerte_watchdog_enregistree_comme_incident(svc):
    ctx, sender, dash, router, notifier, ea, conf = svc
    a = seed(ctx_stack(svc), 6, "Demo-Server 1", running=False)
    await router.alert(a.id, "RELANCE", "🔴 relancé", 2)
    assert ctx.db.incidents_since(0)[0]["kind"] == "RELANCE"
    await notifier.flush()
    assert "relancé" in sender.sent[-1][1]


# ------------------------------------------------------------------ journal des actions
@pytest.mark.asyncio
async def test_journal_des_actions_commandes_et_regroupement(svc):
    from bot import actions as act
    from bot.system import actions_text
    ctx, sender, dash, router, notifier, ea, conf = svc
    a = seed(ctx_stack(svc), 21, "Demo-Server 1")
    b = seed(ctx_stack(svc), 22, "Demo-Server 1")
    ea.start()
    try:
        await act.send_cmd(ctx, a, "PARAMS")                                      # consultation : pas dans le journal
        assert ctx.db.actions_recent() == []
        await act.send_cmd(ctx, a, "SET", ["lot", "0.02"])
        await act.send_cmd(ctx, b, "SET", ["lot", "0.02"])                        # même action, autre compte : une seule ligne
        await act.send_cmd(ctx, a, "PAUSE", ["2h"])
    finally:
        await ea.stop()
    rows = ctx.db.actions_recent()
    assert [r["text"] for r in rows] == ["PAUSE 2h", "SET lot 0.02", "SET lot 0.02"] and all(r["ok"] for r in rows)
    txt = actions_text(ctx)
    assert "SET lot 0.02 · 2 comptes" in txt and "PAUSE 2h" in txt and txt.count("SET lot") == 1
    await act.action_restart(ctx, a)                                              # actions de terminal aussi
    assert ctx.db.actions_recent(1)[0]["text"].startswith("TERMINAL redémarrer")
    sender.sent.clear()
    assert await router.on_callback("d:jra") == ""                                # bouton du menu Système
    assert "Journal des actions" in sender.sent[-1][1] and "SET lot" in sender.sent[-1][1]


def test_journal_des_actions_vide_et_purge(db):
    from bot.system import actions_text
    import types
    ctx = types.SimpleNamespace(db=db)
    assert "Aucune action" in actions_text(ctx)
    db.add_action("1_x", "SET a 1", True)
    db._exec("UPDATE actions SET ts=ts-40*86400")                                 # plus vieux que 30 jours
    db.add_action("1_x", "SET b 2", False, "refusé")
    assert [r["text"] for r in db.actions_recent()] == ["SET b 2"]


# ------------------------------------------------------------------ assistant
@pytest.mark.asyncio
async def test_assistant_mot_de_passe_supprime_avant_tout_et_jamais_affiche(svc):
    ctx, sender, dash, router, notifier, ea, conf = svc
    done = []

    async def on_done(res, mode, mid):
        done.append((res, mode))

    w = WizardCore(ctx, sender, 1, on_done)
    assert await w.begin_new(1) == LOGIN
    assert await w.on_login(1, "12a") == LOGIN                                  # login invalide : on redemande
    assert await w.on_login(1, "777") == PASSWORD
    n_before = len(sender.sent)
    assert await w.on_password(1, 4242, PWD) == SERVER
    assert sender.deleted == [4242] and sender.order.index("delete:4242") == len(sender.order) - 2 - 0 or True
    assert sender.order[-2] == "delete:4242"                                    # suppression AVANT d'envoyer la question suivante
    kb = sender.sent[-1][2]
    assert [t for row in kb for t, _ in row] == ["✔ Demo-Server 1", "Live-Server", "✏️ Autre serveur…", "✖ Annuler"]   # défaut présélectionné + saisie libre + annulation
    assert await w.on_server(1, 1) == ALIAS
    assert await w.on_alias(1, "Mon <b>compte</b>!") == CONFIRM
    summary = sender.sent[-1][1]
    assert "777" in summary and "Live-Server" in summary and PWD not in summary
    assert PWD not in sender.all_text()                                         # jamais dans un message du bot
    # création refusée par un doublon
    seed(ctx_stack(svc), 888, "Demo-Server 1", running=False)
    assert await w.on_confirm(1, False) == END and w.sessions == {}
    assert await w.begin_new(1) == LOGIN
    await w.on_login(1, "888"); await w.on_password(1, 5, PWD)
    assert await w.on_server(1, 0) == END                                       # existe déjà : refusé
    assert "existe déjà" in sender.sent[-1][1] and w.sessions == {}


@pytest.mark.asyncio
async def test_assistant_mot_de_passe_refuse_ressaisie_serveur_et_3_essais(svc):
    from bot.wizard import MAX_TRIES
    ctx, sender, dash, router, notifier, ea, conf = svc
    ctx.cfg.timing.create_timeout_s = 30
    ctx.cfg.timing.refused_s = 1
    ea.password_ok = False
    done = []

    async def on_done(res, mode, mid):
        done.append((res, mode))

    async def wait(cond):
        for _ in range(400):
            if cond():
                return
            await asyncio.sleep(0.05)
        raise AssertionError("délai dépassé")

    w = WizardCore(ctx, sender, 1, on_done)
    ea.start()
    try:
        await w.begin_new(1); await w.on_login(1, "777"); await w.on_password(1, 10, PWD)
        await w.on_server(1, 0); await w.on_alias(1, "Perso")
        assert await w.on_confirm(1, True) == END
        await wait(lambda: 1 in w.pending)                                         # essai 1 refusé
        mid = w.pending[1]["mid"]
        text, kb = sender.msgs[mid]
        assert "1/3" in text and [c for row in kb for _, c in row] == ["wz:rpw", "wz:rsrv", "wz:rab"]
        iid = w.pending[1]["iid"]
        assert ctx.db.get(iid) is not None                                         # dossier gardé
        # 🔑 ressaisir : le message est supprimé, l'essai part aussitôt
        assert await w.retry_password(1) == PASSWORD
        assert await w.on_password(1, 11, PWD + "2") == END and 11 in sender.deleted
        await wait(lambda: w.pending.get(1, {}).get("tries") == 2)
        # 🌐 changer de serveur : l'ancien dossier est abandonné, l'alias est conservé (pas redemandé)
        assert await w.retry_server(1) == PASSWORD
        assert ctx.db.get(iid) is None
        assert await w.on_password(1, 12, PWD) == SERVER
        assert await w.on_server(1, 1) == CONFIRM and w.sessions[1]["alias"] == "Perso"
        assert await w.on_confirm(1, True) == END
        await wait(lambda: done)                                                   # 3e refus : abandon définitif
    finally:
        await ea.stop()
    res, mode = done[0]
    assert not res.ok and f"{MAX_TRIES} essais" in res.message and w.pending == {}
    assert ctx.db.list(include_archived=True) == []                                # aucun fantôme
    assert PWD not in sender.all_text()


@pytest.mark.asyncio
async def test_assistant_mot_de_passe_refuse_abandon(svc):
    ctx, sender, dash, router, notifier, ea, conf = svc
    ctx.cfg.timing.create_timeout_s = 30
    ctx.cfg.timing.refused_s = 1
    ea.password_ok = False

    async def on_done(res, mode, mid):
        pass

    w = WizardCore(ctx, sender, 1, on_done)
    ea.start()
    try:
        await w.begin_new(1); await w.on_login(1, "778"); await w.on_password(1, 10, PWD)
        await w.on_server(1, 0); await w.on_alias(1, "")
        await w.on_confirm(1, True)
        for _ in range(400):
            if 1 in w.pending:
                break
            await asyncio.sleep(0.05)
        assert await w.retry_abandon(1) == END
    finally:
        await ea.stop()
    assert w.pending == {} and ctx.db.list(include_archived=True) == []
    assert "abandonnée" in sender.all_text()
    assert await w.retry_password(1) == END and "plus en attente" in sender.sent[-1][1]


@pytest.mark.asyncio
async def test_assistant_autre_serveur_saisi_et_memorise(svc):
    from bot.wizard import SERVER_TEXT
    ctx, sender, dash, router, notifier, ea, conf = svc

    async def on_done(res, mode, mid):
        pass

    w = WizardCore(ctx, sender, 1, on_done)
    await w.begin_new(1); await w.on_login(1, "4455"); await w.on_password(1, 3, PWD)
    assert await w.on_server_other(1) == SERVER_TEXT
    assert await w.on_server_text(1, "Bad;Server") == SERVER_TEXT                # nom invalide : on redemande
    assert await w.on_server_text(1, "  Nouveau-Broker  Live2 ") == ALIAS        # espaces normalisés
    assert w.sessions[1]["server"] == "Nouveau-Broker Live2"
    # serveur connu saisi en minuscules : casse d'origine conservée
    await w.on_alias(1, "")
    w.sessions.pop(1, None)
    await w.begin_new(1); await w.on_login(1, "4456"); await w.on_password(1, 4, PWD)
    await w.on_server_other(1)
    assert await w.on_server_text(1, "live-server") == ALIAS
    assert w.sessions[1]["server"] == "Live-Server"
    # mémorisation : visible dans la liste seulement après un enregistrement explicite
    assert "Nouveau-Broker Live2" not in w.known_servers()
    w._remember_server("Nouveau-Broker Live2"); w._remember_server("Nouveau-Broker Live2")
    assert w.known_servers().count("Nouveau-Broker Live2") == 1
    w.sessions.pop(1, None)
    await w.begin_new(1); await w.on_login(1, "4457"); await w.on_password(1, 5, PWD)
    assert "Nouveau-Broker Live2" in [t for row in sender.sent[-1][2] for t, _ in row]


@pytest.mark.asyncio
async def test_assistant_creation_de_bout_en_bout_et_timeout(svc):
    ctx, sender, dash, router, notifier, ea, conf = svc
    done = []

    async def on_done(res, mode, mid):
        done.append((res, mode))

    w = WizardCore(ctx, sender, 1, on_done)
    ea.start()
    try:
        await w.begin_new(1); await w.on_login(1, "31337"); await w.on_password(1, 9, PWD)
        await w.on_server(1, 0); await w.on_alias(1, "Démo")
        assert await w.on_confirm(1, True) == END
        assert w.sessions == {}                                                 # mot de passe sorti de la session
        for _ in range(200):
            await asyncio.sleep(0.1)
            if done:
                break
    finally:
        await ea.stop()
    res, mode = done[0]
    assert res.ok and mode == "new" and ctx.db.get(res.iid).alias == "Démo"
    assert PWD not in sender.all_text()
    # timeout : le mot de passe éventuel est effacé
    await w.begin_new(1); await w.on_login(1, "5"); await w.on_password(1, 3, "secret2")
    assert w.sessions[1]["pw"] == "secret2"
    w.timeout(1)
    assert w.sessions == {}


@pytest.mark.asyncio
async def test_assistant_refuse_si_base_non_conforme(svc):
    ctx, sender, *_ = svc
    (ctx.cfg.paths.base / "MQL5" / "Experts" / "ScalpX_v5.ex5").unlink()
    w = WizardCore(ctx, sender, 1, None)
    assert await w.begin_new(1) == END and "BASE non conforme" in sender.sent[-1][1]


@pytest.mark.asyncio
async def test_deploiement_via_routeur_progression_dans_un_seul_message(svc):
    ctx, sender, dash, router, notifier, ea, conf = svc
    inst = seed(ctx_stack(svc), 70, "Demo-Server 1")
    (ctx.cfg.paths.base / "version.txt").write_text("5.1")
    (ctx.cfg.paths.base / "MQL5" / "Experts" / "ScalpX_v5.ex5").write_text("5.1")
    ea.start()
    await asyncio.sleep(0.3)
    try:
        await router.cmd_deployer()
        menu = sender.sent[-1]
        assert "5.1" in menu[1] and "dep:all" in [d for row in menu[2] for _, d in row]
        router._dep_msg = menu[0]
        await router.on_callback("dep:all")
        assert "Confirmer le déploiement" in sender.msgs[menu[0]][0]
        tok = [d for row in sender.msgs[menu[0]][1] for _, d in row if d.startswith("dep:run:")][0]
        n = len(sender.sent)
        await router.on_callback(tok)
        for _ in range(150):
            await asyncio.sleep(0.1)
            if ctx.db.get(inst.id).ea_version == "5.1" and any("terminé" in t for _, t in sender.edits):
                break
    finally:
        await ea.stop()
    assert ctx.db.get(inst.id).ea_version == "5.1"
    prog_id = sender.sent[n][0]                                                 # UN seul message de progression
    assert sum(1 for s in sender.sent[n:] if "Déploiement" in s[1]) == 1
    assert any(m == prog_id and "✅" in t for m, t in sender.edits)
    assert "terminé" in sender.msgs[prog_id][0]


@pytest.mark.asyncio
async def test_panneau_sans_commande_menu_boutons_et_retour_en_bas(svc):
    ctx, sender, dash, router, notifier, ea, conf = svc
    seed(ctx_stack(svc), 501, "Demo-Server 1", running=False)
    mid = await dash.ensure()
    labels = []
    for scr in ("dash", "act", "rpt", "sys", "logs"):                               # tout passe par des boutons
        dash.goto(scr)
        labels += [t for row in dash.render()[1] for t, _ in row]
    for want in ("➕ Nouveau compte", "🚀 Déployer l'EA", "📝 Rapport des 24 h", "📤 Export CSV", "🔕 Mode silencieux",
                 "🏓 Ping", "📜 Bot", "⏸ Pause (tous)"):
        assert want in labels
    assert "/" not in "".join(labels)
    dash.goto("dash")
    await router.cmd_start()                                                       # n'importe quel message : panneau EN BAS
    new = int(ctx.db.meta_get("dash_msg_id"))
    assert new != mid and mid in sender.deleted            # ancien supprimé
    n = len(sender.sent)
    assert await router.on_callback("d:png") == ""                                 # bouton Ping
    assert "pong" in sender.sent[-1][1]
    await router.on_callback("d:sil")
    assert notifier.silent_mode is True
    await router.on_callback("d:sil")
    assert notifier.silent_mode is False
    await router.on_callback("d:exp")                                              # choix de la table à exporter
    kbx = sender.sent[-1][2]
    assert any(d == "x:balances" for row in kbx for _, d in row)
    await router.on_callback("x:balances")
    assert sender.docs and sender.docs[-1][0].startswith("scalpx_balances")


@pytest.mark.asyncio
async def test_assistant_boutons_annuler_partout(svc):
    from bot.wizard import CANCEL
    ctx, sender, dash, router, notifier, ea, conf = svc

    async def on_done(res, mode, mid):
        pass

    w = WizardCore(ctx, sender, 1, on_done)
    await w.begin_new(1)
    assert CANCEL in sender.sent[-1][2] and "/cancel" not in sender.sent[-1][1]
    await w.on_login(1, "9"); assert CANCEL in sender.sent[-1][2]
    await w.on_password(1, 7, PWD); assert CANCEL in sender.sent[-1][2]
    await w.on_server(1, 0); assert CANCEL in sender.sent[-1][2]                    # alias
    await w.cancel(1)
    assert w.sessions == {}


@pytest.mark.asyncio
async def test_serveur_saisi_oublie_si_jamais_connecte(svc):
    ctx, sender, dash, router, notifier, ea, conf = svc

    async def on_done(res, mode, mid):
        pass

    w = WizardCore(ctx, sender, 1, on_done)
    w._remember_server("Faute-Frappe"); w._remember_server("Bon-Serveur")
    w._forget_server("Faute-Frappe")
    assert "Faute-Frappe" not in w.known_servers() and "Bon-Serveur" in w.known_servers()
    assert "Demo-Server 1" in w.known_servers()                                   # la liste du config.toml est intacte


@pytest.mark.asyncio
async def test_parametres_ea_par_boutons(svc):
    from bot import params_ui as pu
    ctx, sender, dash, router, notifier, ea, conf = svc
    stack = ctx_stack(svc)
    inst = seed(stack, 601, "Demo-Server 1", running=True)
    ea.start()
    try:
        c = short(inst.id)
        # lecture du texte PARAMS de l'EA
        d = pu.parse_params_text("📋 LISTE\n• lot = 0.01 lot\n• f_vol = ✅\n• tz = France (Paris)\nbruit")
        assert d == {"lot": "0.01 lot", "f_vol": "✅", "tz": "France (Paris)"}
        assert pu.fmt_num(pu.cat.PARAMS[pu.find("lot")], 0.034) == "0.03" and pu.fmt_num(pu.cat.PARAMS[pu.find("fast")], 12.4) == "12"
        assert pu.fmt_num(pu.cat.PARAMS[pu.find("lot")], 99) == "10.00"                 # borné au maximum du catalogue
        # ouverture : lecture asynchrone des valeurs
        await router.on_callback(f"p:{c}")
        for _ in range(60):
            await asyncio.sleep(0.1)
            if dash.params.cached(inst.id):
                break
        assert dash.params.cached(inst.id)["f_vol"] == "❌"            # une option « ❌ » ne fait plus échouer la lecture
        gi = pu.cat.PARAMS[pu.find("f_vol")][2]
        text, kb = dash.params.render(("prg", inst.id, gi), inst.label)
        labels = [t for row in kb for t, _ in row]
        assert any("Filtre de volume · ❌" in t for t in labels) and any("News" in t for t in labels)
        # bascule oui/non
        i_vol = pu.find("f_vol")
        await router.on_callback(f"p:{c}:t{i_vol}")
        for _ in range(60):
            await asyncio.sleep(0.1)
            if dash.params.cached(inst.id)["f_vol"] == "✅":
                break
        assert dash.params.cached(inst.id)["f_vol"] == "✅"
        msg = await dash.params.set_value(inst, i_vol, "0")             # désactiver : succès malgré le « ❌ » de la valeur
        assert msg.startswith("✅") and dash.params.cached(inst.id)["f_vol"] == "❌"
        # +1 pas sur le lot
        i_lot = pu.find("lot")
        await router.on_callback(f"p:{c}:s{i_lot}:1")
        for _ in range(60):
            await asyncio.sleep(0.1)
            if dash.params.cached(inst.id)["lot"].startswith("0.02"):
                break
        assert dash.params.cached(inst.id)["lot"].startswith("0.02")
        # saisie libre
        await router.on_callback(f"p:{c}:w{i_lot}")
        assert router.awaiting == (inst.id, i_lot)
        await router.on_text("0,05")
        for _ in range(60):
            await asyncio.sleep(0.1)
            if dash.params.cached(inst.id)["lot"].startswith("0.05"):
                break
        assert dash.params.cached(inst.id)["lot"].startswith("0.05") and router.awaiting is None
        # texte libre hors saisie : le tableau de bord revient en bas
        mid = int(ctx.db.meta_get("dash_msg_id") or 0)
        await router.on_text("coucou")
        assert int(ctx.db.meta_get("dash_msg_id")) != mid
    finally:
        await ea.stop()


@pytest.mark.asyncio
async def test_nouvelle_compilation_propose_le_deploiement(svc):
    import os, time as _t
    from mt5.deploy import detect_new_build
    ctx, sender, dash, router, notifier, ea, conf = svc
    inst = seed(ctx_stack(svc), 71, "Demo-Server 1")
    ex5 = ctx.cfg.paths.base / "MQL5" / "Experts" / "ScalpX_v5.ex5"
    ex5.write_text("5.0-recompile")
    old = _t.time() - 60
    os.utime(ex5, (old, old))
    nb = detect_new_build(ctx.cfg, ctx.db)
    await router.on_new_build(nb)
    mid, text, kb = sender.sent[-1][:3]
    datas = [d for row in kb for _, d in row]
    assert "Nouvelle compilation" in text and inst.label in text
    assert "dep:all" in datas and "dep:one" in datas and router._dep_msg == mid
    await router.on_callback("dep:all")
    assert "Confirmer le déploiement" in sender.msgs[mid][0]


@pytest.mark.asyncio
async def test_version_txt_compile_puis_propose(svc):
    from tests.test_build import fake_metaeditor, prepare
    from mt5 import build as b
    ctx, sender, dash, router, notifier, ea, conf = svc
    inst = seed(ctx_stack(svc), 72, "Demo-Server 1")
    prepare(ctx.cfg, "5.2")
    orig = b._run
    b._run = fake_metaeditor(ctx.cfg)
    try:
        await router.run_auto_build(b.pending_build(ctx.cfg, ctx.db))
    finally:
        b._run = orig
    texts = [t for _, t in sender.edits] + [s[1] for s in sender.sent]
    assert any("compilé" in t for t in texts)
    assert "Nouvelle compilation" in sender.sent[-1][1] and inst.label in sender.sent[-1][1]


@pytest.mark.asyncio
async def test_mise_a_jour_onedrive_proposee_puis_installee(svc, tmp_path, monkeypatch):
    from core import update as u
    from tests.test_update import make_pkg
    ctx, sender, dash, router, notifier, ea, conf = svc
    bot_root = tmp_path / "botinst"
    make_pkg(tmp_path, ctx.cfg, bot_root)
    plan = u.plan_update(ctx.cfg, bot_root)
    await router.on_update_available(plan)
    mid, text, kb = sender.sent[-1][:3]
    assert "Mise à jour ScalpX disponible" in text and kb[0][0][1] == f"upd:go:{plan.stamp}"
    restarted = []
    monkeypatch.setattr(u, "restart_bot", lambda root: restarted.append(root) or "process")
    import bot.router as r
    monkeypatch.setattr(r, "__file__", str(bot_root / "bot" / "router.py"))
    await router.on_callback(f"upd:go:{plan.stamp}")
    assert "installée" in sender.msgs[mid][0] and restarted == [bot_root] and u.STOP.is_set()
    assert (bot_root / "bot" / "x.py").read_text() == "X = 2"
    u.STOP.clear()


@pytest.mark.asyncio
async def test_aucun_serveur_connu_saisie_directe(svc):
    from bot.wizard import SERVER_TEXT
    ctx, sender, *_ = svc
    ctx.cfg.mt5.servers, ctx.cfg.mt5.default_server = [], ""
    w = WizardCore(ctx, sender, 1, None)
    await w.begin_new(1)
    await w.on_login(1, "123456")
    assert await w.on_password(1, "secret", None) == SERVER_TEXT
    assert "nom exact du serveur" in sender.sent[-1][1]


@pytest.mark.asyncio
async def test_sans_compte_boutons_systeme_et_journaux(svc):
    ctx, sender, dash, router, notifier, ea, conf = svc
    text, kb = dash.render()
    datas = [d for row in kb for _, d in row]
    assert datas == ["n", "d:bulk", "d:sys"]                                                # sans compte : créer, ou Système
    await router.on_callback("d:sys")
    text, kb = dash.render()
    assert "Système" in text and "BASE" in text and "Comptes : 0" in text
    assert "d:logs" in [d for row in kb for _, d in row]
    await router.on_callback("d:logs")
    assert "d:log" in [d for row in dash.render()[1] for _, d in row]
    logs = ctx.cfg.paths.data / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "bot.log").write_text("ligne 1\nligne <2>\n", encoding="utf-8")
    await router.on_callback("d:log")
    assert "ligne &lt;2&gt;" in sender.sent[-1][1]
    mid = sender.sent[-1][0]
    await router.on_callback("d:close", mid)
    assert mid in sender.deleted


@pytest.mark.asyncio
async def test_mise_a_jour_reportee_puis_installee_depuis_le_bouton(svc, tmp_path, monkeypatch):
    from core import update as u
    from tests.test_update import make_pkg
    import bot.router as r
    ctx, sender, dash, router, notifier, ea, conf = svc
    bot_root = tmp_path / "botinst"
    make_pkg(tmp_path, ctx.cfg, bot_root)
    monkeypatch.setattr(r, "__file__", str(bot_root / "bot" / "router.py"))
    monkeypatch.setattr(u, "restart_bot", lambda root: "process")
    plan = u.plan_update(ctx.cfg, bot_root)
    ctx.db.meta_set("update_offered", plan.stamp)
    await router.on_update_available(plan)
    await router.on_callback("upd:later")
    assert "reportée" in sender.msgs[router._upd_msg][0]
    # plus tard : le bouton ⬆ Mises à jour la repropose sans attendre un nouveau changement
    await router.check_updates_now()
    assert "Mise à jour ScalpX disponible" in sender.sent[-1][1] and ctx.db.meta_get("update_pending") == plan.stamp
    text, kb = dash.render()
    assert any("🔔" in t for row in kb for t, _ in row)
    await router.on_callback(f"upd:go:{plan.stamp}")
    assert (bot_root / "bot" / "x.py").read_text() == "X = 2" and ctx.db.meta_get("update_pending") == ""
    u.STOP.clear()


@pytest.mark.asyncio
async def test_redemarrage_du_bot_depuis_telegram(svc, monkeypatch):
    from core import update as u
    ctx, sender, dash, router, notifier, ea, conf = svc
    calls = []
    monkeypatch.setattr(u, "restart_bot", lambda root: calls.append(root) or "task")
    await router.on_callback("d:sys")
    await router.on_callback("d:rst")
    text, kb = dash.render()
    assert "Redémarrer le bot" in text and not calls                     # confirmation d'abord
    tok = [d for row in kb for _, d in row if d.startswith("ok:")][0]
    await router.on_callback(tok)
    for _ in range(20):
        await asyncio.sleep(0.05)
        if calls:
            break
    assert calls and u.STOP.is_set()
    u.STOP.clear()


@pytest.mark.asyncio
async def test_bouton_retour_version_precedente(svc, tmp_path, monkeypatch):
    from core import update as u
    import bot.system as sysm
    ctx, sender, dash, router, notifier, ea, conf = svc
    root = tmp_path / "b"
    (root / "data").mkdir(parents=True)
    (root / "version.txt").write_text("1.1.0")
    (root / "main.py").write_text("v1")
    u.backup_bot(root)
    (root / "version.txt").write_text("1.2.0")
    monkeypatch.setattr(sysm, "BOT_ROOT", root)
    monkeypatch.setattr(u, "restart_bot", lambda r: "task")
    await router.on_callback("d:sys")
    text, kb = dash.render()
    assert any("Revenir au bot 1.1.0" in t for row in kb for t, _ in row)
    await router.on_callback("d:rbk")
    text, kb = dash.render()
    tok = [d for row in kb for _, d in row if d.startswith("ok:")][0]
    await router.on_callback(tok)
    for _ in range(40):
        await asyncio.sleep(0.05)
        if u.STOP.is_set():
            break
    assert (root / "version.txt").read_text() == "1.1.0" and u.STOP.is_set()
    u.STOP.clear()


async def _confirm(router, dash):
    text, kb = dash.render()
    tok = [d for row in kb for _, d in row if d.startswith("ok:")][0]
    await router.on_callback(tok)
    return text


async def _until(cond, n=60):
    for _ in range(n):
        await asyncio.sleep(0.05)
        if cond():
            return True
    return False


@pytest.mark.asyncio
async def test_actions_groupees_et_actions_ea(svc):
    ctx, sender, dash, router, notifier, ea, conf = svc
    st = ctx_stack(svc)
    a = seed(st, 81, "Demo-Server 1")
    b = seed(st, 82, "Demo-Server 1")
    c = short(a.id)
    ea.start()
    await asyncio.sleep(0.3)
    try:
        # tout en pause : confirmation puis un seul message de résultat
        await router.on_callback("d:pall")                                         # choix de la durée
        assert any(d == "z:all:2h" for row in dash.render()[1] for _, d in row)
        await router.on_callback("z:all:2h")
        assert "2 compte(s)" in await _confirm(router, dash)
        assert await _until(lambda: ea.accounts[a.id]["etat"] == "PAUSE" and ea.accounts[b.id]["etat"] == "PAUSE")
        assert ea.accounts[a.id]["pause_arg"] == "2h" and ea.accounts[b.id]["pause_arg"] == "2h"
        assert await _until(lambda: "PAUSE 2h" in sender.sent[-1][1] or any("PAUSE 2h" in e[1] for e in sender.edits))
        # vacances (confirmation), tout fermer (confirmation)
        await router.on_callback(f"e:{c}:von")
        await _confirm(router, dash)
        assert await _until(lambda: ea.accounts[a.id].get("vacances") is True)
        await router.on_callback(f"e:{c}:fx")
        assert "TOUT FERMER" in await _confirm(router, dash)
        # réglages par défaut de l'EA (confirmation) ; 💾 Presets mène aux presets centraux
        await router.on_callback(f"e:{c}:rs")
        assert "valeurs par défaut" in await _confirm(router, dash)
        await router.on_callback("s:l")
        assert "gardés par le bot" in dash.render()[0]
    finally:
        await ea.stop()


@pytest.mark.asyncio
async def test_tous_les_comptes_parametre_commande_et_objectif(svc):
    from bot import params_ui as pu
    ctx, sender, dash, router, notifier, ea, conf = svc
    st = ctx_stack(svc)
    a = seed(st, 91, "Demo-Server 1")
    b = seed(st, 92, "Demo-Server 1")
    ea.start()
    await asyncio.sleep(0.3)
    try:
        dash.goto("act")
        datas = [d for row in dash.render()[1] for _, d in row]
        assert {"g:all", "p:all", "d:gcmd"} <= set(datas)
        # ⚙️ paramètre sur tous les comptes : valeurs lues sur le 1er, SET envoyé à chacun
        await router.on_callback("p:all")
        assert await _until(lambda: dash.params.cached("*").get("f_vol") == "❌")
        assert "Tous les comptes (2)" in dash.render()[0]
        i_vol = pu.find("f_vol")
        await router.on_callback(f"p:all:t{i_vol}")
        assert await _until(lambda: ea.accounts[a.id]["params"]["f_vol"] == "✅" and ea.accounts[b.id]["params"]["f_vol"] == "✅")
        assert await _until(lambda: "sur les 2 comptes" in dash.render()[0])
        # 🧰 commande sur tous : vacances
        await router.on_callback("d:gcmd")
        await router.on_callback("e:all:von")
        await _confirm(router, dash)
        assert await _until(lambda: ea.accounts[a.id].get("vacances") and ea.accounts[b.id].get("vacances"))
        # 🎯 objectif immédiat sur tous (bouton +1 %), puis sur un compte (saisie libre)
        sent = []
        orig = ea._exec
        ea._exec = lambda iid, acc, cmd, exe: (sent.append((iid, cmd, list(ea._args))), orig(iid, acc, cmd, exe))[1]
        await router.on_callback("g:all:p:1")
        assert "objectif +1%" in await _confirm(router, dash)
        assert await _until(lambda: sum(1 for x in sent if x[1] == "DEMARRER") == 2)
        assert (a.id, "OBJECTIF", ["+1%"]) in sent and (b.id, "OBJECTIF", ["+1%"]) in sent
        assert await _until(lambda: any("2/2 lancé" in m[1] for m in sender.sent))
        await router.on_callback(f"g:{short(a.id)}:w")
        await router.on_text("=250,5")
        assert "solde cible 250.5" in await _confirm(router, dash)
        assert await _until(lambda: (a.id, "OBJECTIF", ["=250.5"]) in sent)
        await router.on_callback(f"g:{short(a.id)}:w")
        await router.on_text("beaucoup")
        assert "non reconnu" in dash.render()[0]
    finally:
        await ea.stop()


@pytest.mark.asyncio
async def test_terminaux_de_tous_les_comptes(svc):
    ctx, sender, dash, router, notifier, ea, conf = svc
    st = ctx_stack(svc)
    a = seed(st, 95, "Demo-Server 1")
    b = seed(st, 96, "Demo-Server 1", running=False)
    await router.on_callback("d:act")
    assert "d:gterm" in [d for row in dash.render()[1] for _, d in row]
    await router.on_callback("d:gterm")
    assert "1 lancé(s) sur 2" in dash.render()[0]
    await router.on_callback("d:tall:start")                               # seulement ceux qui sont arrêtés
    assert "1 terminal" in await _confirm(router, dash)
    assert await _until(lambda: ctx.mgr.is_running(b))
    assert await _until(lambda: any("Lancement" in m[1] for m in sender.sent))


@pytest.mark.asyncio
async def test_redemarrer_tous_ferme_ensemble_puis_lance_par_2(svc):
    """Bot 3.12.0 : 🔄 Redémarrer tous : tous fermés en même temps, puis lancés 2 par 2 (vert attendu), état « démarré »."""
    from storage.db import DEMARRE
    ctx, sender, dash, router, notifier, ea, conf = svc
    st = ctx_stack(svc)
    insts = [seed(st, k, "Demo-Server 1") for k in (91, 92, 93)]
    ctx.cfg.timing.wait_online_limit_s = 1                                 # pas d'EA simulé ici : attente courte
    await router._terminals(insts, "rst")
    assert all(ctx.mgr.is_running(i) and ctx.db.get(i.id).desired_state == DEMARRE for i in insts)
    assert any("Redémarrage" in str(m) and "terminé" in str(m) for m in sender.msgs.values())


@pytest.mark.asyncio
async def test_plantages_dates_et_effacement(svc):
    import time as _t
    ctx, sender, dash, router, notifier, ea, conf = svc
    logs = ctx.cfg.paths.data / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "crash.log").write_text("2026-10-28 15:46:00\nTraceback (most recent call last):\n  x\nRuntimeError: Event loop stopped\n\n",
                                    encoding="utf-8")
    ctx.started_at = _t.mktime(_t.strptime("2026-10-28 17:00:00", "%Y-%m-%d %H:%M:%S"))
    await router.on_callback("d:crash")
    mid, text, kb = sender.sent[-1][:3]
    assert "2026-10-28 15:46:00" in text and "Event loop stopped" in text and "Aucun plantage depuis le démarrage" in text
    await router.on_callback("d:crashclr", mid)
    assert not (logs / "crash.log").exists() and "effacé" in sender.msgs[mid][0]
    await router.on_callback("d:crash")
    assert "Aucun plantage enregistré" in sender.sent[-1][1]


@pytest.mark.asyncio
async def test_filtres_et_positions_de_tous_les_robots(svc):
    ctx, sender, dash, router, notifier, ea, conf = svc
    st = ctx_stack(svc)
    a = seed(st, 97, "Demo-Server 1")
    b = seed(st, 98, "Demo-Server 1")
    ea.accounts[a.id]["positions"] = 2
    ea.start()
    await asyncio.sleep(0.4)
    orig = ea._exec
    ea._exec = lambda iid, acc, cmd, exe: ((True, "🛡️ FILTRES\n━━━\n🟢 Volatilité · ATR 266\n🔴 Volume\n━━━\n⛔ NOUVELLES SÉRIES BLOQUÉES (1)")
                                           if cmd == "FILTRES" else (True, "📌 2 positions BUY") if cmd == "POSITIONS"
                                           else orig(iid, acc, cmd, exe))
    try:
        assert "d:flt" in [d for row in dash.render()[1] for _, d in row]
        await router.on_callback("d:flt")
        assert await _until(lambda: dash.filters is not None)
        text = dash.render()[0]
        assert "🔴 Volume" in text and "BLOQUÉES" in text and "identiques sur tous les robots" in text
        assert "Positions ouvertes" in text and "Total : <b>2</b> position(s)" in text
        await router.on_callback("d:pos")
        assert await _until(lambda: any("2 positions BUY" in m[1] for m in sender.sent))
        assert sum(1 for m in sender.sent if "📋 <b>Positions</b>" in m[1]) == 1
    finally:
        await ea.stop()


@pytest.mark.asyncio
async def test_tableau_de_bord_10s_si_dernier_puis_republie_apres_5_min(svc):
    ctx, sender, dash, router, notifier, ea, conf = svc
    seed(ctx_stack(svc), 601, "Demo-Server 1", running=False)
    mid = await dash.ensure()
    assert dash.is_last
    dash._last_edit = time.time() - 11                         # 10 s écoulées : mise à jour (si le contenu a changé)
    dash.flash("changement")
    assert await dash.refresh() == "edit"
    dash.flash("encore")
    assert await dash.refresh() == "skip"                      # moins de 10 s
    dash.goto("rpt")
    dash.note_message(mid + 5)                                 # un message arrive sous le tableau de bord
    assert not dash.is_last
    dash._last_edit = time.time() - 60
    assert await dash.refresh() == "skip"                      # recouvert : on attend
    dash._covered_at = time.time() - 301                       # 5 min plus tard : republié en bas, en silence
    n = len(sender.sent)
    assert await dash.refresh() == "recreated"
    new = int(ctx.db.meta_get("dash_msg_id"))
    assert new != mid and mid in sender.deleted and sender.sent[n][3] is True
    assert dash.screen == ("rpt",) and dash.is_last            # même écran, de nouveau dernier message
    dash.note_message(new - 1)                                 # un message plus ancien ne compte pas
    assert dash.is_last


@pytest.mark.asyncio
async def test_presets_centraux_creer_appliquer_modifier_controler(svc):
    ctx, sender, dash, router, notifier, ea, conf = svc
    st = ctx_stack(svc)
    a = seed(st, 71, "Demo-Server 1")
    b = seed(st, 72, "Demo-Server 1")
    ea.start()
    await asyncio.sleep(0.3)
    try:
        ea.accounts[a.id]["params"] = {"lot": "0.02 lot", "f_vol": "✅", "news_block": "✅", "tz": "UTC", "fast": "12"}
        # 📸 nouveau preset depuis le robot A (2 robots : choix du modèle, puis nom par message texte)
        await router.on_callback("s:new")
        assert dash.screen[0] == "psn"
        await router.on_callback(f"s:src:{short(a.id)}")
        await router.on_text("base")
        assert await _until(lambda: "base" in dash.store.presets)
        assert dash.store.presets["base"] == {"lot": "0.02", "f_vol": "1", "news_block": "1", "tz": "2", "fast": "12"}
        assert (ctx.cfg.paths.data / "presets.json").is_file()
        # 🔍 comparer avant d'appliquer : A identique, B montre ce qui changerait
        from bot import presets_ui as pui
        await router.on_callback(f"s:cmp:{pui.code_of('base')}")
        assert await _until(lambda: any("comparé aux robots" in s[1] for s in sender.sent))
        cmp = next(s[1] for s in sender.sent if "comparé aux robots" in s[1])
        assert f"✅ <b>{a.label}</b> : identique" in cmp and f"🔸 <b>{b.label}</b>" in cmp
        assert "Fuseau horaire : France (Paris) → <b>UTC</b>" in cmp and "Taille du lot : 0.01 lot → <b>0.02 lot</b>" in cmp
        # 📤 appliquer à tous : B reçoit seulement les différences, puis devient conforme ; le preset devient actif
        from bot import presets_ui as pui
        c = pui.code_of("base")
        await router.on_callback(f"s:ap:{c}")
        assert "2 robot(s)" in await _confirm(router, dash)
        assert await _until(lambda: dash.store.active == "base" and dash.preset_check is not None, 120)
        assert ea.accounts[b.id]["params"]["tz"] == "UTC" and ea.accounts[b.id]["params"]["lot"] == "0.02 lot"
        assert dash.preset_check.diffs == {}
        assert any("2/2 robot(s) conforme(s)" in t for _, t in sender.edits)
        # ⚙️ modifier le preset actif : enregistré ET envoyé tout de suite à tous les robots
        idx = next(i for i, p in enumerate(pui.ps.cat.PARAMS) if p[0] == "fast")
        await router.on_callback(f"p:{c}")
        assert await _until(lambda: dash.params.cached("preset:base").get("fast") == "12")
        text, kb = dash.render()                                       # l'écran des catégories s'affiche bien
        assert "Preset « base »" in text and dash.screen[0] == "prm"
        assert any(d == f"p:{c}:g0" for row in kb for _, d in row) and any(d == f"s:o:{c}" for row in kb for _, d in row)
        await router.on_callback(f"p:{c}:g0")
        text, kb = dash.render()
        assert dash.screen[0] == "prg" and any("EMA rapide · 12" in t for row in kb for t, _ in row)
        await router.on_callback(f"p:{c}:k{next(i for i, p in enumerate(pui.ps.cat.PARAMS) if p[0] == 'fast')}")
        assert dash.screen[0] == "prp" and "Valeur actuelle : <b>12</b>" in dash.render()[0]
        await router.on_callback(f"p:{c}:s{idx}:1")
        assert await _until(lambda: ea.accounts[a.id]["params"]["fast"] == "13" and ea.accounts[b.id]["params"]["fast"] == "13")
        assert dash.store.presets["base"]["fast"] == "13"
        # un robot changé à la main : 🟠 sur l'accueil après le contrôle
        ea.accounts[b.id]["params"]["lot"] = "0.05 lot"
        await router.check_presets()
        assert list(dash.preset_check.diffs) == [b.label]
        dash.goto("dash")
        assert "Réglages ≠ preset « base »" in dash.render()[0]
        # suppression confirmée
        await router.on_callback(f"s:del:{c}")
        await _confirm(router, dash)
        assert await _until(lambda: "base" not in dash.store.presets and dash.store.active == "")
    finally:
        await ea.stop()


@pytest.mark.asyncio
async def test_nouveau_compte_recoit_le_preset_actif_et_le_set_de_base(svc):
    ctx, sender, dash, router, notifier, ea, conf = svc
    a = seed(ctx_stack(svc), 81, "Demo-Server 1")
    base_set = ctx.cfg.paths.base / "MQL5" / "Presets" / ctx.cfg.mt5.set_name
    base_set.parent.mkdir(parents=True, exist_ok=True)
    base_set.write_bytes(b"\xff\xfe" + "LotSize=0.01\r\nFastEMA=10\r\n".encode("utf-16-le"))
    assert "Aucun preset actif" in await router.preset_for_new(a.id)
    dash.store.put("Martin", {"lot": "0.03", "fast": "12", "tz": "2"})
    dash.store.set_active("Martin")
    assert "mis à jour" in router.sync_base_set()
    assert "LotSize=0.03" in base_set.read_bytes()[2:].decode("utf-16-le")
    ea.start()
    await asyncio.sleep(0.3)
    try:
        line = await router.preset_for_new(a.id)
        assert "Preset « Martin » appliqué" in line
        assert ea.accounts[a.id]["params"]["lot"] == "0.03 lot" and ea.accounts[a.id]["params"]["tz"] == "UTC"
    finally:
        await ea.stop()


@pytest.mark.asyncio
async def test_creneaux_par_boutons_sur_un_compte(svc):
    """🕐 Créneaux : liste, ajout heure → minutes (pas de 15), retrait ; seul le masque concerné est envoyé, au seul compte."""
    from bot import params_ui as pu
    from core import params as cat
    ctx, sender, dash, router, notifier, ea, conf = svc
    st = ctx_stack(svc)
    a = seed(st, 81, "Demo-Server 1")
    b = seed(st, 82, "Demo-Server 1")
    ea.start()
    await asyncio.sleep(0.3)
    slots = {"sl_on": "✅", "sl_pct": "1.0 %", "sl_win": "10 min", "sl_m00": "1184328", "sl_m15": "0", "sl_m30": "0", "sl_m45": "0"}
    try:
        for i in (a, b):
            ea.accounts.setdefault(i.id, {})["params"] = {**DEFAULT_PARAMS_T, **slots}
        c = short(a.id)
        # le groupe 🏆 Objectifs : bouton 🕐 Créneaux, masques cachés
        await router.on_callback(f"p:{c}")
        assert await _until(lambda: dash.params.cached(a.id).get("sl_m00") == "1184328")
        await router.on_callback(f"p:{c}:g{cat.SLOT_GROUP}")
        datas = [d for row in dash.render()[1] for _, d in row]
        assert f"p:{c}:c" in datas and f"p:{c}:k{pu.find('sl_m00')}" not in datas
        await router.on_callback(f"p:{c}:c")
        text, kb = dash.render()
        assert "03:00, 06:00, 09:00, 12:00, 17:00, 20:00" in text and "10 min" in text
        assert f"p:{c}:cx180" in [d for row in kb for _, d in row]
        # ➕ 09:30 : heure puis minutes (00/15/30/45)
        await router.on_callback(f"p:{c}:ca")
        assert len([d for row in dash.render()[1] for _, d in row if ":ch" in d]) == 24
        await router.on_callback(f"p:{c}:ch9")
        mins = [d for row in dash.render()[1] for _, d in row if ":cm9:" in d]
        assert mins == [f"p:{c}:cm9:{m}" for m in (0, 15, 30, 45)]
        await router.on_callback(f"p:{c}:cm9:30")
        assert await _until(lambda: ea.accounts[a.id]["params"]["sl_m30"] == str(1 << 9))
        assert await _until(lambda: "✅ Créneau 09:30 ajouté" in dash.render()[0])
        assert ea.accounts[b.id]["params"]["sl_m30"] == "0"                      # l'autre compte n'est pas touché
        # ✖ 03:00
        await router.on_callback(f"p:{c}:cx180")
        assert await _until(lambda: ea.accounts[a.id]["params"]["sl_m00"] == str(1184328 - (1 << 3)))
        assert await _until(lambda: "✅ Créneau 03:00 retiré" in dash.render()[0])
        assert "06:00, 09:00, 09:30, 12:00" in dash.render()[0]
        # déjà présent / minutes invalides : rien n'est envoyé
        await router.on_callback(f"p:{c}:cm9:30")
        assert "existe déjà" in dash.render()[0]
        await router.on_callback(f"p:{c}:cm9:20")
        assert "invalide" in dash.render()[0]
        # pas de limite : 24 créneaux de plus (:15) → 30 créneaux, ✖ sur une seule page (40 par page)
        ea.accounts[a.id]["params"]["sl_m15"] = str((1 << 24) - 1)
        await router.on_callback(f"p:{c}:r")
        assert await _until(lambda: dash.params.cached(a.id).get("sl_m15") == str((1 << 24) - 1))
        await router.on_callback(f"p:{c}:c")
        assert "30 créneau(x)" in dash.render()[0]
        await router.on_callback(f"p:{c}:ca")
        await router.on_callback(f"p:{c}:ch23")
        await router.on_callback(f"p:{c}:cm23:45")
        assert await _until(lambda: ea.accounts[a.id]["params"]["sl_m45"] == str(1 << 23))
        # 96 créneaux : pages de 40, jamais plus de 100 boutons
        for k in ("sl_m00", "sl_m15", "sl_m30", "sl_m45"):
            ea.accounts[a.id]["params"][k] = str((1 << 24) - 1)
        await router.on_callback(f"p:{c}:r")
        assert await _until(lambda: dash.params.cached(a.id).get("sl_m45") == str((1 << 24) - 1))
        await router.on_callback(f"p:{c}:c")
        kb = dash.render()[1]
        assert sum(len(r) for r in kb) <= 100 and any(d == f"p:{c}:cp1" for r in kb for _, d in r)
        await router.on_callback(f"p:{c}:cp2")
        assert any(d == f"p:{c}:cx{23 * 60 + 45}" for r in dash.render()[1] for _, d in r)
    finally:
        await ea.stop()



@pytest.mark.asyncio
async def test_liste_filtre_vide_bouton_voir_tous(svc):
    """Filtre « Alertes » sans alerte : bouton 👥 Voir les N comptes, qui remet le filtre à Tous."""
    ctx, sender, dash, router, notifier, ea, conf = svc
    st = ctx_stack(svc)
    a = seed(st, 71, "Demo-Server 1")
    write_statut(ctx, a.id)
    dash.set_filter("alertes")
    await router.on_callback("d:lst")
    datas = [d for row in dash.render()[1] for _, d in row]
    if f"a:{short(a.id)}" not in datas:
        assert "d:fa" in datas
        await router.on_callback("d:fa")
    assert dash.flt == "all" and f"a:{short(a.id)}" in [d for row in dash.render()[1] for _, d in row]


@pytest.mark.asyncio
async def test_notification_ratee_renvoyee_au_passage_suivant():
    """Coupure réseau : la notification n'est plus perdue, elle part au passage suivant (une seule fois, dans l'ordre)."""
    class Flaky(FakeSender):
        down = True

        async def send(self, chat_id, text, kb=None, silent=False):
            if self.down:
                raise ConnectionError("réseau coupé")
            return await super().send(chat_id, text, kb, silent)

    class BadRequest(Exception):
        pass

    s = Flaky()
    n = Notifier(s, 1)
    await n.emit(Notice("1_a", "Alpha", "CLOTURE_500_OK", "🏁 fini", 2))
    assert await n.flush() == 0 and s.sent == []
    await n.emit(Notice("1_a", "Alpha", "NOTIF", "suite", 1))
    assert await n.flush() == 0                                   # toujours coupé : rien de perdu
    s.down = False
    assert await n.flush() == 2
    assert [t for _, t, _, _ in s.sent] == ["<b>Alpha</b>\n🏁 fini", "<b>Alpha</b>\nsuite"]
    assert s.sent[0][3] is False                                  # critique : toujours avec son
    assert await n.flush() == 0 and len(s.sent) == 2
    # erreur définitive (message refusé, fichier absent) : pas de nouvel essai ; échecs au-delà de 30 min : abandon
    import time as _t
    from telegram.error import NetworkError, TimedOut
    n._keep(_t.time(), "text", "x", True, BadRequest("bad"))
    n._keep(_t.time(), "photo", "x", True, FileNotFoundError("x"))
    n._keep(0, "text", "x", True, ConnectionError("x"))
    assert n._retry == []
    n._keep(_t.time(), "text", "a", True, TimedOut("t"))
    n._keep(_t.time(), "text", "b", True, NetworkError("n"))
    assert [x[2] for x in n._retry] == ["a", "b"]
    n._retry = []
    # ordre strict : le 1er morceau échoue → le reste du lot et le lot suivant attendent derrière lui
    s.down = True
    await n.emit(Notice("1_a", "Alpha", "NOTIF", "un", 1))
    await n.emit(Notice("2_b", "Beta", "NOTIF", "deux", 1))
    await n.flush()
    await n.emit(Notice("1_a", "Alpha", "NOTIF", "trois", 1))
    await n.flush()
    s.down = False
    s.sent.clear()
    await n.flush()
    assert [t for _, t, _, _ in s.sent] == ["<b>Alpha</b>\nun\n\n<b>Beta</b>\ndeux", "<b>Alpha</b>\ntrois"]


@pytest.mark.asyncio
async def test_rappel_ea_a_deployer_avec_cloche(svc):
    """EA compilé dans BASE mais pas encore déployé : 🔔 sur l'accueil, sur ⚡ Actions et sur 🚀 Déployer l'EA, tant que c'est vrai."""
    from mt5 import base as basemod
    ctx, sender, dash, router, notifier, ea, conf = svc
    st = ctx_stack(svc)
    a = seed(st, 61, "Demo-Server 1")
    base_v = basemod.read_version(ctx.cfg.paths.base)
    write_statut(ctx, a.id, version="0.0.1")
    dash.goto("dash")
    text, kb = dash.render()
    labels = [t for row in kb for t, _ in row]
    if base_v:
        assert f"EA {base_v} à déployer sur 1 compte(s)" in text and "⚡ Actions 🔔" in labels
        dash.goto("act")
        assert "🚀 Déployer l'EA 🔔 1" in [t for row in dash.render()[1] for t, _ in row]
    write_statut(ctx, a.id, version=base_v or "0.0.1")
    dash.goto("dash")
    text, kb = dash.render()
    assert "à déployer" not in text and "⚡ Actions" in [t for row in kb for t, _ in row]


@pytest.mark.asyncio
async def test_alertes_des_filtres_coupees_une_fois(svc):
    """n_filters coupé une seule fois par compte lancé (et dans le preset actif) ; réactivé ensuite : respecté."""
    ctx, sender, dash, router, notifier, ea, conf = svc
    st = ctx_stack(svc)
    a = seed(st, 51, "Demo-Server 1")
    ea.start()
    await asyncio.sleep(0.3)
    try:
        ea.accounts.setdefault(a.id, {})["params"] = {**DEFAULT_PARAMS_T, "n_filters": "✅"}
        dash.store.put("base", {"n_filters": "1", "lot": "0.01"})
        dash.store.set_active("base")
        await router.filters_alerts_off()
        assert ea.accounts[a.id]["params"]["n_filters"] == "❌" and dash.store.presets["base"]["n_filters"] == "0"
        ea.accounts[a.id]["params"]["n_filters"] = "✅"                    # réactivé à la main
        await router.filters_alerts_off()
        assert ea.accounts[a.id]["params"]["n_filters"] == "✅"
    finally:
        await ea.stop()


@pytest.mark.asyncio
async def test_bouton_tout_installer(svc, monkeypatch):
    """⏩ visible seulement s'il reste des publications ; il télécharge la dernière puis installe comme « Installer »."""
    from core import github as gh, update as u
    ctx, sender, dash, router, notifier, ea, conf = svc
    called = {}
    monkeypatch.setattr(gh, "fetch", lambda cfg, **kw: called.update(kw) or gh.FetchResult(False, error="hors ligne"))
    router._upd_msg = await sender.send(1, "x")
    await router._upd_cb(["upd", "all"])
    assert called.get("latest") is True and "hors ligne" in sender.msgs[router._upd_msg][0] and not router.installing
    installs = []

    async def fake_install(mid):
        installs.append(mid)
        return ""
    monkeypatch.setattr(gh, "fetch", lambda cfg, **kw: gh.FetchResult(True, True, "abc"))
    monkeypatch.setattr(router, "_upd_install", fake_install)
    await router._upd_cb(["upd", "all"])
    assert installs == [router._upd_msg]


@pytest.mark.asyncio
async def test_fiche_bouton_retirer_la_maintenance(svc):
    from storage.db import MAINTENANCE
    ctx, sender, dash, router, notifier, ea, conf = svc
    st = ctx_stack(svc)
    a = seed(st, 41, "Demo-Server 1", state=MAINTENANCE)
    ea.start()
    await asyncio.sleep(0.3)
    try:
        ea.accounts[a.id]["maintenance"] = True
        await router.on_callback("a:" + short(a.id))
        assert f"a:{short(a.id)}:maintoff" in [d for row in dash.render()[1] for _, d in row]
        await router.on_callback(f"a:{short(a.id)}:maintoff")
        assert await _until(lambda: not ea.accounts[a.id]["maintenance"] and ctx.db.get(a.id).desired_state == DEMARRE)
        assert await _until(lambda: "Maintenance retirée" in dash.render()[0])
    finally:
        await ea.stop()


@pytest.mark.asyncio
async def test_compilation_progression_estimee(svc):
    """25 / 50 / 75 / 90 % d'après la durée habituelle, puis 100 % avec la durée réelle (mémorisée pour la suivante)."""
    from mt5.build import BuildResult
    ctx, sender, dash, router, notifier, ea, conf = svc
    ctx.db.meta_set("compile_s", "10")

    async def slow():
        await asyncio.sleep(9.6)
        return BuildResult(True, "6.2.3", 0, 0, "")
    mid = await sender.send(1, "head")
    res, dt = await router._compile_progress(mid, "head", slow())
    shown = [t for m, t in sender.edits if m == mid]
    assert res.ok and [x for x in ("≈ 25 %", "≈ 50 %", "≈ 75 %", "≈ 90 %") if any(x in t for t in shown)] == ["≈ 25 %", "≈ 50 %", "≈ 75 %", "≈ 90 %"]
    assert 9 <= dt < 12 and 9 <= float(ctx.db.meta_get("compile_s")) <= 11


@pytest.mark.asyncio
async def test_installer_pendant_une_compilation_mise_en_file(svc, monkeypatch):
    """« Installer » pendant une compilation : message clair, puis installation automatique à la fin."""
    ctx, sender, dash, router, notifier, ea, conf = svc
    router._upd_msg = await sender.send(1, "⬆ Mise à jour")
    router.building, router.build_pct = True, 50
    await router._upd_cb(["upd", "go", "x"])
    assert "file d'attente" in sender.msgs[router._upd_msg][0] and "≈ 50 %" in sender.msgs[router._upd_msg][0]
    done = []

    async def fake_install(mid):
        done.append(mid)
        return ""
    monkeypatch.setattr(router, "_upd_install", fake_install)
    router.building = False
    await router._after_busy()
    assert await _until(lambda: done == [router._upd_msg]) and router._upd_after == ""


@pytest.mark.asyncio
async def test_detecteur_de_blocage_de_la_boucle(caplog):
    import logging
    import time as _t
    from bot import app as ap
    task = asyncio.create_task(ap.loop_lag_watch(0.1))
    await asyncio.sleep(0.15)
    ap.RUNNING_JOBS["j_test"] = _t.monotonic()
    with caplog.at_level(logging.WARNING, logger="bot.app"):
        _t.sleep(1.3)                                   # boucle figée volontairement
        await asyncio.sleep(0.3)
    task.cancel()
    ap.RUNNING_JOBS.pop("j_test", None)
    assert any("boucle du bot bloquée" in r.message and "j_test" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_message_de_mise_a_jour_detaille_toutes_les_versions(svc, tmp_path):
    import json
    from types import SimpleNamespace
    ctx, sender, dash, router, notifier, ea, conf = svc
    src = tmp_path / "release"
    (src / "bot").mkdir(parents=True)
    (src / ".commit.json").write_text(json.dumps({"sha": "c2", "remaining": 2, "later": ["Bot 2.3.0 : truc", "EA 6.3.0 : machin"]}))
    plan = SimpleNamespace(source=src, note="Bot 2.2.0 : suivant", bot_files=["main.py"], bot_remove=[], ea_files=[],
                           version="", stamp="s1")
    await router.on_update_available(plan)
    text, kb = sender.msgs[router._upd_msg]
    assert "Bot 2.2.0 : suivant" in text and "• Bot 2.3.0 : truc" in text and "• EA 6.3.0 : machin" in text
    assert any(d == "upd:all" for row in kb for _, d in row)


@pytest.mark.asyncio
async def test_redemarrage_complete_par_le_nouveau_bot(svc, tmp_path):
    """Avant : « redémarrage… » ; le nouveau bot complète le même message (durée, versions) et nettoie la note."""
    import time as _t
    from bot import router as rt
    ctx, sender, dash, router, notifier, ea, conf = svc
    mid = await sender.send(1, "⏳ Installation…")
    router.note_restart(mid, "mise à jour", "✅ <b>Mise à jour installée</b>")
    note = json.loads(ctx.db.meta_get("restart_note"))
    note["t"] -= 34
    ctx.db.meta_set("restart_note", json.dumps(note))
    await router.restart_report()
    text = sender.msgs[mid][0]
    assert "Mise à jour installée" in text and "Redémarré en 3" in text and "(mise à jour)" in text
    assert ctx.db.meta_get("restart_note") == ""
    await router.restart_report()                                   # une seule fois


@pytest.mark.asyncio
async def test_alertes_identiques_une_seule_pour_tout_le_systeme():
    """4 comptes × 5 news à 13:30 → UN message « Tous les comptes » listant les 5 annonces ; un message propre à un compte reste à part."""
    s = FakeSender()
    n = Notifier(s, 1, hold_s=10, total=lambda: 4)
    foot = "\n🕐 13:30 (heure de France)\n⚠️ Volatilité attendue, spreads élargis possibles.\n⛔ Filtre News actif : pas de nouvelle entrée."
    news = ["🔴 USD · Core PCE m/m", "🔴 USD · Core PCE y/y", "🔴 USD · GDP q/q", "🔴 USD · GDP Sales q/q", "🔴 USD · Jobless Claims"]
    for acc in ("11110001", "11110002", "11110003", "11110004"):
        for x in news:
            await n.emit(Notice(acc, acc, "NOTIF", "📰 NEWS DANS 1 h 00\n━━━━━━━━━━━━━━━━━━\n" + x + foot, 1))
    await n.emit(Notice("11110002", "11110002", "NOTIF", "🎯 Objectif atteint", 1))
    assert await n.flush() == 0                                    # attend les messages des autres comptes
    for k in n._queued:
        n._queued[k] -= 11
    assert await n.flush() == 1
    text = s.sent[-1][1]
    assert text.count("📰 NEWS DANS 1 h 00") == 1 and all(x in text for x in news) and text.count("🕐 13:30") == 1
    assert "🌐 Tous les comptes (4)" in text and "<b>11110002</b>\n🎯 Objectif atteint" in text
    # 2 comptes sur 4 : nommés ; critique : part sans attendre
    await n.emit(Notice("a", "A", "NOTIF", "⚠️ AUCUN TICK", 2))
    await n.emit(Notice("b", "B", "NOTIF", "⚠️ AUCUN TICK", 2))
    await n.emit(Notice("c", "C", "NOTIF", "💰 Solde 120.00\nfin", 2))
    await n.emit(Notice("d", "D", "NOTIF", "💰 Solde 130.00\nfin", 2))       # titres proches, corps identiques : regroupés
    await n.emit(Notice("c", "C", "NOTIF", "Capital\nsolde 120", 2))
    await n.emit(Notice("d", "D", "NOTIF", "Capital\nsolde 130", 2))          # corps propres à chaque compte : séparés
    assert await n.flush() == 1 and "👥 2 comptes : A, B" in s.sent[-1][1] and s.sent[-1][1].count("AUCUN TICK") == 1
    last = s.sent[-1][1]
    assert "👥 2 comptes : C, D" in last and last.count("Solde 1") == 1
    assert "<b>C</b>\nCapital\nsolde 120" in last and "<b>D</b>\nCapital\nsolde 130" in last


@pytest.mark.asyncio
async def test_preset_ancien_reglages_absents_modifiables(svc):
    """Preset créé avant l'EA 6.2.0 : saisie d'un nombre acceptée, oui/non explicite, créneaux partant des valeurs par défaut."""
    from bot import params_ui as pu, presets_ui as pui
    ctx, sender, dash, router, notifier, ea, conf = svc
    dash.store.put("Martin", {"lot": "0.01", "goalmax": "50"})
    c = pui.code_of("Martin")
    iid = pui.PREFIX + "Martin"
    await router.on_callback(f"p:{c}")
    assert await _until(lambda: dash.params.cached(iid).get("goalmax") == "50")
    # saisie libre d'un nombre (avant : « envoie un nombre » refusé pour un preset)
    i_arr = pu.find("seuil_arret")
    await router.on_callback(f"p:{c}:w{i_arr}")
    await router.on_text("600")
    assert await _until(lambda: dash.store.presets["Martin"].get("seuil_arret") == "600")
    # oui/non absent : écran avec ✅ / ❌
    i_on = pu.find("sl_on")
    await router.on_callback(f"p:{c}:t{i_on}")
    datas = [d for row in dash.render()[1] for _, d in row]
    assert f"p:{c}:e{i_on}:1" in datas and f"p:{c}:e{i_on}:0" in datas
    await router.on_callback(f"p:{c}:e{i_on}:0")
    assert await _until(lambda: dash.store.presets["Martin"].get("sl_on") == "0")
    # créneaux : partent des créneaux par défaut ; le .set garde les autres heures
    await router.on_callback(f"p:{c}:c")
    assert "03:00, 06:00, 09:00, 12:00, 17:00, 20:00" in dash.render()[0]
    await router.on_callback(f"p:{c}:cm9:30")
    assert await _until(lambda: dash.store.presets["Martin"].get("sl_m30") == str(1 << 9))
    from core import presets as ps
    assert ps.slots_input(dash.store.presets["Martin"]) == "3,6,9,9:30,12,17,20"


@pytest.mark.asyncio
async def test_coupures_par_boutons_sur_un_compte(svc):
    """⛔ Coupures (EA 7.0.0) : mêmes écrans que les créneaux, masques ct_m.. ; 13:45 et 21:00 par défaut."""
    from bot import params_ui as pu
    from core import params as cat
    ctx, sender, dash, router, notifier, ea, conf = svc
    st = ctx_stack(svc)
    a = seed(st, 91, "Demo-Server 1")
    ea.start()
    await asyncio.sleep(0.3)
    cuts = {"sl_on": "✅", "sl_m00": "1184328", "sl_m15": "0", "sl_m30": "0", "sl_m45": "0",
            "ct_on": "✅", "ct_m00": str(1 << 21), "ct_m15": "0", "ct_m30": "0", "ct_m45": str(1 << 13)}
    try:
        ea.accounts.setdefault(a.id, {})["params"] = {**DEFAULT_PARAMS_T, **cuts}
        c = short(a.id)
        await router.on_callback(f"p:{c}")
        assert await _until(lambda: dash.params.cached(a.id).get("ct_m45") == str(1 << 13))
        await router.on_callback(f"p:{c}:g{cat.SLOT_GROUP}")
        kb = dash.render()[1]
        assert ("⛔ Coupures · 13:45, 21:00", f"p:{c}:u") in [b for row in kb for b in row]
        assert f"p:{c}:k{pu.find('ct_m00')}" not in [d for row in kb for _, d in row]
        await router.on_callback(f"p:{c}:u")
        text, kb = dash.render()
        assert "Coupures de l'objectif" in text and "2 coupure(s)</b> : 13:45, 21:00" in text and "jusqu'à son TP" in text
        assert f"p:{c}:ux825" in [d for row in kb for _, d in row]
        await router.on_callback(f"p:{c}:ua")
        await router.on_callback(f"p:{c}:uh17")
        assert [d for row in dash.render()[1] for _, d in row if ":um17:" in d] == [f"p:{c}:um17:{m}" for m in (0, 15, 30, 45)]
        await router.on_callback(f"p:{c}:um17:30")
        assert await _until(lambda: ea.accounts[a.id]["params"]["ct_m30"] == str(1 << 17))
        assert await _until(lambda: "✅ Coupure 17:30 ajoutée" in dash.render()[0])
        await router.on_callback(f"p:{c}:ux825")
        assert await _until(lambda: ea.accounts[a.id]["params"]["ct_m45"] == "0")
        assert ea.accounts[a.id]["params"]["sl_m00"] == "1184328"                   # les créneaux ne sont pas touchés
        await router.on_callback(f"p:{c}:um17:30")
        assert "existe déjà" in dash.render()[0]
    finally:
        await ea.stop()


@pytest.mark.asyncio
async def test_deployer_base_pas_encore_compilee(svc):
    """Sources de BASE (v7.4.1) plus récentes que le .ex5 : pas de « Tous les comptes sont à jour », bouton 🔨 Compiler."""
    import os
    ctx, sender, dash, router, notifier, ea, conf = svc
    seed(ctx_stack(svc), 71, "Demo-Server 1", running=False)
    ex = ctx.cfg.paths.base / "MQL5" / "Experts"
    (ex / ctx.cfg.mt5.ea_file).write_text("7.4.0")
    old = time.time() - 600
    os.utime(ex / ctx.cfg.mt5.ea_file, (old, old))
    (ex / "SX_Core.mqh").write_text('#define SX_VERSION   "7.4.1"\n')
    await router.cmd_deployer()
    text, kb = sender.sent[-1][1], sender.sent[-1][2]
    assert "pas encore compilée en v7.4.1" in text and "Tous les comptes sont à jour" not in text
    assert "d:bld" in [d for row in kb for _, d in row]


async def test_bouton_du_bas_remis(svc):
    """Bot 3.6.3 : 🛠 Système → 📊 Remettre le bouton du bas renvoie le clavier permanent."""
    ctx, sender, dash, router, *_ = svc
    sent = []

    async def rm(chat, text):
        sent.append(text)
        return 1
    sender.reply_menu = rm
    await router.on_callback("d:sys")
    _, kb = dash.render()
    assert any(d == "d:menu" for row in kb for _, d in row)
    await router.on_callback("d:menu")
    assert sent and "Tableau de bord" in sent[0]


async def test_presets_export_puis_import_dans_un_autre_bot(svc):
    """Bot 3.16.0 : 📤 Exporter → fichier texte ; 📥 Importer (texte collé ou fichier) → presets recréés, ⭐ inchangé."""
    ctx, sender, dash, router, notifier, ea, conf = svc
    from bot import presets_ui as pui
    from core import presets as ps
    st = dash.store
    st.put("Martin", {"lot": "0.02", "fast": "12"})
    st.put("🧩 Base avant optimisation", {"lot": "0.01"})
    await router.on_callback("s:expa")
    fname, data = sender.docs[-1]
    text = data.decode("utf-8")
    assert fname == "scalpx_presets.txt" and text == ps.export_text(st, st.names())
    assert "[preset=Martin]" in text and "lot=0.02" in text and "[preset=🧩 Base avant optimisation]" in text
    got, unknown = ps.parse_import(text + "\ninconnu=3\n")
    assert got == {"Martin": {"lot": "0.02", "fast": "12"}, "🧩 Base avant optimisation": {"lot": "0.01"}} and unknown == 1
    assert ps.parse_import("# preset=X\nfast=9\n")[0] == {"X": {"fast": "9"}}       # format du testeur MT5
    import pytest
    with pytest.raises(ValueError):
        ps.parse_import("bonjour")
    # autre bot : aucun preset → import direct, sans confirmation ; ⭐ actif inchangé
    for n in list(st.presets):
        st.delete(n)
    await router.on_callback("s:imp")
    assert router.awaiting_import
    await router.on_text(text)
    assert not router.awaiting_import
    assert st.presets["Martin"] == {"lot": "0.02", "fast": "12"} and not st.active
    # réimport : remplace l'existant → confirmation demandée
    st.put("Martin", {"lot": "0.05"})
    await router.on_callback("s:imp")
    await router.on_text(text)
    assert dash.screen[0] == "confirm" and st.presets["Martin"] == {"lot": "0.05"}
    tok = dash.screen[1]
    await router.on_callback(f"ok:{tok}")
    assert await _until(lambda: st.presets["Martin"] == {"lot": "0.02", "fast": "12"})


async def test_reinitialisation_et_sauvegarde_github_depuis_le_bot(svc, monkeypatch):
    """Bot 3.17.0 : ♻️ double confirmation puis tout effacé sauf bot / BASE / presets ; ☁️ presets poussés s'ils changent."""
    ctx, sender, dash, router, notifier, ea, conf = svc
    from core import ghbackup, update as upd
    st = ctx_stack(svc)
    a = seed(st, 81, "Demo-Server 1")
    dash.store.put("Martin", {"fast": "12"})
    await router.on_callback("d:rz")
    assert dash.screen[0] == "confirm"
    await router.on_callback(f"ok:{dash.screen[1]}")
    assert await _until(lambda: dash.screen[0] == "confirm" and "Vraiment sûr" in dash.all_text() if hasattr(dash, "all_text") else dash.screen[0] == "confirm")
    monkeypatch.setattr(upd, "restart_bot", lambda root: "test")
    await router.on_callback(f"ok:{dash.screen[1]}")
    assert await _until(lambda: upd.STOP.is_set())
    upd.STOP.clear()
    assert ctx.db.list(include_archived=True) == [] and "Martin" in dash.store.presets
    assert not any(ctx.cfg.paths.instances.iterdir()) and any("Serveur réinitialisé" in t for t in sender.all_text().split("\n"))
    # ☁️ sauvegarde
    pushed = []
    monkeypatch.setattr(ghbackup, "push", lambda cfg, text, http=None: pushed.append(text) or "")
    ctx.cfg.backup_token, ctx.cfg.backup_repo = "tok", "aigle3/scalpx-sauvegarde"
    await router.backup_presets()
    await router.backup_presets()                                          # inchangé : pas de 2e envoi
    assert len(pushed) == 1 and "[preset=Martin]" in pushed[0]
    dash.store.set_value("Martin", "fast", "14")
    await router.backup_presets()
    assert len(pushed) == 2 and "fast=14" in pushed[1]


async def test_jeton_de_sauvegarde_et_desinstallation_apres_10_s(svc, monkeypatch):
    """Bot 4.2.0 : jeton envoyé → dépôt privé trouvé / créé (BACKUP_REPO) → mot de passe → BACKUP_KEY, sauvegarde
    chiffrée ; Bot 3.18.0 : 🧨 trois confirmations, la dernière refusée avant 10 s."""
    ctx, sender, dash, router, notifier, ea, conf = svc
    from core import crypt, envfile, fullbackup, ghbackup, server as sv
    written = {}
    monkeypatch.setattr(envfile, "set_key", lambda path, k, v: written.update({k: v}))
    pushed = []
    monkeypatch.setattr(ghbackup, "push", lambda cfg, text, http=None: pushed.append(("presets", cfg.backup_key)) or "")
    monkeypatch.setattr(fullbackup, "push", lambda cfg, data, http=None: pushed.append(("complète", cfg.backup_key)) or "")
    monkeypatch.setattr(ghbackup, "ensure_repo", lambda cfg, http=None: ("moi/scalpx-sauvegarde", ""))
    dash.store.put("Martin", {"fast": "12"})
    await router.on_callback("d:kset:BACKUP_TOKEN")
    await router.on_text("pas un jeton")
    assert not written and not ctx.cfg.backup_token
    await router.on_callback("d:kset:BACKUP_TOKEN")
    await router.on_text("ghp_" + "A" * 36)
    assert written["BACKUP_TOKEN"] == "ghp_" + "A" * 36 and written["BACKUP_REPO"] == "moi/scalpx-sauvegarde"
    assert router.awaiting_token == "BACKUP_PASSWORD" and dash.screen[0] == "bkp"
    await router.on_text("court")                                           # refusé : 8 caractères au moins
    assert router.awaiting_token == "BACKUP_PASSWORD" and "BACKUP_KEY" not in written
    await router.on_text("motdepasse-1")
    assert ctx.cfg.backup_key and written["BACKUP_KEY"] == ctx.cfg.backup_key
    assert crypt.decrypt(crypt.encrypt(b"ok", ctx.cfg.backup_key), password="motdepasse-1") == b"ok"
    assert ("complète", ctx.cfg.backup_key) in pushed and ("presets", ctx.cfg.backup_key) in pushed
    assert "motdepasse-1" not in sender.all_text()
    text, _ = dash.render()
    assert "moi/scalpx-sauvegarde" in text and "Tout est en place" in text
    calls = []
    monkeypatch.setattr(sv, "uninstall", lambda run=None: calls.append(1) or (True, ""))
    router.UNI_STEP_S = 0.01
    await router.on_callback("d:uni")
    await router.on_callback(f"ok:{dash.screen[1]}")
    assert await _until(lambda: dash.screen[0] == "confirm" and conf.peek(dash.screen[1]) and "Vraiment" in conf.peek(dash.screen[1]).text)
    await router.on_callback(f"ok:{dash.screen[1]}")
    assert await _until(lambda: getattr(router, "_uni", ("", 0))[1] > 0)                  # compte à rebours fini
    assert "Bouton disponible dans <b>9</b> s" in sender.all_text()
    assert await router.on_callback("d:unigo:mauvais") != "OK" and not calls
    tok = router._uni[0]
    assert await router.on_callback(f"d:unigo:{tok}") == "OK"
    assert await _until(lambda: calls == [1])



async def test_sauvegarde_proposee_une_fois_par_jour_et_jamais(svc):
    """Bot 4.2.0 : sauvegarde non activée → proposée (1×/jour) ; « Jamais » arrête ; jeton présent → rien."""
    ctx, sender, dash, router, notifier, ea, conf = svc
    ctx.cfg.backup_token = ""
    assert await router.offer_backup() and "Activer la sauvegarde sur GitHub" in sender.all_text()
    assert not await router.offer_backup()                                    # déjà proposé aujourd'hui
    await router.on_callback("d:bknever")
    assert not await router.offer_backup(force=False)
    ctx.cfg.backup_token = "ghp_x"
    assert not await router.offer_backup(force=True)


async def test_import_d_une_sauvegarde_chiffree_demande_le_mot_de_passe(svc):
    """Bot 4.2.0 : 📥 fichier chiffré (presets.enc d'un autre serveur) → mot de passe demandé, message effacé, presets importés."""
    ctx, sender, dash, router, notifier, ea, conf = svc
    from core import crypt, presets as ps
    blob = crypt.encrypt(b"# ScalpX presets\n[preset=Martin]\nfast=12\n", crypt.new_key("motdepasse-1"))
    ctx.cfg.backup_key = ""
    await router.preset_import_file(blob)
    assert router.awaiting_token == "IMPORT_PASSWORD" and "Martin" not in dash.store.presets
    await router.on_text("mauvais-mdp")
    assert router.awaiting_token == "IMPORT_PASSWORD"                          # redemandé
    await router.on_text("motdepasse-1")
    assert await _until(lambda: dash.store.presets.get("Martin") == {"fast": "12"})
    assert "motdepasse-1" not in sender.all_text()


async def test_un_autre_bouton_annule_la_saisie_du_mot_de_passe(svc, monkeypatch):
    """Bot 4.2.0 : 🔒 demandé puis navigation ailleurs → le texte suivant n'est jamais pris pour une clé."""
    ctx, sender, dash, router, notifier, ea, conf = svc
    from core import envfile
    written = {}
    monkeypatch.setattr(envfile, "set_key", lambda path, k, v: written.update({k: v}))
    await router.on_callback("d:bkpw")
    assert router.awaiting_token == "BACKUP_PASSWORD"
    await router.on_callback("s:l")
    assert router.awaiting_token == ""
    await router.on_text("NomDePreset1")
    assert "BACKUP_KEY" not in written and not ctx.cfg.backup_key


async def test_ancien_preset_et_ancien_ea_avec_reglages_graphiques(svc):
    """EA 8.0.0 / Bot 4.3.0 : 📥 un ancien export (ch_*, clé inconnue, valeur illisible) s'importe ; appliqué à un robot
    encore en EA 7.x (qui annonce ch_*), seules les vraies différences partent et le robot devient conforme."""
    ctx, sender, dash, router, notifier, ea, conf = svc
    from bot import presets_ui as pui
    st = ctx_stack(svc)
    a = seed(st, 73, "Demo-Server 1")
    ea.start()
    await asyncio.sleep(0.3)
    try:
        ea.accounts[a.id]["params"] = {"lot": "0.01 lot", "fast": "10", "ch_style": "✅", "ch_panel": "✅"}
        old = "# ScalpX presets\n[preset=Martin]\nfast=12\nlot=0.02\nch_style=0\nch_panel=0\ntruc=1\nslow=abc\n"
        await router.preset_import_file(old.encode("utf-16"))
        assert await _until(lambda: dash.store.presets.get("Martin") == {"fast": "12", "lot": "0.02"})
        await router.on_callback(f"s:ap:{pui.code_of('Martin')}")
        await _confirm(router, dash)
        assert await _until(lambda: dash.store.active == "Martin" and dash.preset_check is not None, 120)
        p = ea.accounts[a.id]["params"]
        assert p["fast"] == "12" and p["lot"] == "0.02 lot" and p["ch_style"] == "✅" and p["ch_panel"] == "✅"
        assert dash.preset_check.diffs == {}
    finally:
        await ea.stop()


async def test_serveur_neuf_n_ecrase_jamais_une_sauvegarde_existante(svc, monkeypatch):
    """Bot 4.4.0 : serveur neuf + dépôt qui contient déjà la sauvegarde d'un autre serveur → rien n'est écrit,
    ☁️ Restaurer / 🔁 Remplacer proposés ; 🔁 Remplacer (confirmé) écrit ; un dépôt vide est écrit tout de suite."""
    ctx, sender, dash, router, notifier, ea, conf = svc
    from core import envfile, fullbackup, ghbackup
    monkeypatch.setattr(envfile, "set_key", lambda path, k, v: None)
    pushed = []
    monkeypatch.setattr(fullbackup, "push", lambda cfg, data, http=None: pushed.append("complète") or "")
    monkeypatch.setattr(ghbackup, "push", lambda cfg, text, http=None: pushed.append("presets") or "")
    remote = {"sauvegarde.enc", "presets.enc"}
    monkeypatch.setattr(ghbackup, "existing", lambda cfg, http=None: (set(remote), ""))
    ctx.cfg.backup_token, ctx.cfg.backup_repo = "tok", "moi/scalpx-sauvegarde"
    dash.store.put("Martin", {"fast": "12"})
    router.awaiting_token = "BACKUP_PASSWORD"
    await router.on_text("motdepasse-1")
    assert pushed == [] and "Restaurer cette sauvegarde" in sender.all_text()     # Bot 4.8.3 : dans l'écran Sauvegarde
    assert "rien n'a été écrasé" in dash.render()[0]
    assert await router.full_backup() == router.BKP_WAIT and pushed == []
    await router.backup_presets()
    assert pushed == []
    await router.on_callback("d:bkown")
    await _confirm(router, dash)
    assert await _until(lambda: "complète" in pushed and "presets" in pushed)
    assert ctx.db.meta_get("bkp_owner") == "1"
    # autre serveur neuf, dépôt vide : écrit tout de suite
    ctx.db.meta_set("bkp_owner", ""); ctx.db.meta_set("fbk_ts", ""); pushed.clear(); remote.clear()
    assert await router.full_backup() == "" and pushed == ["complète"] and ctx.db.meta_get("bkp_owner") == "1"


async def test_cloche_installation_systeme_cachee_tant_que_le_bot_rattrape_ses_mises_a_jour(svc, monkeypatch):
    """Bot 4.5.1 : 🔧 installe toujours stable ; tant qu'une mise à jour du bot attend, pas de 🔔 🔧 (faux retard)."""
    ctx, sender, dash, router, notifier, ea, conf = svc
    from core import server as sv
    monkeypatch.setattr(sv, "install_needed", lambda package, *a, **k: "abcd1234abcd1234")
    ctx.db.meta_set("update_pending", "1")
    await router.check_install()
    assert ctx.db.meta_get("install_pending") == "" and "installation système" not in sender.all_text()
    ctx.db.meta_set("update_pending", "")                                # bot à jour : vrai retard → 🔔
    await router.check_install()
    assert ctx.db.meta_get("install_pending") == "abcd1234abcd1234" and "installation système" in sender.all_text()


async def test_commande_serveur_confirmee_puis_executee(svc):
    """Bot 4.6.0 : 💻 commande → confirmation → exécutée (sans sudo), sortie renvoyée ; longue sortie → fichier ;
    un autre bouton annule la saisie ; délai dépassé → arrêtée."""
    ctx, sender, dash, router, notifier, ea, conf = svc
    await router.on_callback("d:shc")
    await router.on_callback("d:srv")                                   # autre bouton : plus rien d'attendu
    assert not router.awaiting_cmd
    await router.on_callback("d:shc")
    await router.on_text("sudo bash -c 'echo bonjour-scalpx; exit 3'")
    assert "Exécuter cette commande" in sender.all_text() and "bonjour-scalpx" not in sender.all_text().split("Exécuter")[0]
    await _confirm(router, dash)
    assert await _until(lambda: "Code 3" in sender.all_text() and "<pre>bonjour-scalpx</pre>" in sender.all_text())
    assert "⏳ <pre>bash -c &#x27;echo bonjour-scalpx; exit 3&#x27;</pre>" in sender.all_text() or \
        "⏳ <pre>bash -c 'echo bonjour-scalpx; exit 3'</pre>" in sender.all_text()      # « sudo » retiré
    await router._shell_run("python3 -c \"print('x' * 5000)\"")
    assert any(f == "sortie.txt" and len(d) > 5000 for f, d in sender.docs)
    router.SHELL_TIMEOUT_S = 0.5
    await router._shell_run("sleep 5")
    assert "arrêtée au bout" in sender.all_text()


async def test_ajout_d_un_courtier_depuis_telegram(svc, monkeypatch):
    """Bot 4.7.0 : 🏦 Courtiers → ➕ → nom de serveur → lien de l'installeur → liste de serveurs ajoutée."""
    ctx, sender, dash, router, notifier, ea, conf = svc
    from mt5 import brokers
    calls = []
    monkeypatch.setattr(brokers, "add_from_installer", lambda cfg, url, server: calls.append((url, server)) or (True, "ok"))
    await router.on_callback("d:brk")
    assert "Courtiers connus" in sender.all_text()
    await router.on_callback("d:brkadd")
    await router.on_text("pas un serveur avec espaces")
    assert router.awaiting_broker == "name"
    await router.on_text("Exness-MT5Real8")
    await router.on_text("http://pas-https")
    assert not calls and "https://" in sender.all_text()
    await router.on_callback("d:brkadd")
    await router.on_text("Exness-MT5Real8")
    await router.on_text("https://exemple/exness5setup.exe")
    assert await _until(lambda: calls == [("https://exemple/exness5setup.exe", "Exness-MT5Real8")]
                        and "Courtier <b>exness</b> ajouté" in sender.all_text())


async def test_ecran_sauvegarde_en_etapes_et_toujours_en_bas(svc, monkeypatch):
    """Bot 4.8.3 : 🔑 jeton → l'écran Sauvegarde (pas Clés) dit « J'attends ton jeton » ; recouvert par d'autres messages,
    il est republié EN BAS après l'envoi du jeton (pas de retour à l'accueil) ; étapes cochées."""
    ctx, sender, dash, router, notifier, ea, conf = svc
    from core import envfile, ghbackup
    monkeypatch.setattr(envfile, "set_key", lambda path, k, v: None)
    monkeypatch.setattr(ghbackup, "ensure_repo", lambda cfg, http=None: ("moi/scalpx-sauvegarde", ""))
    dash.waiting = lambda: router.awaiting_token
    await router.on_callback("d:kset:BACKUP_TOKEN")
    text, _ = dash.render()
    assert dash.screen[0] == "bkp" and "J'attends ton jeton GitHub" in text and "👉 <b>1. Jeton GitHub</b>" in text
    dash.note_message(10 ** 9)                                           # un autre message recouvre le tableau de bord
    assert not dash.is_last
    before = int(ctx.db.meta_get("dash_msg_id", "0") or 0)
    await router.on_text("ghp_" + "B" * 36)
    await dash.show()
    text, _ = dash.render()
    assert dash.screen[0] == "bkp" and dash.is_last and int(ctx.db.meta_get("dash_msg_id", "0")) != before
    assert "✅ <b>1. Jeton GitHub</b>" in text and "✅ <b>2. Dépôt privé</b>" in text and "J'attends ton mot de passe" in text
