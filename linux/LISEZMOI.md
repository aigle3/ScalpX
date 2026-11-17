# ScalpX sur un VPS Ubuntu (Wine)

Tout tourne sur la même machine : MT5 (un terminal portable par compte) sous **Wine**, avec un écran virtuel **Xvfb**,
et le bot Telegram central en **service systemd**. Le bot, l'EA et les mises à jour (bouton « ⬆ Installer ») marchent
comme sous Windows.

## Prérequis
- Ubuntu **24.04** conseillé (22.04 accepté), 64 bits, sans bureau graphique. 8 Go de RAM pour une dizaine de comptes.
- Accès root (sudo).

## Installation
1. Récupère le paquet `stable` (jeton GitHub en lecture seule, le même que sur le VPS Windows) :
   ```bash
   mkdir -p ~/scalpx-paquet && cd ~/scalpx-paquet
   curl -fsSL -H "Authorization: Bearer github_pat_TON_JETON" \
        https://api.github.com/repos/aigle3/ScalpX/tarball/stable | tar xz --strip-components=1
   ```
2. Lance l'installation :
   ```bash
   sudo bash linux/install.sh
   ```
   Le script installe Wine (WineHQ stable), Xvfb et Python, crée l'utilisateur `scalpx` (sans sudo), le préfixe Wine,
   installe MT5 de RoboForex dans `BASE`, copie l'EA et le bot, puis demande le token Telegram, ton chat_id, le jeton
   GitHub et l'URL healthchecks.io. Autre broker : `sudo MT5_SETUP_URL=https://…/setup.exe bash linux/install.sh`.
3. Dans Telegram, recrée tes comptes (bouton ➕), comme sous Windows.

⚠️ **Un compte ne doit jamais tourner sur les deux VPS en même temps** (deux EA = ordres en double). Avant d'ajouter
un compte ici, arrête-le sur le VPS Windows (⏹ Arrêter). Et utilise un autre token Telegram tant que le bot Windows
tourne : deux bots sur le même token se volent les messages.

Relancer `install.sh` ne touche jamais à `.env`, `config.toml`, `data/` ni aux comptes déjà créés.

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

## Sécurité
Ferme l'accès SSH par mot de passe (clé uniquement) et n'ouvre aucun autre port : le bot n'en a pas besoin (il contacte
Telegram et GitHub, rien n'entre).
