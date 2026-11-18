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
# Installeur RoboForex d'abord (lien du bouton « Télécharger pour PC » de roboforex.com) : il contient la liste des
# serveurs RoboForex (Config/servers.dat). Celui de MetaQuotes ne connaît pas « RoboForex-Pro » : les comptes ne se
# connectent jamais (aucune ligne « Network » dans le journal du terminal).
ROBO_SETUP="${ROBO_SETUP_URL-https://download.terminal.free/cdn/web/22605/mt5/roboforexltd5setup.exe}"
SETUP_URLS="${MT5_SETUP_URL:-$ROBO_SETUP https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe https://download.terminal.free/cdn/web/metaquotes.ltd/mt5/mt5setup.exe}"
PKG="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"          # racine du paquet (dossiers bot/, ea/, linux/)
AUTO=""; [ "${1:-}" = "--auto" ] && AUTO=1                         # --auto : relancé par le bot (🔧), aucune question

# Suivi dans Telegram (si le .env du bot a déjà le token et le chat_id : relance, 🔧) : chaque étape, les erreurs, la fin.
tg() {
    # Jamais d'échec ici (serveur neuf : pas encore de .env ; set -e + pipefail coupaient l'installation sans message).
    local f="/home/${SCALPX_USER:-scalpx}/scalpx/bot/.env" tk="" ch=""
    if [ -r "$f" ]; then
        tk=$(sed -n 's/^TELEGRAM_TOKEN=//p' "$f" 2>/dev/null | head -1 | tr -d '\r"'"'"' ' || true)
        ch=$(sed -n 's/^ALLOWED_CHAT_IDS=//p' "$f" 2>/dev/null | head -1 | tr -d '\r"'"'"' ' || true); ch="${ch%%,*}"
    fi
    [ -n "$tk" ] || tk="${TELEGRAM_TOKEN:-}"; [ -n "$ch" ] || { ch="${ALLOWED_CHAT_IDS:-}"; ch="${ch%%,*}"; }
    [ -n "$tk" ] && [ -n "$ch" ] && curl -s --max-time 15 -o /dev/null "https://api.telegram.org/bot$tk/sendMessage" \
        --data-urlencode "chat_id=$ch" --data-urlencode "text=$1" -d parse_mode=HTML -d disable_notification="${2:-true}" || true
}
lg()   { echo "$(date '+%H:%M:%S') $*" >> "${LOG:-/var/log/scalpx-installation.log}" 2>/dev/null || true; }
step() { echo; echo -e "\e[36m=== $*\e[0m"; lg "=== $*"; tg "🔧 Installation : $*"; }
ok()   { echo -e "  \e[32m[OK]\e[0m $*"; lg "[OK] $*"; }
warn() { echo -e "  \e[33m[!]\e[0m  $*"; lg "[!] $*"; tg "⚠️ Installation : $*"; }
die()  { DIED=1; echo -e "  \e[31m[ERREUR]\e[0m $*"; lg "[ERREUR] $*"; tg "❌ <b>Installation arrêtée</b> : $*" false; exit 1; }
# Filet : toute erreur imprévue s'affiche (ligne et commande) au lieu d'un arrêt muet.
# Filet : un arrêt imprévu (set -e) s'affiche avec la commande en cause, au lieu d'un arrêt muet.
DIED=""; CLEAN=""
on_exit() { local rc=$1 cmd=$2; set +e; [ -n "$CLEAN" ] && rm -rf "$CLEAN"
            if [ "$rc" -ne 0 ] && [ -z "$DIED" ]; then die "arrêt inattendu (code $rc) sur : ${cmd:0:160}"; fi; }
trap 'on_exit $? "$BASH_COMMAND"' EXIT
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
# VPS neuf : les mises à jour automatiques d'Ubuntu tiennent souvent apt quelques minutes → on attend (10 min max).
apt-get() { command apt-get -o DPkg::Lock::Timeout=600 -o Acquire::http::Timeout=60 -o Acquire::Retries=3 "$@"; }
if pgrep -x unattended-upgr >/dev/null 2>&1 || pgrep -f apt.systemd.daily >/dev/null 2>&1; then
    echo "  Mises à jour automatiques d'Ubuntu en cours : attente de leur fin (10 min max)…"
