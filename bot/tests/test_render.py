"""Accueil : bloc « état du trading » (EA 5.5.0+) en tout premier."""
from __future__ import annotations

import time

from bot import render
from core.health import Health
from core.models import Statut
from core.summary import AccountView, totals
from storage.db import Instance

FILTRES = [
    {"nom": "Volatilité (ATR)", "actif": True, "ok": True, "info": "120 pts", "raison": ""},
    {"nom": "Volume", "actif": True, "ok": False, "info": "40 %", "raison": "Volume trop faible"},
    {"nom": "Horaire", "actif": False, "ok": True, "info": "", "raison": ""},
    {"nom": "News", "actif": True, "ok": True, "info": "", "raison": ""},
]


def view(login: int, **st) -> AccountView:
    inst = Instance(id=f"{login}_Demo", login=login, server="Demo", alias=str(login), folder="", created_at=0,
                    ea_version="", desired_state="DEMARRE")
    base = dict(ts=int(time.time()), devise="EUR", objectif_actif=True, ea_demarre=True, etat="ACTIF",
                trading_autorise=True, filtres=FILTRES, news_filtre=True)
    base.update(st)
    return AccountView(inst, Statut(**base), Health("🟢", "OK"), True, False)


def block(*views) -> str:
    return "\n".join(render.trading_block(list(views), time.time()))


def test_autorise_filtres_et_news():
    news = [{"heure": time.strftime("%Y.%m.%d") + " 14:30", "dans_min": 95, "devise": "USD", "nom": "Non-Farm Payrolls", "haute": True},
            {"heure": time.strftime("%Y.%m.%d") + " 16:00", "dans_min": 185, "devise": "USD", "nom": "ISM", "haute": False}]
    txt = block(view(1, signal="achat", news=news), view(2))
    assert txt.startswith("🟡 <b>En attente</b>") and "✅ Entrée au prochain signal (achat)" in txt
    assert "🛡 Filtres <b>2/3 OK</b>\n" in txt and "Horaire" not in txt                 # désactivé : pas affiché and "   🔴 Volume : Volume trop faible" in txt
    assert "14:30 USD Non-Farm Payrolls (dans 1 h 35) · +1 autre(s)" in txt and "ISM" not in txt
    assert "Aucune position" in txt


def test_news_bloque_et_positions():
    txt = block(view(1, trading_autorise=False, raison_blocage="News : USD NFP (14:30)", news_bloque=True,
                     news_raison="USD NFP (14:30)", positions=3, flottant_ea=-4.5),
                view(2, positions=0))
    assert txt.startswith("🟢 <b>En train de trader</b>")
    assert "⛔ Nouvelles séries <b>bloquées</b> : News : USD NFP (14:30)" in txt
    assert "<b>NEWS EN COURS</b> : USD NFP (14:30)" in txt
    assert "📌 3 position(s) · flottant -4.50 EUR" in txt


def test_filtre_news_desactive_pause_et_vieil_ea():
    assert "Filtre News <b>désactivé</b>" in block(view(1, news_filtre=False))
    txt = block(view(1, etat="PAUSE", ea_demarre=False, pause_jusqua=time.strftime("%Y.%m.%d") + " 16:00"))
    assert txt.startswith("⏸ <b>En pause</b> jusqu'à 16:00")
    assert block(view(1, objectif_actif=False, ea_demarre=False)).startswith("⚪ <b>À l'arrêt</b>")
    old = block(view(1, filtres=None, trading_autorise=None))                     # EA < 5.5.0
    assert "après la mise à jour de l'EA" in old
    assert "aucune donnée" in block(view(1, ts=int(time.time()) - 600))


def test_accueil_compact_trading_en_titre():
    v = [view(1)]
    txt = render.home_text(v, totals(v), {}, "EUR", "2.4.0", "5.5.0", False, 1.0, time.time())
    lines = txt.splitlines()
    assert "🟡 <b>En attente</b>" in lines[0]
    assert txt.index("En attente") < txt.index("💰")
    assert "<i>Bot 2.4.0 · EA 5.5.0</i>" in txt and "🟢 1/1 · ✅ aucune alerte · 🖥 VPS OK" in txt


