# CHANGELOG — ScalpX v5.0 (architecture multi-comptes)

Base : ScalpX v4.5 (dossier `ScalpX\files`). Les originaux ne sont **pas modifiés** : la v5 est dans `ScalpX\v5_archi`.
Les numéros de ligne sont ceux des fichiers v5.

## Versions
| Version | Date | Changement |
|---|---|---|
| 6.1.0 | 2026-10-29 | **Marché fermé / trading désactivé** (réponses du broker) : plus traités comme des échecs d'ordre. Entrées et ordres de grille : nouvel essai espacé (marché fermé : 60 s ; trading désactivé : 30 s → 5 min, sans limite), UN message par épisode (rappel toutes les 30 min) puis « ✅ trading rétabli », raison visible dans les filtres. Fermetures (`CloseAllFast`) restées incomplètes pour cette cause (ou terminal déconnecté / AutoTrading coupé) : essais espacés 5 s → 60 s jusqu'à réussite, message « 🚫 fermeture impossible » puis « ✅ rétablie » ; les fermetures décidées explicitement (manuelle, stop loss d'urgence, plafond vacances) sont refaites pendant 30 min maximum (annulé si le robot est relancé). Message de fin d'objectif adapté quand la clôture est bloquée. Commandes du bot lues toutes les 1 s (avant 2 s). Aucun changement du signal, des filtres, des lots, de la grille ni du TP. |
| 6.0.0 | 2026-10-29 | **Changement de comportement (demandé)** : plus de délai de reprise. Si le robot tournait, il reprend TOUT DE SUITE après un redémarrage du terminal ou un rattachement de l'EA (avant : après `AutoResumeMinutes` = 20 min) ; les filtres décident ensuite des entrées. Réglage `autoresume`, input `AutoResumeMinutes`, événement `REPRISE_PROGRAMMEE` et champ `reprise_auto` supprimés ; information silencieuse `REPRISE_IMMEDIATE`. |
| 5.6.2 | 2026-10-29 | Correctif : un dépôt n'est plus compté deux fois dans les plus hauts d'equity (global, jour, objectif) → plus de faux « drawdown détecté » ni d'arrêt auto sur drawdown après un dépôt (`PeakRaise` / `PeakAfterFlow`). Seuils et calcul du drawdown inchangés. |
| 5.6.1 | 2026-10-28 | Dépôts et retraits : un seul événement `CASHFLOW` avec le texte, priorité 1 (notification avec son). |
| 5.6.0 | 2026-10-28 | Reprise automatique expliquée : événement `REPRISE_PROGRAMMEE` au redémarrage (heure du redémarrage, heure de reprise, ⏸ pour l'empêcher), message de reprise avec la raison, `reprise_auto` dans le statut. Logique de reprise inchangée. |
| 5.5.1 | 2026-10-28 | Pause programmée échue pendant un arrêt du terminal (moins de 12 h) : reprise automatique au redémarrage. |
| 5.5.0 | 2026-10-28 | Nettoyage (aucun changement de la stratégie ni de ses paramètres) : ancienne interface Telegram de l'EA supprimée (`SX_TgBot.mqh`, `SX_TgExtra.mqh` → `SX_Commands.mqh`, commandes en accès direct), ancien multi-instances (`inst_*.txt`), rapport journalier de l'EA (celui du bot reste), heures calmes et résumé groupé, réglages `ch_atr` et `live`, inputs Telegram/rapport/ATR obsolètes. **News = vrai filtre** (8e ligne : panneau, journal, alerte globale, statut) ; calendrier toujours lu ; le statut publie les filtres, le signal, l'autorisation de trader et les 3 prochaines news. `PAUSE 2h` / `PAUSE 14:30` : pause programmée avec reprise automatique. |
| 5.4.0 | 2026-10-28 | Sous-fenêtre ATR retirée du graphique (et supprimée si une version précédente l'avait ajoutée) ; l'ATR reste calculé pour la grille. Affichage seulement. |
| 5.3.1 | 2026-10-28 | Liste PRESETS : noms des presets ajoutés au texte (« • nom »), pour les boutons du bot. Affichage seulement, aucun changement de trading. |
| 5.3.0 | 2026-10-28 | Volume et pause news activés par défaut (`NewsPauseEnabledAtStart`), versions à 3 niveaux. |

## 0. Ce qui n'a PAS changé (vérifié)
- `SX_Trade.mqh`, `SX_Filters.mqh`, `SX_Chart.mqh`, `SX_FilterBoard.mqh` : **identiques octet pour octet** à la v4.5.
- Aucune modification de : signal, filtres, lots, grille, TP, `CloseAllFast`, `ManageTP`, `GoalTick`, `FinishGoal` (logique), money management.
- Les 70+ paramètres du registre (noms, valeurs par défaut, ordre) et tous les inputs existants sont conservés. Les nouveaux inputs sont ajoutés **à la fin** : l'ancien `.set` se charge tel quel.
- Mécanisme du 1 % (objectif + clôture anticipée) : intact. Les 500 le **réutilisent** (`CloseAllFast`) sans le dupliquer.

## 1. Nouveau fichier : `SX_Archi.mqh` (remplace `SX_Telegram.mqh`)
Entièrement encadré par les commentaires de fichier ; il contient :
| Fonction | Rôle |
|---|---|
| `ArchiPreInit / ArchiInit / ArchiDeinit / ArchiTimer` | cycle de vie, planification (commandes 2 s, statut 5 s, 1 Hz) |
| `AFileWrite / AFileRead / AFileAppend` | fichiers dans `Common\Files`, UTF-8 sans BOM, **écriture atomique** (`.tmp` puis `FileMove` avec `FILE_REWRITE`) |
| `StatutWrite` | `statut_<id>.json` (tous les champs demandés + objectif, prochain créneau, raison de blocage) |
| `CmdPoll / CmdExecute / AckWrite` | `cmd_<id>_<n>.json` lues dans l'ordre, supprimées avant exécution, > 5 min ignorées, JSON illisible ignoré (supprimé après 3 essais) ; `ack_<id>_<n>.json` |
| `EvTyped / EvPush / EvFlushPending` | `events_<id>.jsonl` en ajout ; si le fichier est verrouillé (renommage par le bot), l'événement est conservé en mémoire et réécrit |
| `TgQueueP / TgQueue` | **même interface que l'ancien module** : le texte devient un événement `NOTIF` (ou le message de l'accusé pendant une commande). C'est ce qui garantit « même logique, même résultat » pour les ~90 messages existants |
| `StateSave / StateLoad / SetState` | ACTIF / ARRET_500 / PAUSE + cycle : GlobalVariable + `GlobalVariablesFlush()` + `etat_<id>.json` (secours) |
| `PeutTrader()` | ACTIF ET pas de clôture 500 ET pas de maintenance ET solde < SeuilArret. Toujours `true` dans le Strategy Tester |
| `Seuil500Tick / Close500Sequence / ToArret500 / ContinueTo500` | [SEUIL500] clôture anticipée, vérification, reprise |
| `SlotsParse / SlotTick / NextSlot` | objectif automatique +1 % aux créneaux |

Identifiant : `ACCOUNT_LOGIN` + `_` + serveur dont tout caractère non alphanumérique ASCII est remplacé par `_` (règle à reproduire à l'identique côté Python).

## 2. Fichier principal `ScalpX_v5.mq5` (ex `ScalpX_v4.mq5`)
| Ligne | Modification | Raison |
|---|---|---|
| 2-4, 11-12, 16, 21-22, 26 | en-têtes, `#property version "5.00"` | version |
| 102-106 | `Telegram*` : inputs **conservés mais ignorés** (commentaire OBSOLÈTE) | compatibilité du `.set` existant ; le token n'est plus jamais lu |
| 124-134 | [ARCHI] 7 nouveaux inputs en fin de liste : `ActiverSeuil500=true`, `SeuilArret=500`, `SeuilReprise=125`, `ObjectifAutoCreneaux=true`, `ObjectifAutoPct=1.0`, `ObjectifAutoHeures="3,6,9,12,17,20"`, `ObjectifAutoFenetreMin=5` | architecture, 500, créneaux |
| 142 | `#include "SX_Telegram.mqh"` → `"SX_Archi.mqh"` | retrait du transport Telegram |
| 175-177 | [ARCHI] `ArchiPreInit()` dans `OnInit` | paramètres validés par défaut ; calcul de l'identifiant |
| ancien 167 | suppression de `TgExtraInit()` | panneau Telegram supprimé |
| 200-202 | [ARCHI] `ArchiInit()` | chargement état/cycle, événement `DEMARRAGE_EA`, premier statut |
| ancien 189 | suppression de `TgSendWelcome()` | plus de Telegram |
| 223-225 | [ARCHI] `ArchiDeinit(reason)` à la place du message d'arrêt Telegram | événement `ARRET_EA` |
| 245-248 | [SEUIL500] `Seuil500Tick()` juste après `GoalTick()` | même endroit et même fréquence que le 1 % |
| 283-285 | [ARCHI] `if(!PeutTrader()) return;` avant `PlaceGridBatch()` | ouverture d'ordres de grille. Placé **après** `ManageTP/CheckTPExit` : n'empêche jamais la gestion/fermeture |
| 304-306 | [ARCHI] `if(!PeutTrader()) return;` avant `SendMarketEntry()` | ouverture d'une nouvelle série |
| ancien 314 | suppression de `TgExtraTick1Hz()` | panneau Telegram supprimé |
| 357-359 | [ARCHI] `ArchiTimer()` à la place du polling Telegram | OnTimer léger, aucun impact sur OnTick |

## 3. Modules existants
| Fichier | Ligne(s) | Modification | Raison |
|---|---|---|---|
| `SX_Core.mqh` | 6 | `SX_VERSION "5.0"` | version écrite dans le statut |
| | 129-134, 159 | suppression des variables de transport Telegram (`g_tg*`, `g_q*`, `g_sb*`, `g_pinOKMs`) | code Telegram retiré |
| `SX_Goal.mqh` | 224-226 | [ARCHI] fin d'objectif réussi : `TgQueue(...)` → `EvTyped("OBJECTIF_ATTEINT", …)` (même texte) | événement typé pour l'historique des cycles. **Aucune logique modifiée** |
| | 362-364 | [ARCHI] événement `CASHFLOW` (montant, solde) avant la notification existante | détection des retraits par le bot |
| `SX_Smart.mqh` | 226, 233 | perte / retour de connexion : `TgCrit` → `EvTyped("CONNEXION_PERDUE" / "CONNEXION_RETABLIE")` (même texte, même délai de 30 s) | événements typés demandés |
| | ancien 462-537 | suppression du bloc **code PIN** | la sécurité passe côté bot (chat_id autorisé) |
| | 464-496 | [ARCHI] `ShotTick()` : la capture est copiée dans `Common\Files` et annoncée par un événement `CAPTURE` / `COURBE` (fichier PNG/BMP) | l'EA n'envoie plus rien sur Internet |
| `SX_Journal.mqh` | 580 | `g_curveReq = TelegramChatID` → `"1"` | drapeau de demande de courbe |
| `SX_TgBot.mqh` | 115-118 | [ARCHI] le statut affiche état/cycle/prochain créneau à la place de « Telegram connecté » | |
| | ancien 477, 538, 545, 664-670 | suppression des appels `PinInput`, `TgAnswer`, `PinNeededCb/PinGate` et de `TgSendWelcome()` | code Telegram retiré |
| `SX_TgExtra.mqh` | ancien 15-17, 63-182, 280-299, 321-337, 420-458 | suppression du **panneau d'état live** (envoi/édition de messages, `TgHttp`), de la confirmation « un geste », de l'init/nettoyage Telegram, des blocs PIN | le tableau de bord live est désormais fait par le bot |
| | 242, 248, 251, 257 | `g_shotReq/g_curveReq = "1"` | captures sans chat_id |
| `SX_Panel.mqh` | 120-121, 143, 353-357 | textes du panneau du graphique (plus de mention Telegram, état d'architecture dans le pied) | affichage seulement |

Les menus à boutons (`Btn`, `KB`, `KRow`…) restent dans `SX_TgBot/TgExtra/FilterBoard` : ils produisent des chaînes ignorées (le « markup » n'est plus transmis). Le **nettoyage complet** de ce code mort peut se faire dans une étape ultérieure, sans risque pour le trading.

## 4. Comportements ajoutés (à connaître)
### 4.1 Démarrage par défaut
- Paramètres considérés validés au démarrage (`g_configOK = true`) : plus besoin d'un « Valider ».
- Un redémarrage brutal (kill) restaure l'état : `GlobalVariablesFlush()` après chaque changement + fichier de secours.
- L'indicateur « le robot tournait » (`run`) est maintenant écrit en continu, ce qui permet à la logique existante de reprise de fonctionner aussi après un plantage.

### 4.2 Objectifs automatiques (créneaux)
- Créneaux : `3,6,9,12,17,20` h, **lundi à vendredi**, fuseau réglé dans l'EA (France par défaut), fenêtre de 5 min après l'heure pile (`ObjectifAutoFenetreMin`).
- Au créneau : si l'état est ACTIF, qu'**aucun objectif n'est en cours** et que le solde est ≥ 125, un objectif **+1 %** du solde courant est lancé (`GoalSetFromArg("+1%")`) et le robot est démarré.
- Objectif encore en cours à l'heure suivante → créneau **sauté** (événement `CRENEAU_SAUTE`), **aucun rattrapage**. L'objectif en cours continue normalement (nouvelles séries comprises).
- Solde < 125 → pas de lancement (n'interrompt jamais une série en cours).
- Une fois +1 % atteint (`FinishGoal`), le robot s'arrête jusqu'au créneau suivant libre (comportement existant).

### 4.3 Clôture anticipée à 500 [SEUIL500]
- État ACTIF, ≥ 1 position, `solde + flottant EA ≥ 500` (flottant = `st_floating` : profit + swap des positions de l'EA, filtre magic + symbole) → phase CLÔTURE 500 : `CloseAllFast` (même fonction que le 1 %, ordres en attente supprimés d'abord), puis attente de la mise à jour du solde (2,5 s max, comme `FinishGoal`).
  - solde final ≥ 500 → `ARRET_500`, événement `CLOTURE_500_OK` (solde, écart, montant à retirer) ;
  - solde final < 500 → retour en ACTIF, événement `CLOTURE_500_SOUS_SEUIL` ; **le robot reprend** : si aucun objectif ne couvre déjà 500, un objectif « balance cible 500 » est armé (`=500`) et le robot est démarré, jusqu'à obtenir ≥ 500.
- Priorité avec le 1 % : `GoalTick` passe d'abord. S'il a déjà tout fermé, `Seuil500Tick` voit 0 position et applique « solde ≥ 500 et 0 position → ARRET_500 ». Une seule séquence de clôture a lieu (`g_closing`).
- `ARRET_500` : l'objectif en cours est terminé, plus aucune ouverture, ordres en attente supprimés. Sortie : 0 position, solde < 500 et ≥ 125 → ACTIF, cycle +1 (événement `REPRISE_RETRAIT`). Solde < 125 → reste arrêté, `CAPITAL_INSUFFISANT` **une seule fois**.
- En ACTIF : aucun seuil bas, rien n'est fermé à cause de l'equity/solde sous 125.
- Plantage pendant la clôture : phase non persistée, condition réévaluée au premier tick.
- `ActiverSeuil500=false` : `Seuil500Tick` ne fait rien (le 1 % et le reste se comportent comme avant).

### 4.4 Commandes (fichiers `cmd_<id>_<n>.json`)
Nouvelles : `PAUSE`, `REPRISE`, `STATUT`, `MAINTENANCE_ON`, `MAINTENANCE_OFF`, `FERMER_TERMINAL`, `VALIDER`, `PING`.
Anciennes (même logique, texte renvoyé dans l'accusé) : `DEMARRER` (= REPRISE), `FERMER_TOUT`, `OBJECTIF <10|+5%|=1250>`, `ANNULER_OBJECTIF`, `SET <clé> <valeur>`, `POSITIONS`, `PARAMS`, `FILTRES`, `RISQUE`, `PRESETS`, `PRESET_SAVE|LOAD|DELETE <nom>`, `RESET_PARAMS`, `RAPPORT`, `STATS`, `JOURNAL`, `MULTI`, `NEWS`, `OUTILS`, `RAPIDE`, `AIDE`, `LOT`, `TP`, `GRILLE`, `SPREAD`, `VACANCES [on|off]`, `CAPTURE`, `COURBE`, et `BOUTON <donnée>` pour les actions de boutons de l'ancienne interface.
Format : `{ "id": "<id>", "cmd": "...", "args": [...], "ts": <unix> }`. Accusé : `{ "id", "n", "cmd", "ok", "message", "ts" }`.

### 4.5 Deux choix de ma part à valider
1. **PAUSE annule les ordres en attente** (comme la maintenance), puis la grille est reposée à la reprise. Sinon un ordre limite de la grille pouvait encore s'ouvrir pendant la pause, contrairement au message « plus aucune nouvelle position ni ordre de grille ».
2. **Équité vs flottant** : le déclenchement des 500 utilise `solde + flottant de l'EA` (pas l'equity de tout le compte, qui inclurait des positions manuelles). Le 1 % existant utilise l'equity du compte ; les deux coïncident quand l'EA est seul sur le compte.

## 5. Abandonné / hors périmètre
- Backtest ancien vs nouveau et `SimulerRetraitTester` (abandonnés à ta demande ; tout le code d'architecture reste désactivé dans le Strategy Tester).
- Compilation : **non faite ici** (pas de MetaEditor). Contrôles statiques faits : parenthèses/accolades équilibrées, aucune référence à une fonction supprimée, aucun identifiant dupliqué. **À compiler chez toi** et me renvoyer les éventuelles erreurs.

## 6. Pour tester sur démo
Un compte démo a généralement un solde ≥ 500 : l'EA passerait aussitôt en `ARRET_500`. Abaisse `SeuilArret` (ex. solde actuel + 2) et `SeuilReprise` dans le `.set` (fourni : `scalpx_v5.set`), qui garde le token Telegram **vide**.


## v5.00 — panneau refondu + .set production
- [ARCHI] SX_Panel.mqh : largeur 340 px ; nouvelle section PLANNING (prochain créneau +1 %, compte à rebours, jetons des créneaux du jour, message « quand le trading reprend / pourquoi il ne trade pas ») ; badges d'état (MAINTENANCE, CLÔTURE 500, ARRÊT 500, PAUSE, VEILLE, EN ATTENTE, ACTIF) ; bloc COMPTE enrichi (résultat jour, niveau de marge, barre de progression vers le seuil 500 / minimum 125, cycle) ; objectif : cible/départ/reste, plafond d'affichage >999 % ; pied : connexion, algo terminal, version.
- [ARCHI] PText : texte vide remplacé par un espace (supprime l'affichage « Label »).
- scalpx_v5.set : clés Telegram retirées (obsolètes), VerboseLogs=false, en-tête d'aide. Logique de trading inchangée.

## v5.00 — option de test du glissement (étape 4, test B-3)
- ScalpX_v5.mq5 (en-tête) : `//#define SX_TEST_GLISSEMENT 2.0` **commenté** (désactivé). Encadré `// [SEUIL500]`.
- SX_Archi.mqh, `Close500Sequence()` : `#ifdef SX_TEST_GLISSEMENT bal -= SX_TEST_GLISSEMENT; #endif` juste avant la vérification du solde final. Sans le `#define`, ce code n'est pas compilé : comportement strictement inchangé.

## Correctif — un seul graphique par terminal
- `[ARCHI]` Nouvel input `FermerAutresGraphiques` (défaut `true`) : au démarrage, l'EA ferme les autres graphiques du terminal 
  (3 passes : à l'init, +3 s, +7 s, +11 s) car MT5 rouvre ceux du profil par défaut (EURUSD, GBPUSD…) quel que soit le profil demandé dans le `.ini`. 
  Aucune logique de trading modifiée. Ignoré dans le testeur. À recompiler dans BASE.
