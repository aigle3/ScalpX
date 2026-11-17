#!/usr/bin/env bash
# ScalpX — désinstallation complète (installé par install.sh dans /usr/local/sbin/scalpx-uninstall, appartient à root).
# Lancé par le bot (🧨 Tout désinstaller) via sudo, ou à la main : sudo /usr/local/sbin/scalpx-uninstall
# Supprime : services scalpx-bot et scalpx-xvfb, utilisateur @USER@ et TOUT son dossier (bot, BASE, comptes MT5, préfixe Wine,
# historique), autorisations sudo et ce script. Wine, Xvfb et Python restent installés (paquets système).
# Les positions restent chez le broker.
set -u
U="@USER@"
if [ -z "${SCALPX_DETACHED:-}" ]; then
    # hors du service du bot : la désinstallation continue quand le bot s'arrête
    exec systemd-run --quiet --collect --unit="scalpx-uninstall-$(date +%s)" -E SCALPX_DETACHED=1 /usr/local/sbin/scalpx-uninstall
fi
sleep 5                                                    # laisse le bot envoyer son dernier message
systemctl disable --now scalpx-bot.service scalpx-xvfb.service 2>/dev/null
pkill -KILL -u "$U" 2>/dev/null
sleep 2
rm -f /etc/systemd/system/scalpx-bot.service /etc/systemd/system/scalpx-xvfb.service
systemctl daemon-reload
id "$U" >/dev/null 2>&1 && userdel -r "$U" 2>/dev/null
rm -rf "/home/$U"
rm -f /etc/sudoers.d/scalpx /usr/local/sbin/scalpx-uninstall /usr/local/sbin/scalpx-root-update /var/log/scalpx-root-update.log
logger "ScalpX désinstallé"
