# ScalpX — bot Telegram central

Un seul bot Telegram pilote tous les terminaux MT5 d'un VPS Windows (un terminal portable par compte).
Le bot **ne se connecte jamais aux comptes** et les EA **ne parlent jamais à Telegram** :
tout passe par des fichiers dans `%APPDATA%\MetaQuotes\Terminal\Common\Files`.

```
Telegram ⇄ bot Python ⇄ fichiers Common\Files ⇄ EA (un par terminal)
              │
              ├─ crée / lance / arrête les terminaux C:\MT5\instances\<login>_<serveur>\ (copies de C:\MT5\BASE)
              └─ se met à jour depuis GitHub (dépôt privé, jeton en lecture seule)
```

| Fichier (Common\Files) | Sens | Rôle |
|---|---|---|
| `statut_<id>.json` | EA → bot | heartbeat toutes les 5 s (solde, état, cycle, version…) |
| `events_<id>.jsonl` | EA → bot | notifications et événements |
| `cmd_<id>_<n>.json` | bot → EA | une commande par fichier, exécutée dans l'ordre |
| `ack_<id>_<n>.json` | EA → bot | résultat de chaque commande |
| `etat_<id>.json` | EA | copie de secours de l'état et du cycle |

`<id>` = `login_serveur`, tout caractère hors `[0-9A-Za-z]` remplacé par `_`.

## Installation

Voir `LISEZMOI.txt` à la racine du dépôt : `INSTALL.bat` installe tout (BASE depuis le MT5 du broker, EA, bot, venv,
`.env` avec les jetons Telegram et GitHub, tâche planifiée « ScalpX Bot »). Vérification : `.venv\Scripts\python main.py --check`.

- `.env` : `TELEGRAM_TOKEN`, `ALLOWED_CHAT_IDS`, `GITHUB_TOKEN` (jamais recopiés par les mises à jour).
- `config.toml` : chemins, symbole/période, délais, `[update]` (dépôt GitHub). Toute option a une valeur par défaut.
- Production : la tâche planifiée lance `pythonw.exe main.py` à l'ouverture de session (session Windows à garder ouverte).
  Débogage : `.venv\Scripts\python.exe main.py --console`. Journaux : `data\logs\bot.log`, `compile.log`, `crash.log`.

## Utilisation (tout par boutons, aucune commande « / »)

