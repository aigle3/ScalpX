#!/usr/bin/env bash
# ScalpX — installation sur un VPS Ubuntu (22.04, 24.04 ou 26.04), tout sur la même machine :
# Wine + écran virtuel Xvfb + MT5 (dossier BASE) + bot Telegram central (service systemd).
#
#   sudo bash linux/install.sh
#
# Relançable sans risque : ne touche jamais à .env, config.toml, data/ (s'ils existent), ni aux comptes déjà créés.
# Variables facultatives : SCALPX_USER (scalpx), MT5_SETUP_URL (installeur MT5 du broker, RoboForex par défaut).
set -euo pipefail

U="${SCALPX_USER:-scalpx}"
H="/home/$U"
APP="$H/scalpx"
BOT="$APP/bot"
MT="$H/mt5"
BASE="$MT/BASE"
PFX="$H/.wine"
DISP=":99"
# Installeur MT5 : celui de MetaQuotes (il se connecte aux serveurs RoboForex). Autre installeur : MT5_SETUP_URL=…
SETUP_URLS="${MT5_SETUP_URL:-https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe https://download.terminal.free/cdn/web/metaquotes.ltd/mt5/mt5setup.exe}"
PKG="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"          # racine du paquet (dossiers bot/, ea/, linux/)
AUTO=""; [ "${1:-}" = "--auto" ] && AUTO=1                         # --auto : relancé par le bot (🔧), aucune question

# Suivi dans Telegram (si le .env du bot a déjà le token et le chat_id : relance, 🔧) : chaque étape, les erreurs, la fin.
tg() {
    local f="/home/${SCALPX_USER:-scalpx}/scalpx/bot/.env" tk ch
    tk=$(sed -n 's/^TELEGRAM_TOKEN=//p' "$f" 2>/dev/null | head -1 | tr -d '\r"'"'"' ')
    ch=$(sed -n 's/^ALLOWED_CHAT_IDS=//p' "$f" 2>/dev/null | head -1 | tr -d '\r"'"'"' '); ch="${ch%%,*}"
    [ -n "$tk" ] && [ -n "$ch" ] && curl -s --max-time 15 -o /dev/null "https://api.telegram.org/bot$tk/sendMessage" \
        --data-urlencode "chat_id=$ch" --data-urlencode "text=$1" -d parse_mode=HTML -d disable_notification="${2:-true}" || true
}
lg()   { echo "$(date '+%H:%M:%S') $*" >> "${LOG:-/var/log/scalpx-installation.log}" 2>/dev/null || true; }
step() { echo; echo -e "\e[36m=== $*\e[0m"; lg "=== $*"; tg "🔧 Installation : $*"; }
ok()   { echo -e "  \e[32m[OK]\e[0m $*"; lg "[OK] $*"; }
warn() { echo -e "  \e[33m[!]\e[0m  $*"; lg "[!] $*"; tg "⚠️ Installation : $*"; }
die()  { echo -e "  \e[31m[ERREUR]\e[0m $*"; lg "[ERREUR] $*"; tg "❌ <b>Installation arrêtée</b> : $*" false; exit 1; }
ask()  { local r=""; [ -n "$AUTO" ] && { echo ""; return; }; printf '  %s ' "$1" >&2
         if [ -t 0 ]; then read -r r || true; else read -r r </dev/tty || true; fi; echo "$r"; }
as_u() { sudo -u "$U" -H env WINEPREFIX="$PFX" WINEARCH=win64 DISPLAY="$DISP" WINEDEBUG=-all "$@"; }

[ "$(id -u)" -eq 0 ] || die "lance ce script avec sudo : sudo bash linux/install.sh"
# Journal de l'installation (partagé avec installer.sh ; jamais de token dedans), écrit ligne à ligne par step/ok/warn/die.
LOG="${SCALPX_LOG:-/var/log/scalpx-installation.log}"
[ -n "${SCALPX_LOG:-}" ] || { touch "$LOG"; chmod 600 "$LOG"; echo "=== $(date '+%d/%m/%Y %H:%M:%S') · install.sh ${1:-}" >> "$LOG"; } 2>/dev/null || true
[ -f "$PKG/bot/main.py" ] && [ -d "$PKG/ea/MQL5/Experts" ] || die "paquet incomplet : lance le script depuis le dossier ScalpX téléchargé"
. /etc/os-release
[ "${ID:-}" = "ubuntu" ] || warn "système non Ubuntu ($PRETTY_NAME) : non testé"

