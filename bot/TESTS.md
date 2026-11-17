# ScalpX v5 — checklist de tests avant mise en production

**Tout se fait sur COMPTE DÉMO.** Pour déclencher vite les cas 500, abaisse les seuils dans les entrées de l'EA
(clic droit sur le graphique → *Expert Advisors → Propriétés → Entrées*) : par ex. `SeuilArret = solde + 2`.
Les « Vérification » indiquent où lire la preuve : **Telegram** (fiche/notifications), **statut** (`Common\Files\statut_<id>.json`),
**journal** (onglet *Experts* de MT5, ou `<instance>\MQL5\Logs`), **log bot** (`data\logs\bot.log`).

> **Décision du 27/10 : les backtests sont abandonnés.** Les lignes A2 et B1 sont remplacées par des contrôles équivalents
> (comparaison de code, tests en démo). Aucune simulation de retrait dans le Strategy Tester n'existe dans cette version.

Légende : ☐ à faire · OK / KO à noter à la main.

## A. EA — non-régression

| # | Test | Procédure | Résultat attendu | Vérification | OK/KO |
|---|---|---|---|---|---|
| A1 | Compilation | MetaEditor → `ScalpX_v5.mq5` → F7 | 0 erreur, 0 warning | Onglet *Erreurs* de MetaEditor | ☐ |
| A2 | Logique de trading inchangée (remplace le backtest comparé) | Comparer les sources v4.5 et v5 : `fc /b` (ou `git diff --no-index`) sur `SX_Trade.mqh`, `SX_Filters.mqh`, `SX_Chart.mqh`, `SX_FilterBoard.mqh`, `SX_Goal.mqh` | Les 4 premiers sont **identiques** ; `SX_Goal.mqh` ne diffère que par 2 blocs `// [ARCHI]` (événements) ; tout ajout est encadré `// [ARCHI]` ou `// [SEUIL500]` | Diff ; `CHANGELOG.md` liste chaque modification | ☐ |
| A3 | `.set` chargeable | Graphique → EA → *Entrées → Charger* → `scalpx_v5.set` | Aucune erreur, toutes les valeurs reprises (lot 0.01, grille 150, etc.) | Onglet Entrées + journal | ☐ |
| A4 | Aucune trace de Telegram/WebRequest | `grep -il -e WebRequest -e api.telegram *.mq5 *.mqh` dans le dossier de l'EA | Aucun résultat (les entrées `Telegram*` restent pour la compatibilité du `.set`, marquées « OBSOLÈTE ») | Console | ☐ |
| A5 | Mécanisme du 1 % inchangé | Lancer un objectif `+1%` (créneau, ou fiche compte → **🧰 Commandes EA** → Objectif) sur la démo | Clôture anticipée comme avant : l'EA ferme tout dès que `equity − départ ≥ montant`, vérifie le solde, sinon continue | Journal + événement `OBJECTIF_ATTEINT` | ☐ |

## B. EA — clôture anticipée à 500

