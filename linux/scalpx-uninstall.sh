#!/usr/bin/env bash
# ScalpX — désinstallation complète (installé par install.sh dans /usr/local/sbin/scalpx-uninstall, appartient à root).
# Lancé par le bot (🧨 Tout désinstaller) via sudo, ou à la main : sudo /usr/local/sbin/scalpx-uninstall
# Supprime : services scalpx-bot et scalpx-xvfb, utilisateur @USER@ et TOUT son dossier (bot, BASE, comptes MT5, préfixe Wine,
# historique), autorisations sudo, scripts ScalpX, paquet téléchargé à la main (~/scalpx-paquet), Wine et Xvfb.
# Python reste installé (Ubuntu en a besoin).
# Les positions restent chez le broker. Chaque étape est envoyée dans Telegram (le bot étant arrêté, c'est ce script qui
# écrit, avec le token lu dans le .env avant sa suppression) et notée dans /var/log/scalpx-uninstall.log.
set -u
U="@USER@"
LOG=/var/log/scalpx-uninstall.log
if [ -z "${SCALPX_DETACHED:-}" ]; then
    # hors du service du bot : la désinstallation continue quand le bot s'arrête
    exec systemd-run --quiet --collect --unit="scalpx-uninstall-$(date +%s)" -E SCALPX_DETACHED=1 /usr/local/sbin/scalpx-uninstall
fi
exec >>"$LOG" 2>&1
ENVF="/home/$U/scalpx/bot/.env"
val() { sed -n "s/^$1=//p" "$ENVF" 2>/dev/null | head -1 | tr -d '\r"'"'"' '; }
TOK=$(val TELEGRAM_TOKEN)
CHAT=$(val ALLOWED_CHAT_IDS); [ -n "$CHAT" ] || CHAT=$(val CHAT_ID); CHAT="${CHAT%%,*}"; CHAT="${CHAT%%;*}"
HOST=$(hostname)
step() {                                                  # étape : journal + message Telegram
    echo "$(date '+%F %T') $1"
    [ -n "$TOK" ] && [ -n "$CHAT" ] && curl -s --max-time 15 -o /dev/null "https://api.telegram.org/bot$TOK/sendMessage" \
        --data-urlencode "chat_id=$CHAT" --data-urlencode "text=$1" -d parse_mode=HTML -d disable_notification="${2:-true}"
}
sleep 4                                                    # laisse le bot envoyer son dernier message
step "🧨 <b>Désinstallation de ScalpX</b> ($HOST)
1/6 ⏹ arrêt du bot et de l'écran virtuel…"
systemctl disable --now scalpx-bot.service scalpx-xvfb.service
step "2/6 🔌 arrêt des terminaux MT5 et de Wine…"
pkill -TERM -u "$U"; sleep 5; pkill -KILL -u "$U"; sleep 2
step "3/6 🗑 suppression des services…"
rm -f /etc/systemd/system/scalpx-bot.service /etc/systemd/system/scalpx-xvfb.service
systemctl daemon-reload
step "4/6 🗑 suppression de l'utilisateur $U et de tout son dossier (bot, BASE, comptes, Wine, historique)…"
id "$U" >/dev/null 2>&1 && userdel -r "$U"
rm -rf "/home/$U"
step "5/6 🗑 suppression des autorisations, des scripts et du paquet téléchargé…"
PKGDIR=$(cat /etc/scalpx/paquet 2>/dev/null)
if [ -n "$PKGDIR" ] && [ -f "$PKGDIR/linux/install.sh" ] && [ -f "$PKGDIR/bot/main.py" ]; then
    rm -rf "$PKGDIR"                                      # ex. ~/scalpx-paquet (jamais un autre dossier)
fi
rm -f /etc/sudoers.d/scalpx /usr/local/sbin/scalpx-root-update /var/log/scalpx-root-update.log /var/log/scalpx-installation.log /tmp/mt5setup.exe /tmp/webview2.exe
rm -rf /etc/scalpx /root/scalpx-paquet.*
step "6/6 🗑 suppression de Wine et de l'écran virtuel (paquets installés pour ScalpX)…"
export DEBIAN_FRONTEND=noninteractive
apt-get purge -y -qq 'winehq-*' 'wine-stable*' 'wine-devel*' 'wine-staging*' wine wine64 wine32 xvfb cabextract >/dev/null 2>&1
rm -f /etc/apt/sources.list.d/winehq-*.sources /etc/apt/keyrings/winehq-archive.key
apt-get autoremove -y -qq >/dev/null 2>&1
apt-get update -qq >/dev/null 2>&1
if [ -d "/home/$U" ] || id "$U" >/dev/null 2>&1; then
    step "⚠️ Désinstallation incomplète : il reste des fichiers de $U. Journal : $LOG" false
else
    step "👋 <b>ScalpX est désinstallé de $HOST.</b>
Merci pour tout ce temps ensemble. Le bot de ce serveur ne répondra plus : tu peux le supprimer dans @BotFather.
Tes positions restent chez le broker, et tes presets t'attendent sur GitHub (☁️) pour le prochain serveur. Bonne chance ! 🍀" false
fi
rm -f /usr/local/sbin/scalpx-uninstall