# ---------------------------------------------------------------- 1. paquets
step "1/7  Paquets système (Wine, Xvfb, Python)"
export DEBIAN_FRONTEND=noninteractive
dpkg --add-architecture i386
apt-get update -qq
apt-get install -y -qq ca-certificates curl wget gnupg xvfb xauth cabextract python3 python3-venv >/dev/null
if ! command -v wine >/dev/null; then
    # WineHQ (version récente) ; clé convertie en binaire (Ubuntu 26.04 refuse la clé texte).
    # Dépôt absent ou refusé pour cette version d'Ubuntu : Wine d'Ubuntu à la place.
    SRC="/etc/apt/sources.list.d/winehq-${VERSION_CODENAME}.sources"
    mkdir -pm755 /etc/apt/keyrings
    if wget -qO- https://dl.winehq.org/wine-builds/winehq.key | gpg --dearmor --yes -o /etc/apt/keyrings/winehq-archive.key \
       && wget -qNP /etc/apt/sources.list.d/ "https://dl.winehq.org/wine-builds/ubuntu/dists/${VERSION_CODENAME}/winehq-${VERSION_CODENAME}.sources" \
       && apt-get update -qq && apt-get install -y -qq --install-recommends winehq-stable >/dev/null; then
        ok "Wine de WineHQ"
    else
        warn "dépôt WineHQ indisponible pour Ubuntu ${VERSION_ID} : Wine d'Ubuntu à la place"
        rm -f "$SRC"
        apt-get update -qq
        apt-get install -y -qq wine >/dev/null
        apt-get install -y -qq wine64 wine32:i386 >/dev/null 2>&1 || true     # 22.04 / 24.04 : paquets séparés
    fi
fi
command -v wine >/dev/null || die "Wine non installé"
ok "$(wine --version)"
PY=python3
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    apt-get install -y -qq python3.11 python3.11-venv >/dev/null || die "Python 3.11+ requis (Ubuntu 24.04 conseillé)"
    PY=python3.11
fi
ok "$($PY --version)"

# ---------------------------------------------------------------- 2. utilisateur + écran virtuel
step "2/7  Utilisateur $U (sans droits administrateur) et écran virtuel $DISP"
id "$U" >/dev/null 2>&1 || useradd -m -s /bin/bash "$U"
install -d -o "$U" -g "$U" "$APP" "$MT"
install -m 644 "$PKG/linux/scalpx-xvfb.service" /etc/systemd/system/scalpx-xvfb.service
sed -i "s/^User=.*/User=$U/" /etc/systemd/system/scalpx-xvfb.service
systemctl daemon-reload
systemctl enable --now scalpx-xvfb.service >/dev/null
sleep 2
systemctl is-active --quiet scalpx-xvfb.service || die "Xvfb ne démarre pas (journalctl -u scalpx-xvfb)"
ok "Xvfb actif"

# ---------------------------------------------------------------- 3. préfixe Wine
step "3/7  Préfixe Wine ($PFX)"
if [ ! -f "$PFX/system.reg" ]; then
    as_u wineboot -i >/dev/null 2>&1 || true
    as_u wineserver -w || true
fi
# comme le script Linux officiel de MetaQuotes. Pas de « wineserver -w » ici : à une relance, les terminaux MT5 tournent
# et il attendrait leur fermeture sans fin (blocage à l'étape 3).
as_u timeout 120 winecfg -v=win11 >/dev/null 2>&1 || true
[ -f "$PFX/system.reg" ] || die "préfixe Wine non créé"
ok "préfixe prêt (Windows 11)"
# L'installeur MT5 exige WebView2 (sinon il s'arrête aussitôt sur une erreur invisible : NtRaiseHardError)
if ! ls -d "$PFX/drive_c/Program Files (x86)/Microsoft/EdgeWebView" >/dev/null 2>&1; then
    echo "  Installation de Microsoft WebView2 (requis par l'installeur MT5)…"
    as_u curl -fsSL -o /tmp/webview2.exe "https://go.microsoft.com/fwlink/p/?LinkId=2124703" || die "téléchargement de WebView2 impossible"
    as_u timeout 600 wine /tmp/webview2.exe /silent /install >/dev/null 2>&1 || true
    pkill -u "$U" -i -f 'msedge|edgeupdate' || true      # services Edge restés lancés (sans toucher aux terminaux MT5)
    sleep 3