fi
dpkg --add-architecture i386
apt-get update -qq
apt-get install -y -qq ca-certificates curl wget gnupg xvfb xauth cabextract python3 python3-venv >/dev/null
# Version de Wine : l'installeur et le terminal MT5 refusent de démarrer (« a debugger has been found running ») avec
# Wine 10.3 à 11.6, dont Wine 11.0 « stable » de WineHQ. Il faut Wine ≤ 10.2 ou ≥ 11.7 (branche devel de WineHQ).
wine_mm() { wine --version 2>/dev/null | sed -nE 's/^wine-([0-9]+)\.([0-9]+).*/\1 \2/p'; }
wine_mt5_ok() {
    local M m; read -r M m <<<"$(wine_mm)"
    [ -n "${M:-}" ] || return 1
    if [ "$M" -eq 10 ] && [ "$m" -ge 3 ]; then return 1; fi
    if [ "$M" -eq 11 ] && [ "$m" -lt 7 ]; then return 1; fi
    return 0
}
winehq_repo() {                                             # dépôt WineHQ (clé convertie en binaire : exigée par Ubuntu 26.04)
    mkdir -pm755 /etc/apt/keyrings
    wget -q --timeout=30 --tries=2 -O- https://dl.winehq.org/wine-builds/winehq.key | gpg --dearmor --yes -o /etc/apt/keyrings/winehq-archive.key \
      && wget -q --timeout=30 --tries=2 -NP /etc/apt/sources.list.d/ "https://dl.winehq.org/wine-builds/ubuntu/dists/${VERSION_CODENAME}/winehq-${VERSION_CODENAME}.sources" \
      && apt-get update -qq
}
WINE_CHANGED=""
# Changer de Wine seulement s'il n'y a pas encore de MT5 (BASE) : un serveur qui tourne garde son Wine.
if ! command -v wine >/dev/null || { ! wine_mt5_ok && [ ! -f "$BASE/terminal64.exe" ]; }; then
    command -v wine >/dev/null && echo "  $(wine --version) refusé par MT5 (contrôle anti-débogueur) : passage à Wine devel de WineHQ (≥ 11.7)"
    got=""
    if winehq_repo; then
        for pkg in winehq-devel winehq-staging; do
            apt-get remove -y -qq winehq-stable wine-stable wine-stable-amd64 wine-stable-i386 wine wine64 wine32:i386 >/dev/null 2>&1 || true
            if apt-get install -y -qq --install-recommends "$pkg" >>"$LOG" 2>&1 && wine_mt5_ok; then got="$pkg"; break; fi
        done
    fi
    if [ -n "$got" ]; then
        WINE_CHANGED=1
        ok "Wine de WineHQ ($got)"
    elif ! command -v wine >/dev/null; then
        warn "Wine devel de WineHQ indisponible : Wine d'Ubuntu à la place"
        rm -f "/etc/apt/sources.list.d/winehq-${VERSION_CODENAME}.sources"; apt-get update -qq
        apt-get install -y -qq wine >/dev/null
        apt-get install -y -qq wine64 wine32:i386 >/dev/null 2>&1 || true     # 22.04 / 24.04 : paquets séparés
    fi
    wine_mt5_ok || warn "$(wine --version 2>/dev/null) : version connue pour bloquer MT5 (il faut ≤ 10.2 ou ≥ 11.7)"
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
# Préfixe en état de marche ? (« could not load kernel32.dll » = préfixe à moitié créé, ex. installation interrompue)
pfx_ok() { as_u timeout 120 wine cmd /c ver >>"$LOG" 2>&1; }
if [ -f "$PFX/system.reg" ] && { [ -n "$WINE_CHANGED" ] || ! pfx_ok; }; then
    if [ -d "$MT/instances" ] && ls "$MT/instances" 2>/dev/null | grep -q .; then
        die "préfixe Wine ($PFX) hors service alors que des comptes existent : contacte le support avant de le recréer"
    fi
    if [ -n "$WINE_CHANGED" ]; then echo "  Nouveau Wine : préfixe recréé"; else warn "préfixe Wine cassé (installation interrompue ?) : recréé"; fi
    as_u wineserver -k 2>/dev/null || true; pkill -u "$U" -f 'wine|\.exe' 2>/dev/null || true; sleep 2
    rm -rf "$PFX"
