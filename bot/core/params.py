"""Catalogue des paramètres de l'EA (généré depuis SX_Core.mqh : clé, libellé, groupe, type, bornes, pas, unité, options).
Les valeurs courantes viennent de la commande PARAMS de l'EA ; les changements passent par la commande SET."""
from __future__ import annotations

GROUPS = [
 "📐 Signal & timeframe",
 "🧱 Lots & grille",
 "🎯 Take profit",
 "🛡️ Filtres marché",
 "🔭 Filtres multi-timeframe",
 "⏰ Filtre horaire",
 "⚠️ Risque",
 "🏆 Objectifs",
 "⚡ Exécution",
 "🔔 Notifications",
 "🎨 Graphique",
 "🧠 Alertes & news"
]

# (clé, libellé, groupe, type, min, max, pas, unité, options)
PARAMS = [
    ("tf", "Timeframe de trading", 0, "enum", 0.0, 5.0, 1.0, "", ["M1", "M5", "M15", "M30", "H1", "H4"]),
    ("bar", "Bougie utilisée", 0, "enum", 0.0, 1.0, 1.0, "", ["En cours", "Clôturée"]),
    ("fast", "EMA rapide", 0, "int", 2.0, 200.0, 1.0, "", []),
    ("slow", "EMA lente", 0, "int", 3.0, 400.0, 1.0, "", []),
    ("atrp", "Période ATR", 0, "int", 2.0, 100.0, 1.0, "", []),
    ("lot", "Taille du lot", 1, "dbl", 0.01, 10.0, 0.01, " lot", []),
    ("tbm", "Trades avant multiplication", 1, "int", 1.0, 50.0, 1.0, "", []),
    ("lotmult", "Multiplicateur de lot", 1, "dbl", 1.0, 3.0, 0.1, " x", []),
    ("grid", "Distance de grille", 1, "int", 10.0, 2000.0, 10.0, " pts", []),
    ("gridmult", "Multiplicateur de distance", 1, "dbl", 1.0, 3.0, 0.05, " x", []),
    ("dyngrid", "Grille dynamique (ATR)", 1, "bool", 0.0, 1.0, 1.0, "", []),
    ("gridatr", "Multiplicateur ATR (grille)", 1, "dbl", 0.1, 5.0, 0.1, " x", []),
    ("gridmin", "Grille dynamique : minimum", 1, "int", 10.0, 2000.0, 10.0, " pts", []),
    ("gridmax", "Grille dynamique : maximum", 1, "int", 20.0, 5000.0, 10.0, " pts", []),
    ("maxpend", "Ordres en attente max", 1, "int", 1.0, 50.0, 1.0, "", []),
    ("maxspread", "Spread maximum", 3, "int", 5.0, 500.0, 5.0, " pts", []),   # 🛡️ Filtres marché (EA 6.3.0)
    ("wavg", "Prix moyen pondéré", 1, "bool", 0.0, 1.0, 1.0, "", []),
    ("dyntp", "TP dynamique (ATR)", 2, "bool", 0.0, 1.0, 1.0, "", []),
    ("atrmult", "Multiplicateur ATR", 2, "dbl", 0.1, 10.0, 0.1, " x", []),
    ("tpfixed", "TP fixe", 2, "dbl", 10.0, 5000.0, 10.0, " pts", []),
    ("tpmin", "TP minimum", 2, "int", 5.0, 2000.0, 5.0, " pts", []),
    ("tpmax", "TP maximum", 2, "int", 10.0, 5000.0, 10.0, " pts", []),
    ("f_atr", "Filtre volatilité (ATR)", 3, "bool", 0.0, 1.0, 1.0, "", []),
    ("atrmin", "ATR minimum", 3, "dbl", 10.0, 5000.0, 10.0, " pts", []),
    ("atrmax", "ATR maximum", 3, "dbl", 20.0, 10000.0, 10.0, " pts", []),
    ("f_ema", "Filtre Prix / EMA", 3, "bool", 0.0, 1.0, 1.0, "", []),
    ("f_vol", "Filtre de volume", 3, "bool", 0.0, 1.0, 1.0, "", []),
    ("volmin", "Volume minimum", 3, "dbl", 0.1, 1.5, 0.05, " x moy.", []),
    ("volmax", "Volume maximum", 3, "dbl", 1.2, 10.0, 0.1, " x moy.", []),
    ("volbars", "Bougies pour la moyenne", 3, "int", 5.0, 100.0, 5.0, "", []),
    ("f_lo", "Filtre EMA timeframe inférieur", 4, "bool", 0.0, 1.0, 1.0, "", []),
    ("lotf", "Timeframe inférieur", 4, "enum", 0.0, 5.0, 1.0, "", ["M1", "M5", "M15", "M30", "H1", "H4"]),
    ("lofast", "Inférieur : EMA rapide", 4, "int", 2.0, 200.0, 1.0, "", []),
    ("loslow", "Inférieur : EMA lente", 4, "int", 3.0, 400.0, 1.0, "", []),
    ("lobar", "Inférieur : bougie utilisée", 4, "enum", 0.0, 1.0, 1.0, "", ["En cours", "Clôturée"]),
    ("f_hi", "Filtre EMA timeframe supérieur", 4, "bool", 0.0, 1.0, 1.0, "", []),
    ("hitf", "Timeframe supérieur", 4, "enum", 0.0, 5.0, 1.0, "", ["M1", "M5", "M15", "M30", "H1", "H4"]),
    ("hifast", "Supérieur : EMA rapide", 4, "int", 2.0, 200.0, 1.0, "", []),
    ("hislow", "Supérieur : EMA lente", 4, "int", 3.0, 400.0, 1.0, "", []),
    ("hibar", "Supérieur : bougie utilisée", 4, "enum", 0.0, 1.0, 1.0, "", ["En cours", "Clôturée"]),
    ("f_can", "Filtre bougie précédente (sup.)", 4, "bool", 0.0, 1.0, 1.0, "", []),
    ("canbody", "Corps minimum de la bougie", 4, "dbl", 0.0, 3.0, 0.05, " x ATR", []),
    ("tz", "Fuseau horaire", 5, "enum", 0.0, 4.0, 1.0, "", ["France (Paris)", "Londres", "UTC", "New York", "Heure du PC"]),
    ("f_time", "Filtre horaire", 5, "bool", 0.0, 1.0, 1.0, "", []),
    ("t_start", "Début du blocage", 5, "int", 0.0, 23.0, 1.0, " h", []),
    ("t_end", "Fin du blocage", 5, "int", 0.0, 23.0, 1.0, " h", []),
    ("t_server", "Heure du serveur", 5, "bool", 0.0, 1.0, 1.0, "", []),
    ("ddthr", "Seuil de drawdown", 6, "dbl", 1.0, 95.0, 1.0, " %", []),
    ("ddstop", "Arrêt auto sur drawdown", 6, "bool", 0.0, 1.0, 1.0, "", []),
    ("emsl", "Stop loss d'urgence", 6, "bool", 0.0, 1.0, 1.0, "", []),
    ("emslpct", "SL d'urgence", 6, "dbl", 1.0, 90.0, 1.0, " %", []),
    ("sesslim", "Limite de perte session", 6, "bool", 0.0, 1.0, 1.0, "", []),
    ("sesspct", "Perte session max", 6, "dbl", 1.0, 90.0, 1.0, " %", []),
    ("early", "Clôture anticipée", 7, "bool", 0.0, 1.0, 1.0, "", []),
    ("goalmax", "Plafond d'objectif (montant)", 7, "dbl", 1.0, 100000.0, 5.0, "", []),
    ("profnotif", "Alerte profit cumulé", 7, "dbl", 0.0, 10000.0, 1.0, "", []),
    ("sl_on", "Objectif auto aux créneaux", 7, "bool", 0.0, 1.0, 1.0, "", []),
    ("sl_pct", "Objectif auto : gain", 7, "dbl", 0.1, 20.0, 0.1, " %", []),
    ("sl_win", "Fenêtre de lancement", 7, "int", 5.0, 15.0, 5.0, " min", []),
    ("sl_m00", "Créneaux à l'heure pile (:00)", 7, "int", 0.0, 16777215.0, 1.0, "", []),
    ("sl_m15", "Créneaux à :15", 7, "int", 0.0, 16777215.0, 1.0, "", []),
    ("sl_m30", "Créneaux à :30", 7, "int", 0.0, 16777215.0, 1.0, "", []),
    ("sl_m45", "Créneaux à :45", 7, "int", 0.0, 16777215.0, 1.0, "", []),
    ("seuil_on", "Clôture anticipée au seuil", 7, "bool", 0.0, 1.0, 1.0, "", []),
    ("seuil_arret", "Seuil d'arrêt", 7, "dbl", 10.0, 1000000.0, 10.0, "", []),
    ("seuil_reprise", "Seuil de reprise", 7, "dbl", 0.0, 999990.0, 5.0, "", []),
    ("dev", "Déviation max", 8, "int", 0.0, 500.0, 5.0, " pts", []),
    ("retries", "Nouvelles tentatives", 8, "int", 0.0, 5.0, 1.0, "", []),
    ("n_filters", "Alertes globales des filtres", 9, "bool", 0.0, 1.0, 1.0, "", []),
    ("n_progress", "Progression de l'objectif", 9, "bool", 0.0, 1.0, 1.0, "", []),
    ("n_errors", "Erreurs d'ordres", 9, "bool", 0.0, 1.0, 1.0, "", []),
    ("ch_style", "Style moderne du graphique", 10, "bool", 0.0, 1.0, 1.0, "", []),
    ("ch_ema", "Afficher les EMA", 10, "bool", 0.0, 1.0, 1.0, "", []),
    ("ch_levels", "Afficher TP / prix moyen / grille", 10, "bool", 0.0, 1.0, 1.0, "", []),
    ("ch_panel", "Afficher le panneau", 10, "bool", 0.0, 1.0, 1.0, "", []),
    ("n_watchdog", "Surveillance (watchdog)", 11, "bool", 0.0, 1.0, 1.0, "", []),
    ("n_market", "Alertes spread / swap", 11, "bool", 0.0, 1.0, 1.0, "", []),
    ("n_news", "Alerte news économiques", 11, "bool", 0.0, 1.0, 1.0, "", []),
    ("news_min", "News : préavis", 11, "int", 5.0, 180.0, 5.0, " min", []),
    ("news_imp", "News : importance", 3, "enum", 0.0, 1.0, 1.0, "", ["Haute", "Moyenne + Haute"]),
    ("news_block", "Filtre News (pas d'entrée autour des annonces)", 3, "bool", 0.0, 1.0, 1.0, "", []),
    ("news_before", "Filtre News : blocage avant l'annonce", 3, "int", 0.0, 120.0, 5.0, " min", []),
    ("news_after", "Filtre News : blocage après l'annonce", 3, "int", 0.0, 180.0, 5.0, " min", []),
    ("vac_risk", "Mode vacances : plafond de risque", 6, "dbl", 0.5, 50.0, 0.5, " %", []),
]


