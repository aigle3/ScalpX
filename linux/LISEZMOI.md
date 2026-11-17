# ScalpX sur un VPS Ubuntu (Wine)

Tout tourne sur la même machine : MT5 (un terminal portable par compte) sous **Wine**, avec un écran virtuel **Xvfb**,
et le bot Telegram central en **service systemd**. Le bot, l'EA et les mises à jour (bouton « ⬆ Installer ») marchent
comme sous Windows.

## Prérequis
- Ubuntu **24.04 ou 26.04** (22.04 accepté), 64 bits, sans bureau graphique. 8 Go de RAM pour une dizaine de comptes.
- Accès root (sudo).

## Installation (seule fois où tu touches au VPS)
Le dépôt est privé : le téléchargement demande ton jeton GitHub de lecture (remplace `github_pat_TON_JETON`) :
```bash
mkdir -p ~/scalpx-paquet && cd ~/scalpx-paquet
curl -fsSL -H "Authorization: Bearer github_pat_TON_JETON" https://api.github.com/repos/aigle3/ScalpX/tarball/stable | tar xz --strip-components=1
sudo bash linux/install.sh
```
Le script installe tout (Wine, Xvfb, Python, MT5, EA, bot, services, droits limités du bot) et ne demande que le
**token Telegram** et ton **chat_id**, seulement s'ils manquent. Au démarrage, le bot te demande dans Telegram les jetons
GitHub qui lui manquent (mises à jour, sauvegarde) : tu les envoies, il les enregistre et efface ton message.
Si l'installeur MT5 échoue, copie le dossier `C:\MT5\BASE` du VPS Windows dans `/home/scalpx/mt5/BASE` puis relance.

Relancer l'installation (sans jeton : le bot garde toujours la dernière version téléchargée) :
`sudo bash /home/scalpx/scalpx/release/linux/install.sh` — ou, une fois les droits du bot en place, 🛠 Système → 🔧.

Ensuite tout se fait dans Telegram :
- **🛠 Système → 🔑 Clés et jetons** : jeton de sauvegarde GitHub, healthchecks.io, jeton des mises à jour (ton message est effacé aussitôt) ;
- **🛠 Système → 🖥 Serveur** : CPU, RAM, disque, processus, démarrage auto,
  🔧 mise à jour de l'installation (droits administrateur, sans mot de passe), 🔁 redémarrage du VPS, 🧨 désinstallation complète ;
- **♻️ Réinitialiser** : comptes et historique effacés, bot, BASE et presets gardés.

⚠️ **Un compte ne doit jamais tourner sur les deux VPS en même temps** (deux EA = ordres en double). Avant d'ajouter
un compte ici, arrête-le sur le VPS Windows (⏹ Arrêter). Et utilise un autre token Telegram tant que le bot Windows
tourne : deux bots sur le même token se volent les messages.

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
sudo systemctl restart scalpx-bot         # redémarrer le bot (les terminaux MT5 continuent de tourner)
sudo -u scalpx bash -c 'cd ~/scalpx/bot && .venv/bin/python main.py --check'   # contrôle de l'installation
```

## Différences avec Windows
- Pas de fenêtres : l'écran est virtuel. Les boutons « Réduire les terminaux », « ⚡ Optimiser le VPS » et « 🎬 Plex »
  n'apparaissent pas.
- Redémarrage du bot (mise à jour, bouton 🔄) : le bot s'arrête et systemd le relance. Les terminaux ne sont pas
  arrêtés avec lui.
- Le VPS redémarre : Xvfb puis le bot démarrent seuls, et le bot relance les terminaux.

## Sauvegarde des presets sur GitHub
Dépôt privé `scalpx-sauvegarde` + jeton fine-grained limité à ce dépôt (Contents : Read and write), envoyé au bot
par 🔑 Clés et jetons → BACKUP_TOKEN. Les presets y sont réécrits à chaque changement ; 💾 Presets → ☁️ Restaurer
depuis GitHub les récupère sur un serveur neuf.

## Sécurité
Ferme l'accès SSH par mot de passe (clé uniquement) et n'ouvre aucun autre port : le bot n'en a pas besoin (il contacte
Telegram et GitHub, rien n'entre).