fi
if [ ! -f "$PFX/system.reg" ]; then
    echo "  Création du préfixe Wine (1 à 3 min)…"
    # Fenêtres « installer Wine Mono / Gecko » invisibles sur l'écran virtuel (wineboot attendait sans fin) :
    # - Gecko (moteur HTML) est NÉCESSAIRE à l'interface de l'installeur MT5 : pré-téléchargé dans le cache de Wine,
    #   il s'installe sans fenêtre ;
    # - Mono (.NET) n'est pas utilisé par MT5 : désactivé.
    GK=2.47.4; as_u mkdir -p "$H/.cache/wine"
    for a in x86 x86_64; do
        f="$H/.cache/wine/wine-gecko-$GK-$a.msi"
        [ -s "$f" ] || as_u curl -fsSL --connect-timeout 20 --max-time 300 -o "$f" "https://dl.winehq.org/wine/wine-gecko/$GK/wine-gecko-$GK-$a.msi" \
            || { rm -f "$f"; warn "Gecko $a non téléchargé : l'installeur MT5 risque de ne pas s'afficher"; }
    done
    as_u env WINEDLLOVERRIDES="mscoree=" timeout 300 wineboot -i >>"$LOG" 2>&1 || warn "wineboot : délai dépassé, on continue"
    as_u timeout 60 wineserver -w || as_u wineserver -k || true
fi
# comme le script Linux officiel de MetaQuotes. Pas de « wineserver -w » ici : à une relance, les terminaux MT5 tournent
# et il attendrait leur fermeture sans fin (blocage à l'étape 3).
as_u env WINEDLLOVERRIDES="mscoree=" timeout 120 winecfg -v=win11 >/dev/null 2>&1 || true
# MT5 refuse de démarrer s'il croit voir un débogueur : Wine déclare winedbg comme débogueur système (clé AeDebug).
for k in 'HKLM\Software\Microsoft\Windows NT\CurrentVersion\AeDebug' 'HKLM\Software\Wow6432Node\Microsoft\Windows NT\CurrentVersion\AeDebug'; do
    as_u timeout 60 wine reg delete "$k" /v Debugger /f >>"$LOG" 2>&1 || true
done
[ -f "$PFX/system.reg" ] || die "préfixe Wine non créé (détail : $LOG)"
pfx_ok || die "Wine ne démarre pas dans le préfixe neuf ($(wine --version 2>/dev/null)) : détail dans $LOG"
ok "préfixe prêt (Windows 11)"
# L'installeur MT5 exige WebView2 (sinon il s'arrête aussitôt sur une erreur invisible : NtRaiseHardError)
if ! ls "$PFX/drive_c/Program Files (x86)/Microsoft/EdgeWebView/Application"/*/msedgewebview2.exe >/dev/null 2>&1; then
    echo "  Installation de Microsoft WebView2 (requis par l'installeur MT5, 2 à 10 min)…"; tg "🔧 Installation : WebView2 (2 à 10 min)…"
    # 1er choix : installeur COMPLET hors ligne (x64, ~180 Mo) : n'a pas besoin du service de téléchargement de Microsoft,
    # qui ne démarre pas sous Wine. Secours : le petit lanceur (il télécharge lui-même en arrière-plan).
    if as_u curl -fsSL --connect-timeout 20 --max-time 900 -o /tmp/webview2.exe "https://go.microsoft.com/fwlink/?linkid=2124701"; then
        echo "  installeur complet téléchargé ($(du -h /tmp/webview2.exe | cut -f1))"
    else
        as_u curl -fsSL --connect-timeout 20 --max-time 600 -o /tmp/webview2.exe "https://go.microsoft.com/fwlink/p/?LinkId=2124703" \
            || die "téléchargement de WebView2 impossible"
    fi
    as_u env WINEDEBUG=err+all timeout 900 wine /tmp/webview2.exe /silent /install >>"$LOG" 2>&1 || true   # erreurs Wine → journal
    # Ce fichier n'est qu'un lanceur : il rend la main tout de suite et le service Microsoft (MicrosoftEdgeUpdate)
    # télécharge puis installe WebView2 en arrière-plan. On attend qu'il ait fini (10 min max) AVANT de l'arrêter.
    WV="$PFX/drive_c/Program Files (x86)/Microsoft/EdgeWebView/Application"
    for i in $(seq 1 120); do
        ls "$WV"/*/msedgewebview2.exe >/dev/null 2>&1 && break
        [ $((i % 12)) -eq 0 ] && echo "  … WebView2 encore en cours ($((i / 12)) min)"
        sleep 5
    done
    sleep 10                                                # fin de l'écriture des fichiers
    pkill -u "$U" -i -f 'msedge|edgeupdate' || true      # services Edge restés lancés (sans toucher aux terminaux MT5)
    sleep 3
