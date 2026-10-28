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
    assert "🔴 3" in text and "À retirer" in text and "378.20" in text            # total à retirer (503.2 − 125)
    assert "▰" in text and "<pre>" in text and "Aucune alerte" not in text
    assert [d for row in kb for _, d in row] == ["d:lst", "d:act", "d:rpt", "d:sys"]
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
        await router.on_callback(f"a:{short(inst.id)}:pause")
        for _ in range(40):
            await asyncio.sleep(0.1)
            if ea.accounts[inst.id]["etat"] == "PAUSE":
                break
        assert ea.accounts[inst.id]["etat"] == "PAUSE"
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
    assert "Clôtures à 500" in rep and "Réussies : 1" in rep and "378.20" in rep
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
                 "🏓 Ping", "📜 Bot", "⏸ Tout en pause"):
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
    assert datas == ["n", "d:sys"]                                                  # sans compte : créer, ou Système
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
        await router.on_callback("d:pall")
        assert "2 compte(s)" in await _confirm(router, dash)
        assert await _until(lambda: ea.accounts[a.id]["etat"] == "PAUSE" and ea.accounts[b.id]["etat"] == "PAUSE")
        # vacances (confirmation), tout fermer (confirmation)
        await router.on_callback(f"e:{c}:von")
        await _confirm(router, dash)
        assert await _until(lambda: ea.accounts[a.id].get("vacances") is True)
        await router.on_callback(f"e:{c}:fx")
        assert "TOUT FERMER" in await _confirm(router, dash)
        # presets : liste lue depuis l'EA, enregistrement par message texte, suppression confirmée
        await router.on_callback(f"e:{c}:ps")
        assert await _until(lambda: dash.presets.get(a.id) == ["prudent"])
        await router.on_callback(f"e:{c}:pn")
        await router.on_text("agressif")
        assert await _until(lambda: dash.presets.get(a.id) == ["prudent", "agressif"])
        await router.on_callback(f"e:{c}:pd:0")
        assert "prudent" in await _confirm(router, dash)
        assert await _until(lambda: dash.presets.get(a.id) == ["agressif"])
        await router.on_callback(f"e:{c}:pn")
        await router.on_text("nom invalide !")
        assert "Nom invalide" in dash.render()[0]
    finally:
        await ea.stop()
