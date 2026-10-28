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

- **Tableau de bord** : un message édité en place ; le bouton **📊 Tableau de bord** (ou tout message) le ramène en bas.
  En-tête (comptes par état, soldes, montant à retirer, CPU/RAM/disque), liste filtrable et triable.
- **➕ Nouveau compte** : numéro → mot de passe (message supprimé aussitôt, jamais stocké : MT5 le mémorise) → serveur
  (boutons des serveurs connus, ou saisie, mémorisée) → alias → confirmation. Échec : dossier archivé, aucun fantôme.
- **Fiche compte** : pause, reprise, redémarrer, arrêter, lancer, reconnecter, mettre à jour l'EA, supprimer,
  **⚙️ Paramètres** (les 83 paramètres de l'EA par boutons), **🧰 Commandes EA**, graphique 7 jours.
- **🛠 Système** : versions bot/EA, VPS, état de BASE, de GitHub (et expiration du jeton), surveillance externe,
  dernière sauvegarde, journaux (bot, compilation, plantages), **🔨 Compiler l'EA**, **🔄 Redémarrer le bot**,
  **⏪ Revenir au bot x.y.z** (version précédente, gardée à chaque mise à jour).
- **⏸ Tout en pause / ▶ Tout reprendre** (avec confirmation) ; fiche **🧰** : vacances, presets (charger, enregistrer,
  supprimer, valeurs par défaut), valider les réglages, annuler l'objectif, **🛑 Tout fermer**.
- **Surveillance externe** : `HEALTHCHECK_URL` (.env, ex. healthchecks.io) reçoit un ping par minute ; s'il cesse, le service prévient.
- **Sauvegarde** de l'historique (`scalpx.db`) chaque nuit dans `%OneDrive%\ScalpX\sauvegardes` (14 conservées).
- **⬆ Mises à jour** (🔔 si une mise à jour attend) : vérifie GitHub et propose **Installer** ; « Plus tard » la garde en attente.
- **🚀 Déployer l'EA**, **📝 Rapport** (aussi chaque jour à 22 h), **📤 Export** CSV, **🔕 Silencieux**, **🏓 Ping**.
- **Watchdog** : relance les terminaux DEMARRE disparus (3 fois par heure max, puis alerte) ; un arrêt volontaire n'est jamais relancé.

## Mises à jour

1. Chaque commit sur `main` est testé par GitHub ; s'il passe, la branche `stable` avance. Le bot télécharge `stable`
   (toutes les minutes) dans `C:\ScalpX\release`.
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