fi
if ls "$PFX/drive_c/Program Files (x86)/Microsoft/EdgeWebView/Application"/*/msedgewebview2.exe >/dev/null 2>&1; then
    ok "WebView2 installé"
else
    warn "WebView2 non détecté : l'installation de MT5 risque d'échouer (relancer l'installation le réessaie)"
fi

# ---------------------------------------------------------------- 4. BASE MT5
step "4/7  MetaTrader 5 : dossier BASE ($BASE)"
# MT5 installés dans Program Files du préfixe (RoboForex en premier)
mt5_dirs() { ls -d "$PFX/drive_c/Program Files/"*/ 2>/dev/null | while read -r d; do
                 [ -f "$d/terminal64.exe" ] && [ -f "$d/MetaEditor64.exe" ] && echo "${d%/}"; done | sort -f | awk '/[Rr]obo/{print; next} {o=o $0 "\n"} END{printf "%s", o}'; }
if [ ! -f "$BASE/terminal64.exe" ]; then
    src=$(mt5_dirs | head -1 || true)
    if [ -z "$src" ]; then
        got=""
        for url in $SETUP_URLS; do
            echo "  Téléchargement de l'installeur MT5 : $url"
            if as_u curl -fsSL --connect-timeout 20 --max-time 900 -o /tmp/mt5setup.exe "$url"; then got=1; break; fi
        done
        [ -n "$got" ] || die "installeur MT5 introuvable : relance avec MT5_SETUP_URL=<lien de l'installeur> sudo -E bash linux/install.sh"
        echo "  Installation de MT5 dans Wine (plusieurs minutes)…"
        as_u env WINEDEBUG=err+all wine /tmp/mt5setup.exe /auto >>"$LOG" 2>&1 &   # erreurs Wine → journal
        for _ in $(seq 1 120); do
            sleep 5
            src=$(mt5_dirs | head -1 || true)
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
    ok "BASE créé"
fi
# Serveurs RoboForex dans BASE (Config/servers.dat). BASE venu de l'installeur MetaQuotes (Bot ≤ 4.6) : l'installeur
# RoboForex est installé à part (C:\scalpx-roboforex), sa liste de serveurs copiée dans BASE, puis ce dossier supprimé.
# Ni BASE (déjà compilé) ni les comptes ne sont touchés ; seuls les processus de ce dossier à part sont fermés.
RF_MARK="$BASE/.scalpx_serveurs_roboforex"
if [ -f "$BASE/terminal64.exe" ] && [ ! -f "$RF_MARK" ] && [ -n "$ROBO_SETUP" ]; then
    echo "  Serveurs RoboForex pour MT5 (2 à 5 min)…"; tg "🔧 Installation : serveurs RoboForex pour MT5…"
    sd=""
    rob=$(mt5_dirs | grep -i robo | head -1 || true)
    [ -n "$rob" ] && sd=$(find "$rob" -maxdepth 2 -iname servers.dat 2>/dev/null | head -1 || true)
    if [ -z "$sd" ]; then
        RFD="$PFX/drive_c/scalpx-roboforex"; t0=$(mktemp); rm -rf "$RFD"
        if as_u curl -fsSL --connect-timeout 20 --max-time 900 -o /tmp/rf5setup.exe "$ROBO_SETUP"; then
            as_u env WINEDEBUG=err+all wine /tmp/rf5setup.exe /auto '/path:C:\scalpx-roboforex' >>"$LOG" 2>&1 &
            for i in $(seq 1 120); do                       # 10 min max
                sleep 5
                sd=$(find "$RFD" "$PFX/drive_c/users/$U/AppData/Roaming/MetaQuotes/Terminal" -iname servers.dat -newer "$t0" 2>/dev/null | head -1 || true)
                [ -n "$sd" ] && break
                [ $((i % 12)) -eq 0 ] && echo "  … installeur RoboForex en cours ($((i / 12)) min)"
            done
            sleep 10
        else
            warn "installeur RoboForex non téléchargé ($ROBO_SETUP)"
        fi
        pkill -u "$U" -f 'scalpx-roboforex|rf5setup' || true      # SEULEMENT ce MT5 à part (jamais BASE ni les comptes)
        sleep 3
        rm -f "$t0"
    fi
    if [ -n "$sd" ] && [ -s "$sd" ]; then
        as_u mkdir -p "$BASE/Config"
        [ -f "$BASE/Config/servers.dat" ] && [ ! -f "$BASE/Config/servers.dat.metaquotes" ] && cp -a "$BASE/Config/servers.dat" "$BASE/Config/servers.dat.metaquotes"
        install -o "$U" -g "$U" -m 644 "$sd" "$BASE/Config/servers.dat"
        touch "$RF_MARK"; chown "$U:$U" "$RF_MARK"
        ok "serveurs RoboForex ajoutés à BASE"
    else
        warn "serveurs RoboForex introuvables : les comptes RoboForex ne pourront pas se connecter (relance 🔧 plus tard)"
    fi
    rm -rf "$PFX/drive_c/scalpx-roboforex" /tmp/rf5setup.exe
