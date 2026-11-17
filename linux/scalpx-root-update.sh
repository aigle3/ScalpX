#!/usr/bin/env bash
# ScalpX — mise à jour de l'installation (droits administrateur), lancée depuis Telegram : 🖥 Serveur → 🔧.
# Installé par install.sh dans /usr/local/sbin/scalpx-root-update (appartient à root). Télécharge lui-même la branche
# « stable » de @REPO@ (jeton GITHUB_TOKEN du bot, lecture seule) puis relance linux/install.sh --auto : aucune question,
# .env, config.toml, comptes et BASE conservés. Journal : /var/log/scalpx-root-update.log
set -euo pipefail
U="@USER@"
REPO="@REPO@"
LOG=/var/log/scalpx-root-update.log
if [ -z "${SCALPX_DETACHED:-}" ]; then
    # hors du service du bot : continue pendant que install.sh redémarre le bot
    exec systemd-run --quiet --collect --unit="scalpx-root-update-$(date +%s)" -E SCALPX_DETACHED=1 /usr/local/sbin/scalpx-root-update
fi
exec >"$LOG" 2>&1
echo "$(date '+%F %T') mise à jour de l'installation depuis $REPO (stable)"
TOKEN=$(sed -n 's/^GITHUB_TOKEN=//p' "/home/$U/scalpx/bot/.env" | head -1 | tr -d '\r"'"'"' ')
[ -n "$TOKEN" ] || { echo "GITHUB_TOKEN absent du .env du bot"; exit 1; }
D=$(mktemp -d /root/scalpx-paquet.XXXXXX)
trap 'rm -rf "$D"' EXIT
curl -fsSL -H "Authorization: Bearer $TOKEN" "https://api.github.com/repos/$REPO/tarball/stable" | tar xz -C "$D" --strip-components=1
bash "$D/linux/install.sh" --auto
echo "$(date '+%F %T') terminé"