fi
ls -d "$PFX/drive_c/Program Files (x86)/Microsoft/EdgeWebView" >/dev/null 2>&1 && ok "WebView2 installé" || warn "WebView2 non détecté : l'installation de MT5 risque d'échouer"

# ---------------------------------------------------------------- 4. BASE MT5
step "4/7  MetaTrader 5 : dossier BASE ($BASE)"
if [ ! -f "$BASE/terminal64.exe" ]; then
    src=$(ls -d "$PFX/drive_c/Program Files/"*/ 2>/dev/null | while read -r d; do [ -f "$d/terminal64.exe" ] && echo "${d%/}"; done | head -1 || true)
    if [ -z "$src" ]; then
        got=""
        for url in $SETUP_URLS; do
            echo "  Téléchargement de l'installeur MT5 : $url"
            if as_u curl -fsSL -o /tmp/mt5setup.exe "$url"; then got=1; break; fi
        done
        [ -n "$got" ] || die "installeur MT5 introuvable : relance avec MT5_SETUP_URL=<lien de l'installeur> sudo -E bash linux/install.sh"
        echo "  Installation de MT5 dans Wine (plusieurs minutes)…"
        as_u wine /tmp/mt5setup.exe /auto >/dev/null 2>&1 &
        for _ in $(seq 1 120); do
            sleep 5
            src=$(ls -d "$PFX/drive_c/Program Files/"*/ 2>/dev/null | while read -r d; do [ -f "$d/terminal64.exe" ] && [ -f "$d/MetaEditor64.exe" ] && echo "${d%/}"; done | head -1 || true)
            [ -n "$src" ] && break
        done
        sleep 20                                            # fin de l'écriture des fichiers
        as_u wineserver -k || true                         # l'installeur ouvre MT5 à la fin : fermé
        sleep 3
        [ -n "$src" ] || die "MT5 non installé (aucun terminal64.exe dans Program Files du préfixe)"
    fi
    echo "  Copie de $src vers BASE…"
    as_u mkdir -p "$BASE"
    as_u cp -a "$src/." "$BASE/"
    echo "  Premier démarrage en mode portable (création de l'arborescence)…"
    as_u wine "$BASE/terminal64.exe" /portable >/dev/null 2>&1 &
    for _ in $(seq 1 60); do
        [ -d "$BASE/MQL5/Experts" ] && [ -d "$BASE/Profiles/Charts" ] && break
        sleep 2
    done
    sleep 10
    as_u wineserver -k || true
    sleep 3
    ok "BASE créé"
else
    ok "BASE déjà présent (conservé)"
fi
[ -f "$BASE/Config/accounts.dat" ] && warn "BASE contient un compte mémorisé (Config/accounts.dat) : BASE doit rester SANS compte, supprime ce fichier."

