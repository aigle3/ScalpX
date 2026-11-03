"""Écran Telegram du compte du terminal de test de l'optimiseur (🤖 Optimiseur → 🔑 Terminal de test)."""
from __future__ import annotations

from .render import esc
from .ui import Keyboard


def render_wizard(dash) -> tuple[str, Keyboard]:
    step, data = dash.bt_wiz
    kb: Keyboard = []
    if step == "login":
        text = ("🔑 <b>Compte du terminal de test</b> (1/3)\n\nEnvoie le <b>numéro du compte</b> (login) chez le broker.\n"
                "<i>Démo ou réel : le testeur de l'optimiseur ne passe aucun ordre.</i>")
    elif step == "server":
        text = f"🔑 <b>Compte {data.get('login')}</b> (2/3)\n\nChoisis le <b>serveur</b> du broker :"
        for i, s in enumerate(data.get("servers", [])[:8]):
            kb.append([(s, f"d:btsv:{i}")])
        kb.append([("✏️ Autre serveur…", "d:btsv:new")])
    elif step == "server_text":
        text = f"🔑 <b>Compte {data.get('login')}</b> (2/3)\n\nEnvoie le <b>nom exact du serveur</b> (ex. Broker-Live)."
    else:
        text = (f"🔑 <b>Compte {data.get('login')} · {esc(data.get('server', ''))}</b> (3/3)\n\nEnvoie le <b>mot de passe</b>.\n"
                "<i>Ton message est effacé aussitôt ; MT5 mémorise le mot de passe, le bot ne le garde pas.</i>")
    kb.append([("✖ Annuler", "d:btq")])
    return dash._with_flash(text), kb
