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

step() { echo; echo -e "\e[36m=== $*\e[0m"; }
ok()   { echo -e "  \e[32m[OK]\e[0m $*"; }
warn() { echo -e "  \e[33m[!]\e[0m  $*"; }
die()  { echo -e "  \e[31m[ERREUR]\e[0m $*"; exit 1; }
ask()  { local r; read -rp "  $1 " r </dev/tty || true; echo "$r"; }
as_u() { sudo -u "$U" -H env WINEPREFIX="$PFX" WINEARCH=win64 DISPLAY="$DISP" WINEDEBUG=-all \
              WINEDLLOVERRIDES="mscoree,mshtml=" "$@"; }

[ "$(id -u)" -eq 0 ] || die "lance ce script avec sudo : sudo bash linux/install.sh"
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
as_u wine reg add 'HKEY_CURRENT_USER\Software\Wine' /v Version /t REG_SZ /d win10 /f >/dev/null 2>&1 || true
as_u wineserver -w || true
[ -f "$PFX/system.reg" ] || die "préfixe Wine non créé"
ok "préfixe prêt (Windows 10)"

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
tar -C "$PKG/bot" --exclude=.env --exclude=data --exclude=.venv --exclude=__pycache__ \
    $( [ -f "$BOT/config.toml" ] && echo --exclude=config.toml ) -cf - . | tar -C "$BOT" -xf -
chown -R "$U:$U" "$APP"
[ -x "$BOT/.venv/bin/python" ] || as_u "$PY" -m venv "$BOT/.venv"
as_u "$BOT/.venv/bin/pip" install --disable-pip-version-check -q -r "$BOT/requirements.txt"
ok "bibliothèques installées"

ENVF="$BOT/.env"
if [ ! -f "$ENVF" ]; then
    echo "  Dans Telegram : @BotFather -> /newbot -> copie le TOKEN. Ton chat_id : écris à @userinfobot."
    echo "  Si l'ancien bot Windows tourne encore, utilise un AUTRE token (deux bots sur le même token se gênent)."
    tok=""; for _ in 1 2 3; do tok=$(ask "TELEGRAM_TOKEN :"); [[ "$tok" =~ ^[0-9]{6,}:[A-Za-z0-9_-]{30,}$ ]] && break; warn "format attendu : 123456789:AAxx…"; done
    chat=""; for _ in 1 2 3; do chat=$(ask "Ton chat_id (nombre) :"); [[ "$chat" =~ ^-?[0-9]{5,}(,-?[0-9]{5,})*$ ]] && break; warn "format attendu : un nombre"; done
    gh=$(ask "Jeton GitHub en lecture seule (github_pat_…, Entrée = plus tard) :")
    hc=$(ask "URL healthchecks.io (Entrée = plus tard) :")
    umask 077
    { echo "TELEGRAM_TOKEN=$tok"; echo "ALLOWED_CHAT_IDS=$chat"
      [ -n "$gh" ] && echo "GITHUB_TOKEN=$gh" || echo "# GITHUB_TOKEN=github_pat_..."
      [ -n "$hc" ] && echo "HEALTHCHECK_URL=$hc" || echo "# HEALTHCHECK_URL=https://hc-ping.com/..."; } > "$ENVF"
    umask 022
    chown "$U:$U" "$ENVF"; chmod 600 "$ENVF"
    ok ".env écrit (lisible par $U seulement)"
else
    ok ".env existant conservé"
fi

CFG="$BOT/config.toml"
if ! grep -q '^\[wine\]' "$CFG"; then                     # config.toml du paquet (Windows) → chemins Linux
    as_u "$BOT/.venv/bin/python" - "$CFG" "$MT" "$PFX" "$APP/release" "$U" "$DISP" <<'PYEOF'
import re, sys
cfg, mt, pfx, rel, user, disp = sys.argv[1:]
t = open(cfg, encoding="utf-8-sig").read()
common = f"{pfx}/drive_c/users/{user}/AppData/Roaming/MetaQuotes/Terminal/Common/Files"
for key, val in (("base", f"{mt}/BASE"), ("instances", f"{mt}/instances"), ("archives", f"{mt}/archives"), ("source", rel)):
    t = re.sub(rf'(?m)^{key}\s*=\s*"[^"]*"', f'{key} = "{val}"', t, count=1)
t = re.sub(r'(?m)^#?\s*common_files\s*=.*$', f'common_files = "{common}"', t, count=1)
t = t.rstrip() + f'\n\n[wine]\ncmd     = "wine"\nprefix  = "{pfx}"\ndisplay = "{disp}"         # écran virtuel (service scalpx-xvfb)\n'
open(cfg, "w", encoding="utf-8").write(t)
PYEOF
    ok "config.toml adapté à Linux"
else
    ok "config.toml existant conservé"
fi
as_u mkdir -p "$PFX/drive_c/users/$U/AppData/Roaming/MetaQuotes/Terminal/Common/Files" "$MT/instances" "$MT/archives" "$APP/release"
(cd "$BOT" && as_u "$BOT/.venv/bin/python" main.py --check) || warn "contrôle incomplet : corrige les erreurs ci-dessus puis relance ce script"

# ---------------------------------------------------------------- 7. service
step "7/7  Service scalpx-bot (démarrage automatique, relance si arrêt)"
sed -e "s#@USER@#$U#g" -e "s#@BOT@#$BOT#g" -e "s#@PFX@#$PFX#g" -e "s#@DISP@#$DISP#g" \
    "$PKG/linux/scalpx-bot.service" > /etc/systemd/system/scalpx-bot.service
systemctl daemon-reload
systemctl enable scalpx-bot.service >/dev/null
systemctl restart scalpx-bot.service
sleep 5
systemctl is-active --quiet scalpx-bot.service && ok "bot démarré : ouvre Telegram" || warn "bot non démarré : journalctl -u scalpx-bot -n 50"

echo
echo "Terminé. Journal du bot : journalctl -u scalpx-bot -f   |   fichiers : $BOT/data/logs"
