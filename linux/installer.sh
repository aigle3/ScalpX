#!/usr/bin/env bash
# ScalpX — installation sur un serveur Ubuntu neuf, en une ligne (dépôt public : aucun jeton) :
#
#   curl -fsSL https://raw.githubusercontent.com/aigle3/ScalpX/stable/linux/installer.sh | sudo bash
#
# 1. demande le token du bot Telegram et ton chat_id (les deux seules questions) ;
# 2. télécharge la version validée (branche stable) dans un dossier temporaire ;
# 3. lance linux/install.sh avec ces deux valeurs (rien d'autre à saisir), puis efface le dossier temporaire.
# La sauvegarde sur GitHub (facultative, privée, chiffrée) se propose ensuite dans Telegram.
set -euo pipefail
REPO="${SCALPX_REPO:-aigle3/ScalpX}"
API="https://api.telegram.org"
export SCALPX_REPO="$REPO"

say()  { echo -e "\e[36m$*\e[0m"; }
die()  { echo -e "\e[31m[ERREUR]\e[0m $*"; exit 1; }
ask()  { local r=""; read -rp "  $1 " r </dev/tty || true; echo "$r"; }
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1], {}, {'d': d}))" "$1" 2>/dev/null || true; }

[ "$(id -u)" -eq 0 ] || die "lance avec sudo : curl -fsSL …/installer.sh | sudo bash"
command -v curl >/dev/null && command -v python3 >/dev/null || { apt-get update -qq && apt-get install -y -qq curl python3 >/dev/null; }

# Appels réseau toujours limités dans le temps ; 2e essai forcé en IPv4 (IPv6 du serveur souvent mal configurée).
net() { curl -fsS --connect-timeout 10 --max-time 20 "$@" || curl -4 -fsS --connect-timeout 10 --max-time 20 "$@"; }

say "=== ScalpX : installation rapide"
echo "  Dans Telegram : @BotFather → /newbot → copie le TOKEN. Ton chat_id : écris à @userinfobot."
say "=== Connexion à Telegram…"
curl -s --connect-timeout 10 --max-time 20 -o /dev/null "$API" || curl -4 -s --connect-timeout 10 --max-time 20 -o /dev/null "$API" \
    || die "Telegram injoignable depuis ce serveur (api.telegram.org) : vérifie la connexion Internet / le DNS du VPS, puis relance"
TOK=""
for _ in 1 2 3; do
    TOK=$(ask "Token du bot Telegram :")
    TOK="${TOK//[[:space:]]/}"
    if [[ ! "$TOK" =~ ^[0-9]{6,}:[A-Za-z0-9_-]{30,}$ ]]; then
        echo "  ce n'est pas un token (forme 123456789:AA…), recommence"; TOK=""; continue
    fi
    echo "  vérification du token auprès de Telegram…"
    R=$(net "$API/bot$TOK/getMe" 2>/dev/null || true)
    [ "$(echo "$R" | json "d['ok']")" = "True" ] && { echo "  ✅ bot @$(echo "$R" | json "d['result']['username']")"; break; }
    echo "  token refusé par Telegram (ou révoqué), recommence"; TOK=""
done
[ -n "$TOK" ] || die "token Telegram requis"
CHAT=""
for _ in 1 2 3; do
    CHAT=$(ask "Ton chat_id (nombre) :")
    [[ "$CHAT" =~ ^-?[0-9]{5,}$ ]] && break
    echo "  un nombre, par exemple 123456789"; CHAT=""
done
[ -n "$CHAT" ] || die "chat_id requis"

D=$(mktemp -d /root/scalpx-paquet.XXXXXX)
trap 'rm -rf "$D"' EXIT
say "=== Téléchargement de ScalpX (version validée)…"
{ curl -fsSL --connect-timeout 15 --max-time 600 "https://codeload.github.com/$REPO/tar.gz/refs/heads/stable" \
  || curl -4 -fsSL --connect-timeout 15 --max-time 600 "https://codeload.github.com/$REPO/tar.gz/refs/heads/stable"; } \
  | tar xz -C "$D" --strip-components=1 \
  || die "téléchargement impossible (dépôt $REPO pas encore public, ou GitHub injoignable)"
[ -f "$D/linux/install.sh" ] || die "paquet incomplet"
TELEGRAM_TOKEN="$TOK" ALLOWED_CHAT_IDS="$CHAT" bash "$D/linux/install.sh"
