# ScalpX — bot Telegram central (v5)

Un seul bot Telegram pilote tous les terminaux MT5 (un terminal portable par compte) d'un VPS Windows.
Le bot **ne se connecte jamais aux comptes** (pas de module `MetaTrader5`) et les EA **ne parlent jamais à Telegram** :
tout passe par des fichiers dans `%APPDATA%\MetaQuotes\Terminal\Common\Files`.

```
Telegram ⇄ bot Python ⇄ fichiers Common\Files ⇄ EA (un par terminal)
              │
              └─ crée / lance / arrête les terminaux C:\MT5\instances\<login>_<serveur>\ (copie de C:\MT5\BASE)
```

| Fichier (dans Common\Files) | Sens | Rôle |
|---|---|---|
| `statut_<id>.json` | EA → bot | heartbeat toutes les 5 s (solde, état, cycle, version…) |
| `events_<id>.jsonl` | EA → bot | notifications et événements (le bot renomme, lit, supprime) |
| `cmd_<id>_<n>.json` | bot → EA | une commande par fichier (l'EA les exécute dans l'ordre et les supprime) |
| `ack_<id>_<n>.json` | EA → bot | résultat de chaque commande (le bot le supprime après lecture) |
| `etat_<id>.json` | EA | copie de secours de l'état et du cycle |

`<id>` = `login_serveur` avec tout caractère hors `[0-9A-Za-z]` remplacé par `_` (identique côté EA et côté bot).

---

## 1. Préparer le dossier BASE (une seule fois)

BASE est une installation MT5 **sans compte**, jamais modifiée par le bot (il la copie seulement).

1. Installe MetaTrader 5 de ton broker dans `C:\MT5\BASE` (ou copie une installation existante).
2. Lance-le **une fois** avec `C:\MT5\BASE\terminal64.exe /portable`, **ne te connecte à aucun compte**, puis ferme-le.
   Cela crée l'arborescence (`MQL5\Experts`, `MQL5\Presets`, `Profiles`…).
3. Copie dans `C:\MT5\BASE\MQL5\Experts\` les sources de l'EA (`ScalpX_v5.mq5` et les `SX_*.mqh`), puis compile
   `ScalpX_v5.mq5` avec MetaEditor lancé depuis BASE (**F7**) : tu obtiens `ScalpX_v5.ex5`. **0 erreur, 0 warning.**
4. Copie `scalpx_v5.set` dans `C:\MT5\BASE\MQL5\Presets\`.
5. Crée `C:\MT5\BASE\version.txt` contenant uniquement la version, par exemple `5.0` (elle doit correspondre à `SX_VERSION` de l'EA).
6. **Aucun graphique de BASE ne doit porter l'EA** (sinon chaque instance aurait un EA en double). Le bot le vérifie
   avant chaque copie et refuse de copier en cas de problème. Le profil par défaut de MT5 (quelques graphiques sans EA) convient.

## 2. Installer Python et le bot

1. Installe Python 3.11 ou plus récent (python.org), en cochant « Add python.exe to PATH ».
2. Copie le dossier `bot` sur le VPS (par exemple `C:\ScalpX\bot`), puis dans une console :

```bat
cd C:\ScalpX\bot
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

## 3. Telegram

1. Dans **@BotFather** : crée un bot (`/newbot`) et récupère le token.
   Si un ancien token a déjà circulé (ancien `.set`, conversation…), révoque-le (`/revoke`) et utilise un nouveau.
2. Récupère ton chat_id avec **@userinfobot**.
3. Copie `.env.example` en `.env` et remplis :

```
TELEGRAM_TOKEN=123456789:AA...
ALLOWED_CHAT_IDS=123456789
```

Le bot ignore (et journalise) tout autre chat. Le token n'est que dans `.env`.

## 4. Configurer `config.toml`

