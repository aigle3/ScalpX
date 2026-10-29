"""Configuration : config.toml (pydantic) + .env (token et chat_id autorisés)."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field


def default_common_files() -> Path:
    """%APPDATA%\\MetaQuotes\\Terminal\\Common\\Files"""
    appdata = os.environ.get("APPDATA", "")
    return Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files"


class Paths(BaseModel):
    base: Path = Path(r"C:\MT5\BASE")
    instances: Path = Path(r"C:\MT5\instances")
    archives: Path = Path(r"C:\MT5\archives")
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
    hide_panels: bool = True               # boîte à outils, navigateur et observation du marché masqués
    maximize_chart: bool = True            # graphique agrandi dans la fenêtre MT5
    default_server: str = ""
    servers: list[str] = Field(default_factory=list)


class Timing(BaseModel):
    heartbeat_stale_s: int = 30            # au-delà : heartbeat considéré ancien (🟠)
    create_timeout_s: int = 120
    stop_wait_s: int = 30                  # attente après FERMER_TERMINAL avant kill
    launch_stagger_s: int = 5              # écart entre deux lancements au démarrage
    ack_timeout_s: int = 20
    close500_wait_max_s: int = 300
    maintenance_wait_s: int = 600          # attente 0 position avant « en attente »
    deploy_retry_min: int = 15
    restarts_per_hour: int = 3
    muet_restart_s: int = 300              # terminal lancé mais EA muet (aucun heartbeat) depuis ce délai : relance forcée (0 = jamais)
    watchdog_interval_s: int = 15
    events_interval_s: int = 3
    status_interval_s: int = 5
    verify_deploy_s: int = 120


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


class Runtime(BaseModel):
    lock_port: int = 47653                 # verrou d'instance unique (127.0.0.1)
    log_level: str = "INFO"


class Update(BaseModel):
    github: str = ""                       # dépôt « propriétaire/nom » (ex. aigle3/scalpx) ; jeton GITHUB_TOKEN dans .env
    branch: str = "main"
    settle_min: int = 2                    # un nouveau commit n'est proposé qu'après N minutes sans autre publication
    validated_only: bool = True            # ne télécharger que la branche « stable » (avancée par GitHub quand les tests passent)
    source: str = r"C:\ScalpX\release"    # copie locale du paquet (téléchargée depuis GitHub)
    check_s: int = 60                      # vérification toutes les N secondes
    enabled: bool = True


class Backup(BaseModel):
    dir: str = ""                          # vide → %OneDrive%\\ScalpX\\sauvegardes si OneDrive est présent, sinon data\\backups
    hour: int = 3                          # sauvegarde quotidienne de l'historique (scalpx.db)
    keep: int = 14                         # nombre de sauvegardes conservées


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
    if not cfg.mt5.servers and cfg.mt5.default_server:
        cfg.mt5.servers = [cfg.mt5.default_server]
    return cfg