# ---------------------------------------------------------------- 5. EA
step "5/7  EA ScalpX dans BASE"
if [ ! -f "$BASE/MQL5/Experts/ScalpX_v5.mq5" ]; then
    as_u mkdir -p "$BASE/MQL5/Experts" "$BASE/MQL5/Presets"
    install -o "$U" -g "$U" -m 644 "$PKG"/ea/MQL5/Experts/* "$BASE/MQL5/Experts/"
    install -o "$U" -g "$U" -m 644 "$PKG"/ea/MQL5/Presets/* "$BASE/MQL5/Presets/"
    install -o "$U" -g "$U" -m 644 "$PKG/ea/version.txt" "$BASE/version.txt"
    ok "EA $(cat "$PKG/ea/version.txt") copié"
else
    ok "EA déjà présent : conservé (les mises à jour passent par le bot, bouton Installer dans Telegram)"
fi

# ---------------------------------------------------------------- 6. bot
step "6/7  Bot Python ($BOT)"
as_u mkdir -p "$BOT"
NEWCFG=""; [ -f "$BOT/config.toml" ] || NEWCFG=1
tar -C "$PKG/bot" --exclude=.env --exclude=data --exclude=.venv --exclude=__pycache__ \
    $( [ -f "$BOT/config.toml" ] && echo --exclude=config.toml ) -cf - . | tar -C "$BOT" -xf -
chown -R "$U:$U" "$APP"
[ -x "$BOT/.venv/bin/python" ] || as_u "$PY" -m venv "$BOT/.venv"
as_u "$BOT/.venv/bin/pip" install --disable-pip-version-check -q -r "$BOT/requirements.txt"
ok "bibliothèques installées"

ENVF="$BOT/.env"
# Seuls le token Telegram et ton chat_id sont demandés ici, et seulement s'ils manquent (relancer ce script ne
# redemande rien de ce qui est déjà là). Tout le reste (jetons GitHub, healthchecks.io) est demandé par le bot dans Telegram.
envget() { sed -n "s/^$1=//p" "$ENVF" 2>/dev/null | head -1 | tr -d '\r'; }
umask 077; touch "$ENVF"; umask 022
chown "$U:$U" "$ENVF"; chmod 600 "$ENVF"
# Valeurs déjà obtenues par l'installation en une ligne (linux/installer.sh) : écrites telles quelles, aucune question.
for k in TELEGRAM_TOKEN ALLOWED_CHAT_IDS; do
    v="${!k:-}"
    if [ -n "$v" ] && [ -z "$(envget "$k")" ] && [[ "$v" =~ ^[A-Za-z0-9:_,-]+$ ]]; then echo "$k=$v" >> "$ENVF"; fi
done
tok=$(envget TELEGRAM_TOKEN); chat=$(envget ALLOWED_CHAT_IDS)
if [ -z "$tok" ] || [ -z "$chat" ]; then
    [ -z "$AUTO" ] || die "token Telegram ou chat_id absent du .env : lance une fois sudo bash linux/install.sh"
    echo "  Dans Telegram : @BotFather -> /newbot -> copie le TOKEN. Ton chat_id : écris à @userinfobot."
fi
if [ -z "$tok" ]; then
    for _ in 1 2 3; do tok=$(ask "TELEGRAM_TOKEN :"); [[ "$tok" =~ ^[0-9]{6,}:[A-Za-z0-9_-]{30,}$ ]] && break; warn "format attendu : 123456789:AAxx…"; tok=""; done
    [ -n "$tok" ] || die "token Telegram requis"
    echo "TELEGRAM_TOKEN=$tok" >> "$ENVF"
fi
if [ -z "$chat" ]; then
    for _ in 1 2 3; do chat=$(ask "Ton chat_id (nombre) :"); [[ "$chat" =~ ^-?[0-9]{5,}(,-?[0-9]{5,})*$ ]] && break; warn "format attendu : un nombre"; chat=""; done
    [ -n "$chat" ] || die "chat_id requis"
    echo "ALLOWED_CHAT_IDS=$chat" >> "$ENVF"
fi
ok ".env prêt (lisible par $U seulement) : sauvegarde GitHub et healthchecks.io se règlent ensuite dans Telegram"

CFG="$BOT/config.toml"
if [ -n "$NEWCFG" ]; then                                # config.toml neuf : chemins de cet utilisateur
    as_u "$BOT/.venv/bin/python" - "$CFG" "$MT" "$PFX" "$APP/release" "$U" "$DISP" <<'PYEOF'
import re, sys
cfg, mt, pfx, rel, user, disp = sys.argv[1:]
t = open(cfg, encoding="utf-8-sig").read()
common = f"{pfx}/drive_c/users/{user}/AppData/Roaming/MetaQuotes/Terminal/Common/Files"
for key, val in (("base", f"{mt}/BASE"), ("instances", f"{mt}/instances"), ("archives", f"{mt}/archives"), ("source", rel)):
    t = re.sub(rf'(?m)^{key}\s*=\s*"[^"]*"', f'{key} = "{val}"', t, count=1)
t = re.sub(r'(?m)^#?\s*common_files\s*=\s*"[^"]*"', f'common_files = "{common}"', t, count=1)
t = re.sub(r'(?m)^prefix\s*=\s*"[^"]*"', f'prefix  = "{pfx}"', t, count=1)
t = re.sub(r'(?m)^display\s*=\s*"[^"]*"', f'display = "{disp}"', t, count=1)
open(cfg, "w", encoding="utf-8").write(t)
PYEOF
    ok "config.toml écrit pour $U"
else
    ok "config.toml existant conservé"
fi
as_u mkdir -p "$PFX/drive_c/users/$U/AppData/Roaming/MetaQuotes/Terminal/Common/Files" "$MT/instances" "$MT/archives" "$APP/release"
(cd "$BOT" && as_u "$BOT/.venv/bin/python" main.py --check) || warn "contrôle incomplet : corrige les erreurs ci-dessus puis relance ce script"

# ---------------------------------------------------------------- droits limités du bot (🖥 Serveur dans Telegram)
# Sans mot de passe, SEULEMENT : réactiver les 2 services ScalpX au démarrage, redémarrer le VPS, mettre à jour
# l'installation, tout désinstaller. Rien d'autre en administrateur.
SC=/usr/bin/systemctl
SUD=$(mktemp)
cat > "$SUD" <<SUDEOF
# ScalpX : commandes administrateur permises au bot (écrit par linux/install.sh)
$U ALL=(root) NOPASSWD: $SC reboot, $SC enable --now scalpx-bot.service, $SC enable --now scalpx-xvfb.service, /usr/local/sbin/scalpx-uninstall "", /usr/local/sbin/scalpx-root-update ""
SUDEOF
if visudo -cf "$SUD" >/dev/null; then
    install -m 440 -o root -g root "$SUD" /etc/sudoers.d/scalpx
    ok "droits limités du bot installés (démarrage auto, redémarrage du VPS, mise à jour, désinstallation)"
else
    warn "règle sudo refusée par visudo : boutons 🖥 Serveur sans effet"
fi
rm -f "$SUD"
sed "s#@USER@#$U#g" "$PKG/linux/scalpx-uninstall.sh" > /usr/local/sbin/scalpx-uninstall
chown root:root /usr/local/sbin/scalpx-uninstall
chmod 755 /usr/local/sbin/scalpx-uninstall
# 🔧 mise à jour de l'installation depuis Telegram : script root qui télécharge LUI-MÊME la branche stable du dépôt
# (le bot ne peut pas lui faire exécuter ses propres fichiers) puis relance ce script en mode --auto
REPO=$(sed -n 's/^github *= *"\([^"]*\)".*/\1/p' "$BOT/config.toml" | head -1); REPO="${REPO:-aigle3/ScalpX}"
sed -e "s#@USER@#$U#g" -e "s#@REPO@#$REPO#g" "$PKG/linux/scalpx-root-update.sh" > /usr/local/sbin/scalpx-root-update
chown root:root /usr/local/sbin/scalpx-root-update
chmod 755 /usr/local/sbin/scalpx-root-update