À adapter au minimum : `[paths]` (BASE, instances, archives), `[mt5] servers` (liste de boutons ; tout autre serveur se saisit dans Telegram via « ✏️ Autre serveur… » et est mémorisé après une création réussie) et `default_server`
(**noms exacts** des serveurs tels que MT5 les affiche : dans le Navigateur, clic droit sur le compte, ou
*Fichier → Ouvrir un compte*), `symbol`/`period` si besoin, `[alerts] expected_currency`.
Les seuils 500 / 125 et les créneaux +1 % sont dans le `.set` de l'EA, pas ici.

Vérifie l'installation :

```bat
.venv\Scripts\python main.py --check
```

Tout doit afficher `[OK]` (BASE saine, version.txt, token, chat_id, serveurs, dossiers).

## 5. Premier lancement (debug)

```bat
.venv\Scripts\python.exe main.py --console
```

Dans Telegram : envoie n'importe quel message (ou touche le bouton **📊 Tableau de bord** en bas). Avec 0 compte, le panneau t'invite à créer un compte (bouton **➕ Nouveau compte**).
`Ctrl+C` pour arrêter. Les logs tournants sont dans `data\logs\bot.log` (le token et les mots de passe y sont masqués).

## 6. Production : tâche planifiée

1. Active l'ouverture de session automatique du VPS (**Autologon** de Sysinternals) : le bot et les terminaux
   doivent tourner dans la **même session utilisateur**.
2. Édite `scalpx_bot_task.xml` : `<Command>` (chemin de `pythonw.exe` du venv, ex. `C:\ScalpX\bot\.venv\Scripts\pythonw.exe`)
   et `<WorkingDirectory>`.
3. Importe-la :

```bat
schtasks /Create /TN "ScalpX Bot" /XML C:\ScalpX\bot\scalpx_bot_task.xml
```

Réglages de la tâche : déclencheur « à l'ouverture de session », *Exécuter seulement si l'utilisateur est connecté*,
redémarrage en cas d'échec (toutes les minutes), aucune limite de durée (pas d'arrêt après 3 jours), une seule instance.
Au démarrage, **le bot relance lui-même** les instances dont l'état souhaité est DEMARRE (échelonné, 5 s d'écart) :
il n'y a pas de script de lancement séparé. Un second lancement du bot est refusé (verrou local + erreur Telegram 409).

## 7. Créer le premier compte (démo)

**➕ Nouveau compte** (bouton du panneau ; **✖ Annuler** à chaque étape) :

1. numéro de compte → 2. mot de passe (**ton message est supprimé aussitôt**) → 3. serveur (boutons, défaut présélectionné)
→ 4. alias facultatif → 5. confirmation.

Le bot contrôle (compte inexistant, BASE sain, espace disque), enregistre l'instance en `EN_CREATION` (ignorée du watchdog),
copie BASE, écrit un `.ini` temporaire, lance `terminal64.exe /portable /config:…`, attend un heartbeat dont le login **et**
le serveur correspondent avec « connecté », **supprime le `.ini` dans tous les cas**, puis redémarre le terminal **sans /config**
pour prouver que MT5 a mémorisé le compte et que le profil recharge l'EA (un seul graphique). Succès : carte du compte.
Échec : message clair, terminal arrêté, dossier archivé, registre nettoyé (aucune instance fantôme).

**Mot de passe** : jamais stocké par le bot (ni clair ni chiffré), jamais dans les logs, SQLite ou messages. C'est MT5
qui le mémorise (`KeepPrivate=1`). Si le mot de passe change chez le broker : fiche compte → **🔑 Reconnecter**.

## 8. Utilisation

- **Tableau de bord** : un seul message (non épinglé), édité en place ; le bouton **📊 Tableau de bord** (clavier, en bas) ou n'importe quel message le ramène en bas de la conversation (10-15 s min, seulement si le contenu change). En-tête :
  comptes par état, solde/equity/latent/jour, **montant total à retirer**, charge CPU/RAM/disque. Liste : icône d'état,
  nom, solde, positions, barre vers 500 (`▰▰▰▱▱`, sur solde + flottant), cycle, `⬆` si version obsolète. Filtres, tri, pagination.
