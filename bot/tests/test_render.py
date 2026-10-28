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
    news = [{"heure": time.strftime("%Y.%m.%d") + " 14:30", "dans_min": 95, "devise": "USD", "nom": "Non-Farm Payrolls", "haute": True}]
    txt = block(view(1, signal="achat", news=news), view(2))
    assert txt.startswith("🟡 <b>EN ATTENTE</b>") and "✅ Nouvelles séries <b>autorisées</b>" in txt and "signal achat" in txt
    assert "🟢 Volatilité (ATR)" in txt and "🔴 Volume" in txt and "↳ Volume : Volume trop faible" in txt
    assert "⚪ Désactivés : Horaire" in txt and "🟢 News" in txt
    assert "Filtre News actif : aucune news en cours" in txt
    assert "14:30 USD Non-Farm Payrolls (dans 1 h 35)" in txt
    assert "Aucune position ouverte" in txt


def test_news_bloque_et_positions():
    txt = block(view(1, trading_autorise=False, raison_blocage="News : USD NFP (14:30)", news_bloque=True,
                     news_raison="USD NFP (14:30)", positions=3, flottant_ea=-4.5),
                view(2, positions=0))
    assert txt.startswith("🟢 <b>EN TRAIN DE TRADER</b>")
    assert "⛔ Nouvelles séries <b>bloquées</b> : News : USD NFP (14:30)" in txt
    assert "<b>NEWS EN COURS</b> : USD NFP (14:30)" in txt
    assert "3 position(s) ouverte(s) sur 1 compte(s)" in txt and "-4.50 EUR" in txt


def test_filtre_news_desactive_pause_et_vieil_ea():
    assert "Filtre News <b>désactivé</b>" in block(view(1, news_filtre=False))
    txt = block(view(1, etat="PAUSE", ea_demarre=False, pause_jusqua=time.strftime("%Y.%m.%d") + " 16:00"))
    assert txt.startswith("⏸ <b>EN PAUSE</b> jusqu'à 16:00")
    assert block(view(1, objectif_actif=False, ea_demarre=False)).startswith("⚪ <b>À L'ARRÊT</b>")
    old = block(view(1, filtres=None, trading_autorise=None))                     # EA < 5.5.0
    assert "après la mise à jour de l'EA" in old
    assert "aucune donnée" in block(view(1, ts=int(time.time()) - 600))


def test_accueil_commence_par_le_trading():
    v = [view(1)]
    txt = render.home_text(v, totals(v), {}, "EUR", "2.4.0", "5.5.0", False, 1.0, time.time())
    lines = txt.splitlines()
    assert lines[2].startswith("🟡 <b>EN ATTENTE</b>")
    assert txt.index("EN ATTENTE") < txt.index("💰 Solde")


def test_objectif_en_cours():
    txt = block(view(1, objectif_depart=200.0, objectif_cible=210.0, objectif_progression=66.4), view(2, objectif_actif=False))
    assert "🎯 <b>Objectif en cours</b> : +10.00 EUR (cible 210.00 EUR) ▰▰▰▱▱ 66 % · 1/2 comptes" in txt
    txt = block(view(1, objectif_actif=False, ea_demarre=False, prochain_creneau=time.strftime("%Y.%m.%d") + " 09:00"))
    assert "🎯 Aucun objectif en cours · prochain créneau 09:00" in txt


def test_reprise_automatique_programmee():
    txt = block(view(1, ea_demarre=False, reprise_auto=time.strftime("%Y.%m.%d") + " 23:13"))
    assert txt.startswith("⏳ <b>REPRISE AUTOMATIQUE à 23:13</b>")