| # | Test | Procédure | Résultat attendu | Vérification | OK/KO |
|---|---|---|---|---|---|
| B1 | Cycles complets (remplace le backtest) | Voir B2 puis C2 : clôture → arrêt → « retrait » → reprise | Cycle visible en démo, **aucune ouverture en ARRET_500** | Fiche compte, historique des événements | ☐ |
| B2 | Clôture 500 réussie | Démo avec une série ouverte ; régler `SeuilArret = solde + 1.5` ; attendre que solde + flottant EA l'atteigne | Toutes les positions de l'EA fermées ; solde final ≥ seuil → état `ARRET_500` ; notifications 🔒 puis 🏁 (solde final, montant à retirer) ; événement `CLOTURE_500_OK` | Telegram, `statut.etat = ARRET_500`, `data\scalpx.db` (bouton **📤 Export** → closures500) | ☐ |
| B3 | Clôture terminée sous 500 (glissement simulé) | Sur le compte démo de test uniquement : dans `ScalpX_v5.mq5` décommenter `//#define SX_TEST_GLISSEMENT 2.0`, compiler dans un dossier à part, puis (Arrêter le compte → remplacer `MQL5\Experts\ScalpX_v5.ex5` de l'instance → Lancer). Refaire B2 | Notification ↩️ « clôture terminée sous 500, il manque … » ; événement `CLOTURE_500_SOUS_SEUIL` ; l'EA **reprend** (objectif « =500 » armé) ; plus tard retour à la normale | Telegram + journal. **Remettre l'`.ex5` de BASE ensuite** (Arrêter → copier → Lancer) | ☐ |
| B4 | Aucune ouverture pendant la clôture | Pendant B2, entre 🔒 et 🏁 | 0 nouvelle position, 0 ordre en attente ; aucun nouveau deal dans l'historique du compte | Historique MT5, `statut.positions/ordres` | ☐ |
| B5 | 1 % et 500 en même temps | Objectif +1 % actif et `SeuilArret` réglé pour être franchi au même tick que la cible du 1 % | **Une seule** séquence de clôture, aucune erreur « position introuvable », puis la vérification des 500 s'applique | Journal MT5 (une seule série de fermetures) + événements | ☐ |
| B6 | Kill du terminal pendant la clôture | Juste après 🔒, tuer `terminal64.exe` de l'instance (Gestionnaire des tâches, colonne *Chemin*) | Le watchdog relance (sans /config). Au redémarrage : s'il reste des positions et solde + flottant ≥ seuil → la clôture recommence ; si 0 position et solde ≥ seuil → `ARRET_500` | Telegram (🔴 relancé, puis 🔒/🏁) | ☐ |
| B7 | Solde ≥ 500 → pas d'ouverture, même en ACTIF | Compte à 0 position, `SeuilArret` réglé sous le solde | L'EA passe en `ARRET_500` et n'ouvre rien | `statut.etat`, historique | ☐ |
| B8 | Autres positions intactes | Ouvrir à la main une position (magic 0) pendant B2 | Cette position n'est **jamais** fermée par l'EA (filtre magic 123456) | Historique MT5 | ☐ |

## C. EA — machine à états

| # | Test | Procédure | Résultat attendu | Vérification | OK/KO |
|---|---|---|---|---|---|
| C1 | ARRET_500 n'ouvre rien | Rester en `ARRET_500` 10 min (pendant un créneau +1 %) | Aucune position/ordre ; le panneau affiche « Seuil 500 atteint : reprise après retrait » | Panneau du graphique, historique | ☐ |
| C2 | Reprise avec 125,40 restant | En `ARRET_500`, 0 position : régler `SeuilArret` **au-dessus** du solde (ex. 600) pour simuler un retrait ; ou faire un vrai retrait si le broker démo le permet | Reprise automatique : `ACTIF`, **cycle +1**, notification 🟢, événement `REPRISE_RETRAIT` | Telegram, `statut.cycle` | ☐ |
| C3 | Capital insuffisant | En `ARRET_500`, régler `SeuilReprise` **au-dessus** du solde (ex. 520, `SeuilArret` 600) | Reste `ARRET_500`, **une seule** alerte ⚠️ `CAPITAL_INSUFFISANT` (pas en boucle) | Telegram (1 message sur 10 min) | ☐ |
| C4 | Equity sous 125 en ACTIF | Série ouverte, laisser l'equity passer sous 125 (perte flottante) | L'EA continue de trader au même lot et **ne ferme aucune position** (seul le lancement d'une nouvelle série est bloqué si solde < 125) | Historique, journal | ☐ |
| C5 | PAUSE ne repart pas seule | bouton **⏸ Pause** de la fiche puis solde favorable | État `PAUSE` conservé ; **seule** `REPRISE` le remet en `ACTIF` | `statut.etat` | ☐ |
| C6 | MAINTENANCE | (commande EA MAINTENANCE_ON : à ajouter en bouton si besoin) | Aucune nouvelle ouverture, état inchangé (⚙️) ; levé au redémarrage du terminal | Fiche compte, `statut.maintenance` | ☐ |
| C7 | Kill brutal | Tuer le terminal (Gestionnaire des tâches) | Relance par le watchdog ; **état et cycle conservés** | `statut.etat`, `statut.cycle` avant/après | ☐ |
| C8 | Redémarrage du VPS | Redémarrer le VPS | Autologon → bot → relance échelonnée ; états/cycles conservés | Tableau de bord | ☐ |

## D. Communication

| # | Test | Procédure | Résultat attendu | Vérification | OK/KO |
|---|---|---|---|---|---|
| D1 | Rafale de 10 commandes | Toucher 10 fois très vite le bouton **🏓 Ping** (fiche compte → 🧰 Commandes EA) | 10 exécutions dans l'ordre, 10 ack (« pong »), plus aucun `cmd_*`/`ack_*` dans `Common\Files` après ~1 min | Telegram + dossier | ☐ |
| D2 | Commande > 5 min | Créer à la main `cmd_<id>_1.json` : `{"id":"<id>","cmd":"PING","args":[],"ts":<epoch − 600>}` (`date +%s` donne l'epoch) | L'EA l'ignore et écrit un ack `ok:false` « Commande expirée » ; le bot nettoie l'ack orphelin sous 10 min | Fichier ack | ☐ |
| D3 | JSON corrompu | Déposer `cmd_<id>_2.json` contenant `{"id":` ; écraser un `statut_<id>.json` avec du texte | EA : fichier ignoré puis supprimé après 3 essais, pas de plantage. Bot : continue (statut illisible = ignoré) | Journal EA, log bot | ☐ |
| D4 | Anciennes commandes identiques | Fiche compte → **🧰 Commandes EA** : Positions, Params, Filtres, Risque, Stats, Journal, Rapport, Objectif, Capture, Courbe, Statut (les commandes à paramètre — `LOT`, `TP`, `GRILLE`, `SPREAD`, `VACANCES`, `PRESET_*`, `FERMER_TOUT`… — n'ont plus de saisie texte : à ajouter en boutons) | Même texte/effet qu'avant sur Telegram (le texte est renvoyé par l'EA dans l'ack) | Comparer avec les anciens messages | ☐ |
| D5 | Anciennes notifications | Provoquer : objectif atteint, filtre bloquant, erreur, rapport journalier, déconnexion broker | Elles arrivent toutes via le bot, préfixées du nom du compte | Telegram | ☐ |

## E. Création d'instance

| # | Test | Procédure | Résultat attendu | Vérification | OK/KO |
|---|---|---|---|---|---|
| E1 | Premier compte avec 0 instance | Démarrer le bot sans aucun compte ; envoyer un message ; **➕ Nouveau compte** | Message d'accueil invitant à créer ; création jusqu'à la carte du compte | Telegram | ☐ |
| E2 | Mot de passe supprimé | Pendant l'assistant, saisir le mot de passe | Le message disparaît du chat immédiatement | Chat Telegram | ☐ |
| E3 | `.ini` supprimé (succès et échec) | Après une création réussie, puis après une création ratée | `~/mt5/instances/<id>/scalpx_start.ini` (ou dossier archivé) **absent** dans les deux cas | `ls` | ☐ |
| E4 | Mauvais mot de passe | Créer avec un faux mot de passe | Message clair (« compte non connecté… »), terminal arrêté, dossier dans `archives/*_ECHEC`, **aucune instance fantôme** | Tableau de bord + `archives/` | ☐ |
| E5 | Compte déjà existant | Recréer un compte existant (même login + serveur) | Refusé (« existe déjà ») | Telegram | ☐ |
| E6 | BASE inchangé | Avant/après création : `.venv/bin/python -c "from mt5.base import tree_hash; print(tree_hash('/home/scalpx/mt5/BASE'))"` (ne pas lancer MT5 depuis BASE) | Empreintes identiques | Console | ☐ |
| E7 | Relance sans /config | **Redémarrer** depuis la fiche compte | Reconnexion automatique, un seul graphique EA | `statut.connecte = true`, `nb_graphiques_ea = 1` | ☐ |
| E8 | Mot de passe introuvable | `grep -rF "<mot de passe>" ~/scalpx/bot/data` et dans l'historique Telegram du bot | Aucune occurrence (logs, SQLite) | Console | ☐ |

## F. Pilotage des terminaux

| # | Test | Procédure | Résultat attendu | Vérification | OK/KO |
|---|---|---|---|---|---|
| F1 | Arrêt propre | Fiche → **🛑 Arrêter** → confirmer (avec une position ouverte) | Terminal fermé ; positions toujours ouvertes chez le broker | Appli mobile / web du broker | ☐ |
| F2 | Arrêt pendant une clôture 500 | Lancer B2, puis **Arrêter** aussitôt | Le bot attend la fin de la clôture (≤ `close500_wait_max_s`) avant `FERMER_TERMINAL` ; « Forcer » possible | Tableau de bord, journal | ☐ |
| F3 | Terminal tué → watchdog, 3/heure | Tuer l'instance 4 fois en 1 h | Relances 1 à 3 automatiques (🔴 puis 🟢) ; la 4e **n'est pas** relancée, alerte « 3 relances en 1 h » | Telegram, log bot | ☐ |
| F4 | Arrêt volontaire | **Arrêter** (état ARRETE) | **Pas** de relance par le watchdog | Tableau de bord ⚪ | ☐ |
| F5 | Algo trading / déconnexion | Désactiver le bouton *Algo Trading* ; puis bloquer le terminal dans le pare-feu | Icône 🟠 avec la raison (algo désactivé / déconnecté) | Tableau de bord | ☐ |
| F6 | 3 à 5 instances + reboot VPS | Créer 3-5 comptes démo puis redémarrer le VPS | Relance échelonnée (5 s), toutes 🟢 | Tableau de bord | ☐ |

## G. Déploiement

| # | Test | Procédure | Résultat attendu | Vérification | OK/KO |
|---|---|---|---|---|---|
| G1 | Un compte | Changer `SX_VERSION` (ex. 5.0 → 5.1) dans `SX_Core.mqh`, recompiler **dans BASE**, `version.txt` = 5.1 ; bouton **🚀 Déployer l'EA** → *Un compte* | Nouvelle version, **état et cycle inchangés**, progression dans un seul message (⏳ → ✅), `ex5_backup\` créé | Fiche compte, 📝 Rapport (versions) | ☐ |
| G2 | Position ouverte | Refaire G1 avec une série ouverte | Instance « ⏸ en attente », **aucune ouverture** pendant l'attente (MAINTENANCE_OFF ensuite), nouvelle tentative possible | Message de déploiement | ☐ |
| G3 | `.ex5` défectueux | Placer dans BASE un `.ex5` tronqué (copie coupée) avec `version.txt` = 5.2 ; bouton **🚀 Déployer l'EA** | L'EA ne démarre pas → **ancien `.ex5` restauré**, relance, ❌, alerte, déploiement stoppé (comptes suivants non touchés) | Message + `statut.version` inchangée | ☐ |
| G4 | Redémarrage VPS en plein déploiement | Redémarrer pendant G1 | Au démarrage le bot propose **▶ Reprendre / ✖ Annuler** ; aucune instance ne reste bloquée en maintenance | Telegram | ☐ |

## H. Bot et Telegram

| # | Test | Procédure | Résultat attendu | Vérification | OK/KO |
|---|---|---|---|---|---|
| H1 | Chat non autorisé | Écrire au bot depuis un autre compte Telegram | Aucune réponse ; ligne « chat non autorisé » dans le log | `data\logs\bot.log` | ☐ |
| H2 | Second lancement | Lancer `python main.py` une 2e fois | Refusé : « Un autre bot tourne déjà » (code 3) | Console | ☐ |
| H3 | Tableau de bord 24 h | Laisser tourner 24 h avec plusieurs comptes | Toujours le **même** message édité ; aucune erreur de flood | `grep -iE "flood|retry" data/logs/bot.log` | ☐ |
| H4 | Message du tableau de bord supprimé | Supprimer le message du tableau de bord | Recréé automatiquement (≤ 15 s, ou en touchant 📊) | Telegram | ☐ |
| H5 | Notifications 🔒 🏁 ↩️ | Lors de B2 et B3 | Reçues correctement (regroupées, avec nom du compte) | Telegram | ☐ |
| H6 | Rapport et ping | Attendre 22 h (ou bouton 📝 Rapport) ; bouton 🏓 Ping | Rapport complet (perf, cycles, clôtures, incidents, versions) ; Ping donne uptime et comptes 🟢 | Telegram | ☐ |
| H7 | Endurance 48 h | 48 h sur démo avec plusieurs instances ; relever à 0 h / 24 h / 48 h : `ps -o rss,pcpu -C python3` | Pas de plantage, RAM du bot stable (sans dérive continue), CPU faible | Console | ☐ |