- **Icônes** : 🟢 processus + heartbeat récent · 🟠 anomalie (heartbeat ancien, déconnecté, algo désactivé, EA en double, devise ≠ EUR) ·
  🔴 pas de processus · ⚙️ maintenance · 🔒 clôture à 500 en cours · ⚪ arrêté volontairement.
- **Fiche compte** (bouton du compte) : détail, Pause / Reprise / Redémarrer / Arrêter / Lancer / Reconnecter / Mettre à jour l'EA /
  Supprimer, **🧰 Commandes EA** (toutes les anciennes commandes), Graphique, Actualiser, Retour.
  Arrêt, suppression, reconnexion, déploiement et création demandent une confirmation.
- **Aucune commande « / »** : tout passe par les boutons du panneau — **➕ Nouveau compte**, **🚀 Déployer l'EA**, **📝 Rapport**, **📤 Export** (CSV : balances, events, closures500, withdrawals, cycles), **🔕 Silencieux** (bascule), **🏓 Ping** ; fiche compte : pause, reprise, redémarrer, arrêter, lancer, reconnecter, mettre à jour, supprimer, **🧰 Commandes EA**, graphique.
  Une commande donne « ⏳ envoyé » puis « ✅ exécuté » ou « ❌ échec » (selon l'ack de l'EA, avec timeout).
- **Notifications** (séparées du tableau de bord, regroupées) : 🔒 clôture à 500 lancée · 🏁 500 atteints (solde final, montant à retirer) ·
  ↩️ clôture terminée sous 500 (manque, reprise) · 🟢 reprise après retrait · ⚠️ capital insuffisant · 🔴 terminal muet/planté/relancé ·
  👯 EA en double · 🔄 déploiement · événements de trading de l'EA. Mode silencieux : seules les alertes critiques gardent le son.
- **Rapport** quotidien (22 h par défaut) et bouton **📝 Rapport** : performance par compte, cycles et retraits, clôtures à 500
  (réussies, sous le seuil, glissement moyen), incidents, versions.
