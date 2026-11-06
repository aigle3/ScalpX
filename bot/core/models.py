"""Modèles de données : statut, événement, accusé de réception (lus depuis les fichiers de l'EA)."""
from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Filtre(BaseModel):
    """Un filtre de l'EA (5.5.0+) : nom, activé, au vert, info courante, raison du blocage."""
    model_config = ConfigDict(extra="allow")

    nom: str = ""
    actif: bool = False
    ok: bool = True
    info: str = ""
    raison: str = ""


class NewsItem(BaseModel):
    """Prochaine annonce économique (calendrier MT5, heure du fuseau de l'EA)."""
    model_config = ConfigDict(extra="allow")

    heure: str = ""                     # « 2026.10.28 14:30 »
    dans_min: int = 0                   # négatif : en cours
    devise: str = ""
    nom: str = ""
    haute: bool = False


class Statut(BaseModel):
    """Contenu de statut_<id>.json (heartbeat de l'EA)."""
    model_config = ConfigDict(extra="allow")

    id: str = ""
    login: int = 0
    serveur: str = ""
    broker: str = ""
    devise: str = ""
    solde: float = 0.0
    equity: float = 0.0
    marge: float = 0.0
    marge_libre: float = 0.0
    niveau_marge: float = 0.0
    pl_latent: float = 0.0
    solde_plus_flottant_ea: float = 0.0
    flottant_ea: float = 0.0
    positions: int = 0
    ordres: int = 0
    pl_jour: float = 0.0                # equity − solde du début de journée (réalisé + latent)
    pl_jour_realise: float | None = None   # réalisé du jour seul (EA 6.2.2+)
    etat: str = "ACTIF"                 # ACTIF | ARRET_500 | PAUSE
    phase_cloture500: bool = False
    maintenance: bool = False
    cycle: int = 0
    connecte: bool = False
    algo_terminal: bool = False
    algo_ea: bool = False
    symbole: str = ""
    version: str = ""
    nb_graphiques_ea: int = 0
    ea_demarre: bool = False
    objectif_actif: bool = False
    objectif_depart: float = 0.0
    objectif_cible: float = 0.0
    objectif_progression: float = 0.0
    seuil_arret: float = 500.0
    seuil_reprise: float = 125.0
    seuil500_actif: bool = True
    prochain_creneau: str = ""
    coupures: str = ""                       # EA 7.0.0 : heures de coupure de l'objectif (« 13:45, 21:00 »), vide si désactivée
    coupure_en_cours: str = ""               # EA 7.0.0 : coupure passée pendant une série (la série finit au TP)
    raison_blocage: str = ""
    # EA 5.5.0+ (None / vide avec un EA plus ancien)
    trading_autorise: bool | None = None
    pause_jusqua: str = ""
    vacances: bool = False
    signal: str = ""                    # achat | vente | ""
    filtres: list[Filtre] | None = None
    news_filtre: bool = False
    news_bloque: bool = False
    news_raison: str = ""
    news: list[NewsItem] = Field(default_factory=list)
    magic: int = 0
    levier: int = 0                     # effet de levier du compte (EA 7.2.0+)
    ts: int = 0                         # secondes Unix GMT
    ts_iso: str = ""

    def age_s(self, now: float | None = None) -> float:
        """Âge du heartbeat (secondes) d'après son horodatage GMT."""
        return (now if now is not None else time.time()) - self.ts if self.ts else 1e9

    def day_realized(self) -> float:
        """Résultat du jour SANS le latent (trades fermés, hors dépôts/retraits). EA plus ancien : déduit de
        pl_jour (equity − solde de départ) en retirant equity − solde."""
        if self.pl_jour_realise is not None:
            return self.pl_jour_realise
        if not self.pl_jour:
            return 0.0
        return self.pl_jour - (self.equity - self.solde)

    def progress500(self) -> float:
        """Progression du seuil de reprise (minimum de trading = 0 %) au seuil d'arrêt (= 100 %),
        sur solde + flottant EA (0..1+)."""
        lo = self.seuil_reprise if 0 < self.seuil_reprise < self.seuil_arret else 0.0
        span = self.seuil_arret - lo
        if span <= 0:
            return 0.0
        return max(0.0, (self.solde_plus_flottant_ea - lo) / span)

    def to_retire(self) -> float:
        """Montant à retirer en ARRET_500 : solde − seuil de reprise (0 sinon)."""
        if self.etat != "ARRET_500":
            return 0.0
        return max(0.0, self.solde - self.seuil_reprise)


class Evenement(BaseModel):
    """Ligne de events_<id>.jsonl."""
    model_config = ConfigDict(extra="allow")

    seq: int = 0
    ts: int = 0
    id: str = ""
    type: str = "NOTIF"
    prio: int = 1                       # 0 info, 1 important, 2 critique
    silencieux: bool = False
    texte: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class Ack(BaseModel):
    """Contenu de ack_<id>_<n>.json."""
    model_config = ConfigDict(extra="allow")

    id: str = ""
    n: int = 0
    cmd: str = ""
    ok: bool = False
    message: str = ""
    ts: int = 0