def test_objectif_en_cours_et_barres_des_comptes():
    a = view(1, objectif_depart=200.0, objectif_cible=210.0, objectif_progression=60.0, solde=206.0)
    b = view(2, objectif_actif=False, solde=250.0, solde_plus_flottant_ea=250.0, seuil_arret=500)
    txt = render.home_text([a, b], totals([a, b]), {}, "EUR", "2", "6", False, 1.0, time.time())
    assert "🎯 <b>Objectif 1/2 comptes</b> : +6.00 / +10.00 EUR ▰▰▰▱▱ 60 %" in txt
    assert "▰▰▰▱▱  60% 🎯" in txt                                                   # compte en objectif : barre de l'objectif
    assert "250.00 ▰▰▱▱▱  33%" in txt                                               # sans objectif : de 125 (reprise) à 500
    assert "🎯 <b>Objectif</b> : +1.00 / +2.00 EUR" in render.goal_line(
        [view(1, objectif_depart=100.0, objectif_cible=102.0, objectif_progression=50)], None, time.time())
    c = view(1, objectif_actif=False, ea_demarre=False, solde=100.0, solde_plus_flottant_ea=100.0, seuil_arret=500,
             prochain_creneau=time.strftime("%Y.%m.%d") + " 09:00")
    txt = render.home_text([c], totals([c]), {}, "EUR", "2", "6", False, 1.0, time.time())
    assert "🏁 <b>Progression vers 500</b> · prochain créneau 09:00" in txt and "🎯" not in txt


def test_comptes_a_500_listes_avec_le_montant_a_retirer():
    a = view(1, objectif_actif=False, etat="ARRET_500", solde=512.0, seuil_arret=500, seuil_reprise=125)
    b = view(2, objectif_actif=False, solde=200.0)
    txt = render.home_text([a, b], totals([a, b]), {}, "EUR", "2", "6", False, 1.0, time.time())
    assert "🏁 <b>1 compte(s) à 500</b> · à retirer <b>387.00 EUR</b>" in txt
    assert "💸 1 : <b>387.00 EUR</b> (laisser 125)" in txt
    assert txt.splitlines()[0].endswith("⚪ <b>À l'arrêt</b> : attend un objectif")    # un seul compte à 500 : pas le titre


def test_menu_retraits_et_rappel():
    now = time.time()
    a = view(1, objectif_actif=False, etat="ARRET_500", solde=512.0, seuil_arret=500, seuil_reprise=125)
    b = view(2, objectif_actif=False, solde=430.0, solde_plus_flottant_ea=430.0, seuil_arret=500)
    c = view(3, objectif_actif=False, solde=200.0, solde_plus_flottant_ea=200.0, seuil_arret=500)
    hist = [{"ts": int(now) - 86400, "iid": "2_Demo", "amount": 380.5}]
    txt = render.withdraw_text([a, b, c], hist, 380.5, 380.5, {"1_Demo": int(now) - 7200}, "EUR", now, {"2_Demo": "deux"})
    assert "À retirer maintenant</b> (1 compte(s)) : <b>387.00 EUR</b>" in txt
    assert "512.00 →   387.00 · 2 h 00" in txt and "tout seul" in txt
    assert "Bientôt à 500" in txt and "430.00" in txt and "200.00" not in txt
    assert "deux" in txt and "380.50" in txt and "Total retiré : <b>380.50 EUR</b>" in txt
    r = render.withdraw_reminder([a, b, c], {"1_Demo": int(now) - 2 * 86400}, now)
    assert "Rappel : 1 compte(s) à 500" in r and "1 : <b>387.00 EUR</b> (depuis 2 j 0 h)" in r
    assert render.withdraw_reminder([b, c], {}, now) is None
    empty = render.withdraw_text([c], [], 0.0, 0.0, {}, "EUR", now, {})
    assert "Aucun compte à 500" in empty and "aucun retrait enregistré" in empty


