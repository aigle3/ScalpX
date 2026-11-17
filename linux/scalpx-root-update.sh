#!/usr/bin/env bash
# ScalpX — mise à jour de l'installation (droits administrateur), lancée depuis Telegram : 🖥 Serveur → 🔧.
# Installé par install.sh dans /usr/local/sbin/scalpx-root-update (appartient à root). Télécharge lui-même la branche
# « stable » de @REPO@ (dépôt public) puis relance linux/install.sh --auto : aucune question,
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
D=$(mktemp -d /root/scalpx-paquet.XXXXXX)
trap 'rm -rf "$D"' EXIT
curl -fsSL "https://codeload.github.com/$REPO/tar.gz/refs/heads/stable" | tar xz -C "$D" --strip-components=1   # dépôt public
bash "$D/linux/install.sh" --auto
echo "$(date '+%F %T') terminé"