- **Gestion des terminaux** : arrêt propre = `FERMER_TERMINAL`, attente 30 s, puis kill (processus identifié par le **chemin** de
  l'exécutable). Si une clôture à 500 est en cours, le bot attend sa fin (délai max `close500_wait_max_s`). Les positions restent ouvertes chez le broker.
  Watchdog : relance uniquement les instances DEMARRE dont le processus a disparu, 3 tentatives par heure maximum puis alerte.
  Un arrêt volontaire (état ARRETE) n'est **jamais** relancé.

## 9. Mettre à jour l'EA

1. Compile la nouvelle version **dans BASE** (`MQL5\Experts\ScalpX_v5.ex5`) et mets à jour `BASE\version.txt`.
2. Dans Telegram : bouton **🚀 Déployer l'EA**. Écran de synthèse : version de BASE, version de chaque instance, instances à mettre à jour,
   avertissement si les inputs du `.set` ont changé (les paramètres des comptes ne sont **jamais** modifiés).
3. Choisis **Un compte** (teste d'abord), une **sélection** ou **Tous** → confirmation.
4. Instance par instance : maintenance (plus d'ouverture) → attente de 0 position (sinon « ⏸ en attente », retente plus tard) →
   sauvegarde de l'ancien `.ex5` (`ex5_backup\`) → arrêt propre → copie du **seul** `.ex5` → relance sans /config →
   vérification d'un heartbeat avec la **nouvelle version**, état et cycle inchangés. En cas d'échec : ancien `.ex5` restauré,
   relance, alerte, **déploiement stoppé**.
5. Progression dans un seul message édité (✅ ⏳ ⏸ ❌), rapport final, historique en SQLite.
   Si le VPS redémarre en plein déploiement, le bot propose **▶ Reprendre** ou **✖ Annuler** au démarrage.

## 10. Dépannage

| Symptôme | Piste |
|---|---|
| `--check` : BASE non conforme | `.ex5`, `.set`, `terminal64.exe` manquants, ou un graphique de BASE porte l'EA : ouvre BASE, retire l'EA, ferme proprement (MT5 sauvegarde le profil) |
| Création : « aucun heartbeat » | EA non compilé/placé dans BASE, ou AutoTrading désactivé, ou terminal non lancé (antivirus ?). Regarde `data\logs\bot.log` et le journal MT5 de l'instance |
| Création : « compte non connecté » | mauvais mot de passe ou nom de serveur différent de celui de MT5 (corrige-le via « ✏️ Autre serveur… » ou `servers` dans config.toml) |
| 🟠 « EA en double » | un graphique de plus porte l'EA dans l'instance : ferme-le (ou recrée le compte depuis un BASE sain) |
| 🟠 « algo trading désactivé » | bouton **Algo Trading** de MT5 / case « Autoriser le trading algorithmique » de l'EA |
| 🔴 puis alerte « 3 relances en 1 h » | le terminal plante en boucle : consulte les journaux MT5 de l'instance, puis **Lancer** à la main |
| Bot : « Conflit 409 » / refus de démarrer | un autre bot utilise le même token ou tourne déjà (verrou `lock_port`) |
| Tableau de bord non édité | flood Telegram (le bot attend le `retry_after`) ; envoie un message (ou touche 📊) : le panneau est recréé |
| Mot de passe changé chez le broker | fiche compte → **🔑 Reconnecter** |

## 11. Développement

```bat
.venv\Scripts\python -m pytest
```

Les tests couvrent : parsing des statuts/événements, états et diagnostic, génération du `.ini`, file de commandes avec ack
(rafale de 10, expiration, fichier corrompu), création/reconnexion (aucun fantôme, `.ini` supprimé, mot de passe absent des logs/SQLite),
watchdog (3 relances/h, arrêt volontaire non relancé), déploiement (succès, position ouverte, `.ex5` défectueux restauré, reprise),
tableau de bord (édition en place, message supprimé, flood), assistant (suppression du mot de passe).
Un faux EA et de faux terminaux simulent les fichiers ; aucun vrai MT5 ni Telegram n'est nécessaire.


## Profil MT5 (graphiques)
Avant **chaque lancement**, le bot supprime les graphiques du profil `Default` **du terminal de l'instance** (jamais dans BASE ;
`[mt5] profile`). MT5 ne retient pas le profil demandé dans le `.ini` : on vide donc celui qu'il ouvre de toute façon. Résultat :
**un seul graphique** s'ouvre (XAUUSD + EA, via `[StartUp]`), au lieu d'EURUSD, GBPUSD… Les lancements courants utilisent
`scalpx_run.ini` (Login + Server, **sans mot de passe** : MT5 se reconnecte avec le mot de passe mémorisé).

## Mise à jour de l'EA (compilation et déploiement automatiques)

1. Modifie les sources dans `BASE\MQL5\Experts` si besoin.
2. Écris la nouvelle version dans `BASE\version.txt` (ex. `5.2`) et enregistre.
3. Le bot (vérification toutes les 20 s) met `SX_VERSION` (SX_Core.mqh) et `#property version` (ScalpX_v5.mq5) à cette version, compile avec `MetaEditor64.exe` de BASE et envoie le résultat dans Telegram (journal : `data\logs\compile.log`).
4. Si la compilation réussit, il propose le déploiement sur les comptes existants : tous, un compte d'abord, une sélection, ou plus tard.

Compiler soi-même dans MetaEditor (F7) marche aussi : tout nouvel `.ex5` dans BASE est proposé au déploiement, même sans changement de version.
Une seule tentative par modification de `version.txt` ; si tu changes `SX_VERSION` à la main APRÈS `version.txt`, le bot n'écrase pas ta valeur.