# empreinte de l'installation système (le bot propose 🔔 🔧 quand le paquet en a une plus récente) : même calcul que
# core/server.py linux_digest (fichiers de linux/ hors .md, par nom : « nom\n » + contenu, sha256, 16 caractères)
install -d -m 755 /etc/scalpx
# dossier du paquet téléchargé à la main (ex. ~/scalpx-paquet) : supprimé par la désinstallation
case "$PKG" in "$APP"/*|/root/scalpx-paquet.*) ;; *) echo "$PKG" > /etc/scalpx/paquet ;; esac
( cd "$PKG/linux" && for f in $(ls -1 | grep -v '\.md$' | LC_ALL=C sort); do [ -f "$f" ] && { printf '%s\n' "$f"; cat "$f"; }; done ) \
    | sha256sum | cut -c1-16 > /etc/scalpx/installation
chmod 644 /etc/scalpx/installation

# ---------------------------------------------------------------- 7. service
step "7/7  Service scalpx-bot (démarrage automatique, relance si arrêt)"
sed -e "s#@USER@#$U#g" -e "s#@BOT@#$BOT#g" -e "s#@PFX@#$PFX#g" -e "s#@DISP@#$DISP#g" \
    "$PKG/linux/scalpx-bot.service" > /etc/systemd/system/scalpx-bot.service
systemctl daemon-reload
systemctl enable scalpx-bot.service >/dev/null
tg "✅ <b>Installation terminée</b> ($(hostname)) : le bot redémarre (≈ 10 s)." false
systemctl restart scalpx-bot.service
sleep 5
systemctl is-active --quiet scalpx-bot.service && ok "bot démarré : ouvre Telegram" || warn "bot non démarré : journalctl -u scalpx-bot -n 50"

echo
echo "Terminé. Journal du bot : journalctl -u scalpx-bot -f   |   fichiers : $BOT/data/logs"
# Paquet téléchargé à la main (ex. ~/scalpx-paquet) : plus utile, le bot garde sa propre copie (release) → supprimé.
case "$PKG" in
    "$APP"/*) ;;
    *) if [ -f "$PKG/linux/install.sh" ] && [ -f "$PKG/bot/main.py" ]; then
           cd / && rm -rf "$PKG" && rm -f /etc/scalpx/paquet && echo "Paquet d'installation supprimé ($PKG)."
       fi ;;
esac