- **Accueil** (un message édité en place ; **📊 Tableau de bord** ou tout message le ramène en bas). Mis à jour toutes
  les 10 s tant qu'il est le dernier message ; recouvert par d'autres messages, il est republié en bas, en silence, après 5 min. **En tout premier,
  l'état du trading** (lu sur le robot le plus récent : tous ont les mêmes réglages) : 🟢 trading autorisé / ⛔ entrées
  bloquées (raison) / ⏸ pause (jusqu'à…) / ⚪ sans objectif / 🏖 vacances, l'objectif en cours (gain visé, cible,
  progression) ou le prochain créneau, les positions en cours, le **filtre News**
  (actif, désactivé ou news en cours), chaque filtre 🟢/🔴 avec la raison d'un blocage, et les 3 prochaines news.
  Puis l'état de l'ensemble (comptes 🟢/🟠/🔴, solde, jour, à retirer, alertes, VPS, versions, 🔔 mise à jour), une ligne
  par compte, et 4 menus : **👥 Comptes**, **⚡ Actions**, **📈 Rapports**, **🛠 Système**.
- **🧭 Filtres et positions** (accueil) : état des filtres (lu sur un robot : tous ont les mêmes réglages), signal
  bloqué ou non, et positions / ordres / flottant de chaque compte avec le total ; 📋 détail des positions en un message.
- **👥 Comptes** : liste détaillée, un bouton par compte, 🔎 filtre et ↕ tri (un bouton chacun, qui fait défiler les choix).
- **⏸ Pause** (fiche ou ⚡ Actions pour tous) : tout de suite jusqu'à ▶ Reprise, ou **programmée** 1 h / 2 h / 4 h /
  jusqu'à 08:00 : l'EA reprend tout seul à l'heure prévue (commande `PAUSE 2h`, `PAUSE 08:00`).
- **Fiche compte** : ⏸ Pause, ▶️ Reprise, **🎯 Objectif**, **⚙️ Paramètres** (74 réglages par boutons), **🧰 Commandes EA**
  (consultations, vacances, presets, valider, annuler l'objectif, 🛑 tout fermer), **🖥 Terminal** (redémarrer, lancer,
  arrêter, reconnecter, mettre à jour l'EA, supprimer), 📈 graphique.
- **⚡ Actions — tous les comptes lancés à la fois** : ⏸ pause (immédiate ou programmée), ▶ tout reprendre, **🎯 Objectif (tous)**,
  **⚙️ Paramètres (tous)** (lecture sur le 1er compte, réglage envoyé à chacun), **🧰 Commandes (tous)**, **🖥 Terminaux (tous)** (redémarrer, lancer, arrêter, réduire les fenêtres),
  🚀 déployer l'EA, ➕ nouveau compte, 🔕 mode silencieux. Toujours avec confirmation et un résumé par compte.
- **🎯 Objectif immédiat** (hors créneaux horaires) : +0,5 / 1 / 2 / 3 / 5 / 10 %, ou ✏️ 10 (€), +3%, =1250 (solde cible) ;
  le bot envoie `OBJECTIF` puis `DEMARRER` : la série démarre tout de suite et s'arrête à l'objectif.
- **💾 Presets** (⚡ Actions, ou 🧰 Commandes EA) : gardés par le bot (`data\presets.json`), communs à tous les robots.
  📸 créer depuis les réglages d'un robot (le premier preset se fait ainsi, depuis Telegram) ; 🔍 comparer avec les robots
  (réglage par réglage : valeur du robot → valeur du preset, avant d'appliquer) ; ⚙️ modifier réglage par
  réglage ; 📤 appliquer à tous les robots lancés, tout de suite (seules les différences sont envoyées, puis chaque robot
  est relu) : il devient le preset ⭐ actif. Toute modification du preset actif part aussitôt vers tous les robots ; un
  contrôle toutes les 5 min affiche 🟠 sur l'accueil les robots dont les réglages s'en écartent.
  Le preset actif est aussi écrit dans le `.set` de BASE (y compris après une mise à jour de l'EA) : un nouveau compte
  part de ces valeurs, puis le preset lui est appliqué juste après sa création (réglages sans input compris : fuseau,
  alertes, délais des news) ; le message « ✅ Compte créé » le confirme.
- **Capital insuffisant** : solde sous le seuil de reprise (125) → compte 🟠 sur l'accueil et alerte sonore (une fois,
  puis une autre quand le solde remonte). Dépôts et retraits : notifiés avec son.
- **📈 Rapports** : rapport des 24 h (aussi chaque soir), courbe des soldes, export CSV.
- **🛠 Système** : versions bot/EA, VPS, BASE, GitHub (et expiration du jeton), surveillance externe, dernière sauvegarde,
  ⬆ mises à jour, 🔨 compiler l'EA, 📜 journaux, 🔄 redémarrer le bot, ⏪ revenir à la version précédente.
- **Surveillance externe** : `HEALTHCHECK_URL` (.env, ex. healthchecks.io) reçoit un ping par minute ; s'il cesse, le service prévient.
- **Sauvegarde** de l'historique (`scalpx.db`) chaque nuit dans `%OneDrive%\ScalpX\sauvegardes` (14 conservées).
- **Watchdog** : relance les terminaux DEMARRE disparus (3 fois par heure max, puis alerte) ; un arrêt volontaire n'est jamais relancé.

## Mises à jour

1. Chaque commit sur `main` est testé par GitHub ; s'il passe, la branche `stable` avance. Le bot télécharge `stable`
   (toutes les minutes) dans `C:\ScalpX\release`.
   Une publication n'est proposée qu'après 2 min sans autre publication ; s'il y en a plusieurs, elles sont proposées
   une par une, dans l'ordre (la suivante après l'installation de la précédente).
2. Telegram : « ⬆ Mise à jour disponible » avec la description et les versions (ex. Bot 1.2.0 → 1.3.0, EA 5.3.0 → 5.3.1).
3. **Installer** : fichiers du bot copiés puis redémarrage du bot (les terminaux continuent) ; sources de l'EA et `.set` copiés
   dans BASE, `version.txt` écrit en dernier → compilation automatique (MetaEditor de BASE) → proposition de déploiement.

Versions à trois niveaux, indépendantes : bot (`version.txt` du bot) et EA (`ea/version.txt`, `SX_VERSION`).

## Déploiement de l'EA sur les comptes

Tout nouvel `.ex5` dans BASE (compilation automatique, bouton 🔨 ou F7 dans MetaEditor) est proposé : tous, un compte
d'abord, une sélection ou plus tard. Compte par compte : maintenance → attente de 0 position → sauvegarde de l'ancien `.ex5`
(`ex5_backup\`) → arrêt propre → copie du seul `.ex5` → relance → vérification d'un heartbeat frais, état et cycle inchangés.
Échec : ancien `.ex5` restauré, déploiement stoppé. Un déploiement interrompu (redémarrage du VPS) se reprend ou s'annule.

Changer à la main `BASE\version.txt` déclenche aussi une compilation (version écrite dans les sources), sauf si les sources
sont plus récentes.

## Fenêtre MT5

À chaque ouverture d'un terminal (création, relance, redémarrage, déploiement), le bot masque la boîte à outils,
le navigateur et l'observation du marché, agrandit le graphique dans la fenêtre puis **réduit la fenêtre** (moins de
CPU et de mémoire ; l'EA trade normalement). Réglable dans `config.toml` (`[mt5] minimize`, `hide_panels`,
`maximize_chart`). 🛠 Système → **🪟 Réduire les terminaux** refait le rangement de tous les terminaux ouverts.

## Profil MT5

Avant chaque lancement, le bot vide le profil `Default` du terminal de l'instance (jamais BASE) : un seul graphique
(symbole + EA) s'ouvre. Les lancements utilisent `scalpx_run.ini` (Login + Server, sans mot de passe).

## Dépannage

| Symptôme | Piste |
|---|---|
| BASE non conforme | `.ex5`, `.set` ou `terminal64.exe` manquant, ou un graphique de BASE porte l'EA |
| Création : « aucun heartbeat » | EA absent de BASE, AutoTrading désactivé, antivirus ; voir 📜 Journal du bot |
| Création : « compte non connecté » | mot de passe ou nom de serveur différent de celui de MT5 |
| 🟠 « EA en double » | un graphique de plus porte l'EA dans l'instance |
| Bot : « Conflit 409 » | un autre bot utilise le même token |
| Mises à jour : « jeton GitHub refusé » | `GITHUB_TOKEN` expiré : en créer un nouveau (lecture seule) dans `.env`, puis 🔄 Redémarrer |
| Mot de passe changé chez le broker | fiche compte → **🔑 Reconnecter** |

## Développement

```bat
.venv\Scripts\python -m pytest -q
```

Un faux EA et de faux terminaux simulent les fichiers ; aucun vrai MT5 ni Telegram n'est nécessaire (stub `tests/stubs/telegram`).