def test_accueil_comptes_du_plus_avance_au_moins_avance():
    vs = [view(n, objectif_actif=False, ea_demarre=False, solde=b, solde_plus_flottant_ea=b, seuil_arret=500)
          for n, b in ((11, 150.0), (22, 290.0), (33, 125.0), (44, 228.0))]
    txt = render.home_text(vs, totals(vs), {}, "EUR", "2", "6", False, 1.0, time.time())
    pre = txt.split("<pre>")[1].split("</pre>")[0].splitlines()
    assert [l.split()[1] for l in pre] == ["22", "44", "11", "33"]


def _ev(iid, ts, typ, text):
    return {"iid": iid, "ts": ts, "type": typ, "text": text}


def test_stats_par_creneau():
    from core import slotstats
    won = lambda p, dd: (f"🎯 OBJECTIF ATTEINT\n💰 Solde départ : 200.00 EUR\n✅ Profit : +{p} EUR (+0.50 %)\n"
                         f"⏱️ Durée : 30 min\n📉 Drawdown max : {dd} %")
    sl = "🚨 STOP LOSS D'URGENCE DÉCLENCHÉ\n━━━━━━━━━━━━━━━━━━\n❌ Perte à l'instant : 30.00 EUR (25.0 %)"
    evs = [
        _ev("a", 1000, "OBJECTIF_AUTO", "🎯 Créneau 2:00 : objectif +0.50 % lancé."),
        _ev("b", 1001, "OBJECTIF_AUTO", "🎯 Créneau 2:00 : objectif +0.50 % lancé."),
        _ev("a", 2800, "OBJECTIF_ATTEINT", won("1.00", "3.5")),
        _ev("b", 4000, "NOTIF", "⛔ LIMITE DE PERTE DE SESSION ATTEINTE\n📉 Perte : 20 EUR"),
        _ev("b", 5000, "NOTIF", sl),
        _ev("a", 9000, "OBJECTIF_AUTO", "🎯 Créneau 17:30 : objectif +0.50 % lancé."),
        _ev("a", 9600, "OBJECTIF_ATTEINT", won("1.20", "8.0")),
        _ev("b", 9000, "CRENEAU_SAUTE", "⏭ Créneau 17:30 sauté : solde 100 < 125 EUR."),
        _ev("a", 20000, "OBJECTIF_AUTO", "🎯 Créneau 2:00 : objectif +0.50 % lancé."),     # encore en cours
        _ev("c", 20000, "OBJECTIF_ATTEINT", won("9.99", "1")),                              # objectif manuel : ignoré
    ]
    st = slotstats.compute(evs)
    s2, s17 = st["2:00"], st["17:30"]
    assert (s2.launched, s2.won, s2.lost, s2.open, s2.session_stops) == (3, 1, 1, 1, 1)
    assert s2.gains == [1.0] and s2.losses == [30.0] and s2.durations == [1800, 3999] and s2.dds == [3.5]
    assert (s17.launched, s17.won, s17.skipped, s17.worst_dd) == (1, 1, 1, 8.0)
    txt = render.slot_stats_text(st, 30, 2, "EUR")
    assert txt.index(" 2:00") < txt.index("17:30")
    assert "✅ 2 réussi(s) · 🚨 1 stop loss d'urgence · ⏳ 1 en cours" in txt
    assert "Seuil de rentabilité : <b>96 %</b>" in txt and "pas atteint" in txt
    assert "1 créneau(x) sauté(s) · 1 limite(s) de perte de session" in txt
    assert "Aucun objectif de créneau" in render.slot_stats_text({}, 7, 1, "EUR")