fi
# Premier démarrage de MT5 dans BASE : il compile alors ~130 programmes MetaQuotes (« full recompilation »). On attend la
# fin (25 min max) : sinon CHAQUE compte créé refait ce travail (minutes à 100 % de CPU, aucun signal de l'EA).
# Marque « BASE prêt » = taille:date de terminal64.exe (même calcul que le bot, mt5/prepare.py).
base_ready() { [ -f "$BASE/.scalpx_base_prete" ] && [ "$(cat "$BASE/.scalpx_base_prete")" = "$(stat -c '%s:%Y' "$BASE/terminal64.exe")" ]; }
journal() { for f in "$BASE"/logs/*.log; do [ -f "$f" ] && iconv -f UTF-16LE -t UTF-8 "$f" 2>/dev/null; done | tr -d '\r' || true; }
HAS_ACCOUNTS=""; ls "$MT/instances" 2>/dev/null | grep -q . && HAS_ACCOUNTS=1
if [ -f "$BASE/terminal64.exe" ] && ! base_ready && [ -z "$HAS_ACCOUNTS" ]; then
    echo "  Premier démarrage de MT5 dans BASE : compilation de départ (2 à 10 min)…"; tg "🔧 Installation : compilation de départ de MT5 (2 à 10 min)…"
    n0=$(journal | grep -c "recompilation has been finished" || true)
    as_u wine "$BASE/terminal64.exe" /portable >/dev/null 2>&1 &
    started=""; done_=""
    for i in $(seq 1 300); do                               # 300 × 5 s = 25 min
        sleep 5
        j=$(journal)
        [ "$(printf '%s' "$j" | grep -c "recompilation has been finished" || true)" -gt "${n0:-0}" ] && { done_=1; break; }
        printf '%s' "$j" | grep -q "recompilation has been started" && started=1
        [ -z "$started" ] && [ "$i" -ge 24 ] && [ -d "$BASE/MQL5/Experts" ] && { done_=1; break; }   # 2 min sans compilation : rien à faire
        [ $((i % 12)) -eq 0 ] && echo "  … compilation en cours ($((i / 12)) min)"
    done
    sleep 5
    pkill -u "$U" -f 'mt5[/\\]BASE[/\\]' || true                 # SEULEMENT MT5 / MetaEditor de BASE (jamais les comptes)
    sleep 3
    if [ -n "$done_" ]; then
        stat -c '%s:%Y' "$BASE/terminal64.exe" > "$BASE/.scalpx_base_prete"; chown "$U:$U" "$BASE/.scalpx_base_prete"
        ok "MT5 prêt (compilation de départ terminée)"
    else
        warn "compilation de départ de MT5 non terminée : le bot la reprendra tout seul (création de comptes en attente)"
    fi
else
    if [ -n "$HAS_ACCOUNTS" ] && ! base_ready; then ok "BASE conservé (préparation de MT5 faite par le bot, sans toucher aux comptes)"
    else ok "BASE déjà présent et prêt (conservé)"; fi
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
echo "  Bibliothèques Python (1 à 5 min)…"
as_u timeout 1800 "$BOT/.venv/bin/pip" install --disable-pip-version-check -q --timeout 60 --retries 3 -r "$BOT/requirements.txt" \
    || die "bibliothèques Python non installées (réseau ?) : relance l'installation"
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
(cd "$BOT" && as_u timeout 180 "$BOT/.venv/bin/python" main.py --check) || warn "contrôle incomplet : corrige les erreurs ci-dessus puis relance ce script"

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