# ---------------------------------------------------------------- créneaux d'objectif automatique
# L'EA garde les créneaux dans 4 masques de 24 bits, un par minute (00, 15, 30, 45) : le bit h = un créneau à h:MM.
SLOT_KEYS = ("sl_m00", "sl_m15", "sl_m30", "sl_m45")
SLOT_MINUTES = (0, 15, 30, 45)
HIDDEN = frozenset(SLOT_KEYS)          # pas de bouton « nombre » : ils se règlent dans l'écran 🕐 Créneaux
SLOTS_PER_PAGE = 40                    # boutons ✖ par page (Telegram : 100 boutons au plus par message)
SLOT_GROUP = 7                         # 🏆 Objectifs
# Valeurs par défaut de l'EA (inputs) des réglages ajoutés en 6.2.0 : point de départ quand un preset plus ancien
# ne les contient pas encore (rien n'est enregistré tant qu'on ne les modifie pas)
DEFAULTS = {"sl_on": "1", "sl_pct": "1.0", "sl_win": "10", "sl_m00": "1184328", "sl_m15": "0", "sl_m30": "0", "sl_m45": "0",
            "seuil_on": "1", "seuil_arret": "500", "seuil_reprise": "125"}


def slots_of(masks: list[int]) -> list[int]:
    """4 masques → créneaux en minutes depuis minuit, triés."""
    return sorted(h * 60 + SLOT_MINUTES[i] for i, m in enumerate(masks) for h in range(24) if int(m) >> h & 1)


def masks_of(slots) -> list[int]:
    """Créneaux (minutes depuis minuit) → 4 masques ; les minutes hors 00/15/30/45 sont ignorées."""
    out = [0, 0, 0, 0]
    for s in slots:
        h, m = divmod(int(s), 60)
        if 0 <= h < 24 and m in SLOT_MINUTES:
            out[SLOT_MINUTES.index(m)] |= 1 << h
    return out


def slot_text(slot: int) -> str:
    return f"{slot // 60:02d}:{slot % 60:02d}"


def slots_text(masks: list[int]) -> str:
    s = slots_of(masks)
    return ", ".join(slot_text(x) for x in s) if s else "aucun"


def mask_text(key: str, raw: str) -> str:
    """Valeur d'un masque → texte lisible (« 03:00, 06:00 ») ; autre clé ou valeur illisible : inchangée."""
    if key not in SLOT_KEYS:
        return raw
    try:
        m = int(float(raw))
    except (TypeError, ValueError):
        return raw
    masks = [0, 0, 0, 0]
    masks[SLOT_KEYS.index(key)] = m
    return slots_text(masks)