def test_preset_conseille_supprime(tmp_path):
    """Bot 3.18.1 : les presets « conseille » ajoutés automatiquement sont retirés, sauf s'il est le preset ⭐ actif."""
    from core import presets as cps
    st = cps.PresetStore(tmp_path / "p.json")
    st.put("Martin", {"fast": "12"})
    st.put("conseille", {"fast": "9"})
    st.put("conseille2", {"fast": "9"})
    st.set_active("Martin")
    assert cps.remove_recommended(st) == ["conseille", "conseille2"] and st.names() == ["Martin"]
    st.put("conseille", {"fast": "9"})
    st.set_active("conseille")
    assert cps.remove_recommended(st) == [] and "conseille" in st.presets

def test_stats_creneau_objectif_annule_sans_entree():
    from core import slotstats
    evs = [_ev("a", 10, "OBJECTIF_AUTO", "🎯 Créneau 4:00 : objectif +0.50 % lancé."),
           _ev("a", 2000, "OBJECTIF_ANNULE", "⌛ Créneau 4:00 : objectif annulé : aucune série ouverte en 30 min.")]
    s = slotstats.compute(evs)["4:00"]
    assert (s.launched, s.cancelled, s.open, s.done) == (1, 1, 0, 0)
    assert "1 annulé(s) (sans entrée ou coupure)" in render.slot_stats_text({"4:00": s}, 30, 1, "EUR")


def test_coupure_accueil_stats_et_preset():
    from core import slotstats, presets as cps, params as cat
    txt = block(view(1, positions=2, coupure_en_cours="13:45"), view(2))
    assert "⛔ Coupure 13:45 : la série en cours finit au TP, puis l'objectif est annulé" in txt
    evs = [_ev("a", 10, "OBJECTIF_AUTO", "🎯 Créneau 11:00 : objectif +0.50 % lancé."),
           _ev("a", 9000, "OBJECTIF_COUPE", "⛔ Coupure 13:45 : objectif annulé (aucune série ouverte).")]
    s = slotstats.compute(evs)["11:00"]
    assert (s.cancelled, s.open) == (1, 0)
    assert cps.slots_input(dict(cat.DEFAULTS), cat.CUT_KEYS) == "13:45,21"
    data = b"\xff\xfe" + "A=1\r\nObjectifCoupureHeures=13:45,21\r\n".encode("utf-16-le")
    out = cps.overlay_set(data, {"ct_on": "0", "ct_m00": str(1 << 20), "ct_m15": "0", "ct_m30": "0", "ct_m45": "0"})
    t = out[2:].decode("utf-16-le")
    assert "ObjectifCoupureHeures=20\r\n" in t and "ObjectifCoupureActive=false" in t


def test_mode_continu_accueil_et_titre():
    """EA 7.8.0 : sans objectif, le titre et la ligne de progression parlent du mode continu, pas des créneaux."""
    v = view(1, objectif_actif=False, mode_continu=True, prochain_creneau="2026.11.09 09:00")
    txt = render.home_text([v], totals([v]), {}, "EUR", "3", "7.8.0", False, 1.0, time.time())
    assert "🔁 <b>Mode continu</b> : attend que tous les filtres soient OK" in txt
    assert "prochain créneau" not in txt and "· mode continu" in txt
    a = view(1, objectif_actif=False, mode_continu=True, continu_arme=True)
    txt = render.home_text([a], totals([a]), {}, "EUR", "3", "7.8.0", False, 1.0, time.time())
    assert "⏳ <b>Mode continu</b> : filtres OK, lancement à la clôture M5" in txt


def test_accueil_tous_les_comptes():
    """Bot 3.4.0 : les 9 comptes (et plus) apparaissent sur l'accueil, plus de limite à 8."""
    vs = [view(i) for i in range(1, 13)]
    txt = render.home_text(vs, totals(vs), {}, "EUR", "3", "7.8.3", False, 1.0, time.time())
    assert "autre(s) : 👥 Comptes" not in txt and txt.count("\n🟢 ") + txt.count(">🟢 ") >= 0
    assert all(v.inst.label in txt for v in vs)
