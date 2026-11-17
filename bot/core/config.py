"""Configuration : config.toml (pydantic) + .env (token et chat_id autorisés)."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field


IS_WINDOWS = os.name == "nt"
_MT5_ROOT = Path(r"C:\MT5") if IS_WINDOWS else Path.home() / "mt5"      # Bot 3.13.0 : Linux → ~/mt5


def default_common_files() -> Path:
    """%APPDATA%\\MetaQuotes\\Terminal\\Common\\Files (Linux : le même dossier dans le préfixe Wine)."""
    if not IS_WINDOWS:
        from mt5.wine import WineOptions, common_files
        return common_files(WineOptions())
    appdata = os.environ.get("APPDATA", "")
    return Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files"


class Paths(BaseModel):
    base: Path = _MT5_ROOT / "BASE"
    instances: Path = _MT5_ROOT / "instances"
    archives: Path = _MT5_ROOT / "archives"
    common_files: Path | None = None       # None → %APPDATA%\MetaQuotes\Terminal\Common\Files
    data: Path = Path("data")              # base SQLite, logs, graphiques, verrou

    def common(self) -> Path:
        return self.common_files or default_common_files()


class MT5(BaseModel):
    terminal_exe: str = "terminal64.exe"
    ea_name: str = "ScalpX_v5"             # EA dans MQL5\Experts (sans extension)
    ea_file: str = "ScalpX_v5.ex5"
    set_name: str = "scalpx_v5.set"        # dans MQL5\Presets
    symbol: str = "XAUUSD"
    period: str = "M5"
    profile: str = "Default"               # profil de graphiques MT5 que le bot VIDE dans chaque terminal avant le lancement (évite EURUSD, GBPUSD…)
    minimize: bool = True                  # fenêtre MT5 réduite après chaque ouverture (moins de CPU/RAM)
    minimize_check_s: int = 300            # contrôle périodique : fenêtres ouvertes de nouveau réduites (0 = jamais)
    max_bars: int = 5000                   # Bot 3.10.0 : barres max par graphique (moins de calcul et de RAM ; 0 = réglage MT5 inchangé)
    hide_panels: bool = True               # boîte à outils, navigateur et observation du marché masqués
    maximize_chart: bool = True            # graphique agrandi dans la fenêtre MT5
    default_server: str = ""
    servers: list[str] = Field(default_factory=list)


class Timing(BaseModel):
    heartbeat_stale_s: int = 30            # au-delà : heartbeat considéré ancien (🟠)
    create_timeout_s: int = 300           # Bot 3.6.2 : 5 min (VPS chargé : le terminal met plus de 2 min à charger l'EA)
    refused_s: int = 40                    # création : compte vu « non connecté » pendant ce délai → identifiants refusés
    stop_wait_s: int = 30                  # attente après FERMER_TERMINAL avant kill
    kill_after_s: int = 10                 # Bot 3.12.0 : arrêt forcé au bout de ce délai si le terminal n'est pas fermé (remplace stop_wait_s ; 0 = stop_wait_s)
    launch_group: int = 2                  # Bot 3.12.0 : terminaux lancés ensemble (lancer / redémarrer tous, mises à jour), puis attente du vert
    launch_stagger_s: int = 5              # écart entre deux lancements au démarrage
    ack_timeout_s: int = 20
    close500_wait_max_s: int = 300
    maintenance_wait_s: int = 600          # attente 0 position avant « en attente »
    deploy_wait_flat: bool = False         # mise à jour de l'EA : attendre la fin de la série (0 position) ? Non par défaut :
                                           # le terminal est relancé tout de suite, positions et ordres restent chez le broker
    deploy_retry_min: int = 15
    restarts_per_hour: int = 3
    muet_restart_s: int = 300              # terminal lancé mais EA muet (aucun heartbeat) depuis ce délai : relance forcée (0 = jamais)
    watchdog_interval_s: int = 15
    events_interval_s: int = 3
    status_interval_s: int = 5
    presets_check_s: int = 600             # Bot 3.10.0 : contrôle « robots conformes au preset ⭐ » (10 min, avant 5)
    verify_deploy_s: int = 120
    wait_online_limit_s: int = 0           # Bot 3.11.0 : attente d'un terminal relancé (mise à jour de l'EA, lancer / redémarrer tous) :
                                           # 0 = sans limite, le temps qu'il faut (abandon seulement si le terminal se ferme)


class Alerts(BaseModel):
    min_disk_gb: float = 2.0
    expected_currency: str = "EUR"
    silent_mode: bool = False              # silencieux : seules les alertes critiques sonnent
    batch_window_s: int = 4                # regroupement des notifications
    group_hold_s: int = 10                 # attente des messages identiques des autres comptes (une seule alerte pour tous)


class Dashboard(BaseModel):
    interval_s: int = 12                   # (ancien réglage, remplacé par refresh_s)
    refresh_s: int = 10                    # mise à jour du tableau de bord quand il est le dernier message
    repost_s: int = 300                    # recouvert par d'autres messages depuis 5 min : republié en bas (silencieux)
    page_size: int = 8


class Reports(BaseModel):
    daily_hour: int = 22
    timezone: str = "Europe/Paris"         # fuseau du rapport quotidien
    history_days: int = 30
    withdraw_reminder_hour: int = 10       # rappel quotidien des comptes à 500 qui attendent un retrait (-1 = jamais)


class Runtime(BaseModel):
    lock_port: int = 47653                 # verrou d'instance unique (127.0.0.1)
    log_level: str = "INFO"


class Update(BaseModel):
    github: str = ""                       # dépôt « propriétaire/nom » (ex. aigle3/scalpx) ; jeton GITHUB_TOKEN dans .env
    branch: str = "main"
    settle_min: int = 2                    # un nouveau commit n'est proposé qu'après N minutes sans autre publication
    validated_only: bool = True            # ne télécharger que la branche « stable » (avancée par GitHub quand les tests passent)
    source: str = r"C:\ScalpX\release" if IS_WINDOWS else str(Path.home() / "scalpx" / "release")   # copie locale du paquet (GitHub)
    check_s: int = 60                      # vérification toutes les N secondes
    enabled: bool = True
    cpu_free_pct: int = 30                 # Bot 3.8.2 : 30 % (70 avant) ; Bot 3.7.2 : avant de redémarrer le bot, fermer des terminaux un par un jusqu'à
    cpu_wait_s: int = 30                   # ce % de CPU libre (moyenne sur cpu_wait_s après chaque fermeture) ; 0 = jamais
    close_terminals: bool = True           # Bot 3.11.0 : mise à jour du bot = tous les terminaux arrêtés un par un, puis relancés un par un


class Backup(BaseModel):
    dir: str = ""                          # vide → %OneDrive%\\ScalpX\\sauvegardes si OneDrive est présent, sinon data\\backups
    hour: int = 3                          # sauvegarde quotidienne de l'historique (scalpx.db)
    keep: int = 14                         # nombre de sauvegardes conservées


class Backtest(BaseModel):
    folder: Path | None = None             # None → <dossier parent des instances>\backtest
    leverage: int = 500                    # effet de levier si le compte de référence ne le donne pas (EA < 7.2.0)
    gmt_winter: int = -99                  # décalage GMT du serveur en hiver (h) ; -99 = lu dans le calendrier exporté par l'EA
    us_dst: bool = True                    # le serveur du broker suit l'heure d'été américaine
    stall_min: int = 30                    # aucun progrès (journal MT5 ni avancement) pendant N min : test arrêté
    setup_stall_min: int = 5               # configuration du compte : abandon plus rapide
    poll_s: float = 4.0                    # lecture de l'avancement du testeur (le message Telegram est édité au plus toutes les 5 s)
    low_priority: bool = True              # testeur en priorité basse : les terminaux réels passent toujours avant
    free_cores: int = 1                    # Bot 3.6.2 : cœurs laissés au bot et aux comptes (le testeur ne les utilise pas)


class Plex(BaseModel):
    exe: str = ""                          # Bot 3.8.0 : chemin de « Plex Media Server.exe » (vide : emplacement habituel)
    low_priority: bool = True              # serveur lancé en priorité basse (les terminaux MT5 passent avant)


class Wine(BaseModel):
    """Bot 3.13.0 : Linux seulement (ignoré sous Windows). MT5, MetaEditor et le testeur passent par Wine."""
    cmd: str = "wine"                      # commande Wine
    prefix: Path | None = None             # préfixe Wine (None → $WINEPREFIX, sinon ~/.wine)
    display: str = ":99"                   # écran virtuel Xvfb (service scalpx-xvfb)
    debug: str = "-all"                    # WINEDEBUG

    def options(self):
        from mt5.wine import WineOptions
        return WineOptions(cmd=self.cmd, prefix=self.prefix, display=self.display, debug=self.debug)


class Config(BaseModel):
    paths: Paths = Paths()
    mt5: MT5 = MT5()
    timing: Timing = Timing()
    alerts: Alerts = Alerts()
    dashboard: Dashboard = Dashboard()
    reports: Reports = Reports()
    runtime: Runtime = Runtime()
    update: Update = Update()
    backup: Backup = Backup()
    backtest: Backtest = Backtest()
    plex: Plex = Plex()
    wine: Wine = Wine()

    # secrets (depuis .env, jamais depuis config.toml)
    token: str = ""
    github_token: str = ""
    healthcheck_url: str = ""              # HEALTHCHECK_URL (.env) : surveillance externe, ex. https://hc-ping.com/<uuid>
    allowed_chats: list[int] = Field(default_factory=list)


def parse_env(text: str) -> dict[str, str]:
    """Parse minimal d'un fichier .env (KEY=VALUE, # commentaires, guillemets facultatifs)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def load_config(config_path: Path, env_path: Path | None = None) -> Config:
    """Charge config.toml puis .env (token, chat_id autorisés)."""
    raw = tomllib.loads(Path(config_path).read_text(encoding="utf-8-sig"))
    cfg = Config.model_validate(raw)
    env: dict[str, str] = {}
    env_path = env_path or Path(config_path).with_name(".env")
    if env_path.exists():
        env = parse_env(env_path.read_text(encoding="utf-8-sig"))
    cfg.token = env.get("TELEGRAM_TOKEN", os.environ.get("TELEGRAM_TOKEN", "")).strip()
    cfg.github_token = env.get("GITHUB_TOKEN", os.environ.get("GITHUB_TOKEN", "")).strip()
    cfg.healthcheck_url = env.get("HEALTHCHECK_URL", os.environ.get("HEALTHCHECK_URL", "")).strip()
    chats = env.get("ALLOWED_CHAT_IDS", env.get("CHAT_ID", os.environ.get("ALLOWED_CHAT_IDS", "")))
    cfg.allowed_chats = [int(x) for x in chats.replace(";", ",").split(",") if x.strip().lstrip("-").isdigit()]
    if not IS_WINDOWS and cfg.paths.common_files is None:      # Linux : Common\Files dans le préfixe Wine configuré
        from mt5.wine import common_files
        cfg.paths.common_files = common_files(cfg.wine.options())
    if not cfg.mt5.servers and cfg.mt5.default_server:
        cfg.mt5.servers = [cfg.mt5.default_server]
    return cfg
