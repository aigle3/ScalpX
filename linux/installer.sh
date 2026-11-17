#!/usr/bin/env bash
# ScalpX — installation rapide sur un serveur Ubuntu neuf, en une ligne (aucun jeton dans la commande) :
#
#   curl -fsSL https://raw.githubusercontent.com/aigle3/scalpx-installation/main/installer.sh | sudo bash
#
# Ce fichier est PUBLIC (dépôt aigle3/scalpx-installation) : il ne contient aucun secret ni code de ScalpX.
# 1. demande le token du bot Telegram et ton chat_id (les seules questions) ;
# 2. te demande le jeton GitHub DANS TELEGRAM (ton message est effacé aussitôt) ;
# 3. télécharge la version validée (branche stable du dépôt privé) dans un dossier temporaire ;
# 4. lance linux/install.sh avec ces trois valeurs (rien d'autre à saisir), puis efface le dossier temporaire.
set -euo pipefail
REPO="${SCALPX_REPO:-aigle3/ScalpX}"
API="https://api.telegram.org"

say()  { echo -e "\e[36m$*\e[0m"; }
die()  { echo -e "\e[31m[ERREUR]\e[0m $*"; exit 1; }
ask()  { local r=""; read -rp "  $1 " r </dev/tty || true; echo "$r"; }
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1], {}, {'d': d}))" "$1" 2>/dev/null || true; }

[ "$(id -u)" -eq 0 ] || die "lance avec sudo : curl -fsSL …/installer.sh | sudo bash"
command -v curl >/dev/null && command -v python3 >/dev/null || { apt-get update -qq && apt-get install -y -qq curl python3 >/dev/null; }

say "=== ScalpX : installation rapide"
echo "  Dans Telegram : @BotFather → /newbot → copie le TOKEN. Ton chat_id : écris à @userinfobot."
TOK=""
for _ in 1 2 3; do
    TOK=$(ask "Token du bot Telegram :")
    [[ "$TOK" =~ ^[0-9]{6,}:[A-Za-z0-9_-]{30,}$ ]] && [ "$(curl -fsS "$API/bot$TOK/getMe" | json "d['ok']")" = "True" ] && break
    echo "  token refusé par Telegram, recommence"; TOK=""
done
[ -n "$TOK" ] || die "token Telegram requis"
CHAT=""
for _ in 1 2 3; do
    CHAT=$(ask "Ton chat_id (nombre) :")
    [[ "$CHAT" =~ ^-?[0-9]{5,}$ ]] && break
    echo "  un nombre, par exemple 123456789"; CHAT=""
done
[ -n "$CHAT" ] || die "chat_id requis"

tg() { curl -fsS -o /dev/null "$API/bot$TOK/sendMessage" --data-urlencode "chat_id=$CHAT" --data-urlencode "text=$1" -d parse_mode=HTML || true; }
# messages en attente oubliés : seule la réponse qui suit compte
OFF=$(curl -fsS "$API/bot$TOK/getUpdates?timeout=0" | json "max([u['update_id'] for u in d['result']] or [-1]) + 1")
OFF="${OFF:-0}"

GH=""
tg "🔑 <b>Installation de ScalpX sur $(hostname)</b>
Envoie-moi ici ton <b>jeton GitHub</b> de lecture du dépôt ScalpX (github_pat_…). Je l'efface de la conversation dès réception."
say "=== Jeton GitHub : réponds au message que le bot vient d'envoyer dans Telegram (15 min max)…"
END=$(( $(date +%s) + 900 ))
while [ -z "$GH" ] && [ "$(date +%s)" -lt "$END" ]; do
    R=$(curl -fsS "$API/bot$TOK/getUpdates?timeout=25&offset=$OFF" || echo '{"result":[]}')
    while IFS=$'\t' read -r uid mid chat text; do
        [ -n "$uid" ] || continue
        OFF=$((uid + 1))
        [ "$chat" = "$CHAT" ] || continue
        curl -fsS -o /dev/null "$API/bot$TOK/deleteMessage" -d chat_id="$CHAT" -d message_id="$mid" || true
        if [[ "$text" =~ ^(github_pat_[A-Za-z0-9_]{20,}|ghp_[A-Za-z0-9]{30,})$ ]]; then
            if curl -fsS -o /dev/null -H "Authorization: Bearer $text" "https://api.github.com/repos/$REPO"; then
                GH="$text"; tg "✅ Jeton accepté : installation en cours, je te tiens au courant ici."
            else
                tg "❌ Ce jeton n'a pas accès au dépôt $REPO. Renvoie un jeton valide."
            fi
        else
            tg "Ce n'est pas un jeton GitHub (il commence par github_pat_). Renvoie-le."
        fi
    done < <(echo "$R" | python3 -c "
import json, sys
for u in json.load(sys.stdin).get('result', []):
    m = u.get('message') or {}
    print(u['update_id'], m.get('message_id', ''), (m.get('chat') or {}).get('id', ''), (m.get('text') or '').strip(), sep='\t')
" 2>/dev/null || true)
done
curl -fsS -o /dev/null "$API/bot$TOK/getUpdates?timeout=0&offset=$OFF" || true     # réponses traitées : acquittées
[ -n "$GH" ] || die "aucun jeton GitHub reçu dans Telegram"

D=$(mktemp -d /root/scalpx-paquet.XXXXXX)
trap 'rm -rf "$D"' EXIT
say "=== Téléchargement de ScalpX (version validée)…"
curl -fsSL -H "Authorization: Bearer $GH" "https://api.github.com/repos/$REPO/tarball/stable" | tar xz -C "$D" --strip-components=1
[ -f "$D/linux/install.sh" ] || die "paquet incomplet"
TELEGRAM_TOKEN="$TOK" ALLOWED_CHAT_IDS="$CHAT" GITHUB_TOKEN="$GH" bash "$D/linux/install.sh"
