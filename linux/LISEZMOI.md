# ScalpX sur un VPS Ubuntu (Wine)

Tout tourne sur la même machine : MT5 (un terminal portable par compte) sous **Wine**, avec un écran virtuel **Xvfb**,
et le bot Telegram central en **service systemd**.

## Prérequis
- Ubuntu **24.04 ou 26.04** (22.04 accepté), 64 bits, sans bureau graphique. 8 Go de RAM pour une dizaine de comptes.
- Accès root (sudo).

## Installation (une seule commande, aucun jeton dedans)
Sur le serveur neuf, connecté en root (ou avec sudo) :
```bash
curl -fsSL https://raw.githubusercontent.com/aigle3/ScalpX/stable/linux/installer.sh | sudo bash
```
1. Le script demande le **token du bot Telegram** et ton **chat_id** (les deux seules questions).
2. Aucun jeton GitHub : le dépôt ScalpX est public (installation et mises à jour sans jeton).
3. Tout s'installe (Wine, Xvfb, Python, MT5, EA, bot, services, droits limités du bot), l'avancement s'affiche dans
   Telegram ; le paquet téléchargé est supprimé à la fin.
4. Dans Telegram, le bot propose **☁️ Activer la sauvegarde sur GitHub** (voir plus bas), puis ☁️ Restaurer (🛠 Système)
   et 📄 Comptes (fichier) si tu repars d'une sauvegarde.

Relancer l'installation (sans jeton : le bot garde toujours la dernière version téléchargée) :
`sudo bash /home/scalpx/scalpx/release/linux/install.sh` — ou, une fois les droits du bot en place, 🛠 Système → 🔧.

Ensuite tout se fait dans Telegram :
- **🛠 Système → 🔑 Clés et jetons** : jeton de sauvegarde GitHub, healthchecks.io, jeton des mises à jour (facultatif ; ton message est effacé aussitôt) ;
- **🛠 Système → 🖥 Serveur** : CPU, RAM, disque, processus, démarrage auto,
  🔧 mise à jour de l'installation (droits administrateur, sans mot de passe), 🔁 redémarrage du VPS, 🧨 désinstallation complète ;
- **🖥 Serveur → ♻️ Réinitialiser** : comptes et historique effacés, bot, BASE et presets gardés ;
- **📄 Comptes (fichier)** : plusieurs comptes créés d'un coup (`numéro:mot de passe:serveur` par ligne), 2 par 2 ;
- **☁️ Sauvegarder / Restaurer** (🛠 Système) : sauvegarde complète sur GitHub (historique, optimiseur, presets).

⚠️ **Un compte ne doit jamais tourner sur deux serveurs en même temps** (deux EA = ordres en double).

## Emplacements (utilisateur scalpx)
| Quoi | Où |
|---|---|
| Bot | `/home/scalpx/scalpx/bot` (`.env`, `config.toml`, `data/logs`) |
| BASE et comptes MT5 | `/home/scalpx/mt5/BASE`, `/home/scalpx/mt5/instances/<compte>` |
| Préfixe Wine | `/home/scalpx/.wine` |
| Fichiers d'échange bot ↔ EA | `/home/scalpx/.wine/drive_c/users/scalpx/AppData/Roaming/MetaQuotes/Terminal/Common/Files` |

## Commandes utiles
```bash
sudo systemctl status scalpx-bot          # état du bot
sudo journalctl -u scalpx-bot -f          # journal en direct
sudo cat /var/log/scalpx-installation.log  # journal de l'installation (aucun token dedans)
sudo systemctl restart scalpx-bot         # redémarrer le bot (les terminaux MT5 continuent de tourner)
sudo -u scalpx bash -c 'cd ~/scalpx/bot && .venv/bin/python main.py --check'   # contrôle de l'installation
```

## Fonctionnement
- Écran virtuel : aucune fenêtre. Pages web intégrées de MT5 (WebView2) coupées par défaut (🖥 Serveur → 🧩).
- Redémarrage du bot (mise à jour, bouton 🔄) : le bot s'arrête et systemd le relance. Les terminaux continuent de tourner.
- Le VPS redémarre : Xvfb puis le bot démarrent seuls, et le bot relance les terminaux.

## Sauvegarde sur GitHub (privée, sur TON compte, chiffrée)
🛠 Système → ☁️ Sauvegarde GitHub, ou le bouton ✅ Activer proposé après l'installation :
1. Crée un **jeton classique** (github.com → Settings → Developer settings → Personal access tokens → Tokens (classic),
   case **repo**) et envoie-le au bot (message effacé aussitôt).
2. Le bot crée le dépôt **privé** `<ton-compte>/scalpx-sauvegarde` (ou le reprend ; si le nom est pris par un dépôt
   public, il essaie `scalpx-sauvegarde-2`… `-6`). Variante : crée toi-même un dépôt privé et donne un jeton
   fine-grained limité à ce dépôt (Contents : Read and write).
3. Il demande un **mot de passe** (8 caractères au moins) : les sauvegardes sont chiffrées (AES-256-GCM). Le mot de
   passe n'est jamais gardé ; sans lui, la sauvegarde est illisible.
- presets réécrits à chaque changement (`presets.enc`) ; sauvegarde complète chaque nuit (`sauvegarde.enc`) ;
- 📥 Importer accepte tous ces fichiers (chiffrés ou non, texte, presets.json, sauvegarde complète) : le mot de passe
  est demandé si besoin ;
- serveur neuf : même jeton → 🛠 Système → ☁️ Restaurer → mot de passe, puis 📄 pour recréer les comptes.

## Sécurité
Ferme l'accès SSH par mot de passe (clé uniquement) et n'ouvre aucun autre port : le bot n'en a pas besoin (il contacte
Telegram et GitHub, rien n'entre).
