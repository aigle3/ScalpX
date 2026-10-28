"""Point d'entrée du bot central ScalpX.

  python.exe main.py            → debug (console)
  pythonw.exe main.py           → production (Planificateur de tâches, sans fenêtre)
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import re
import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.commands import CommandQueue            # noqa: E402
from core.config import Config, load_config        # noqa: E402
from core.status import StatusReader               # noqa: E402
from mt5.creator import InstanceCreator            # noqa: E402
from mt5.deploy import DeployManager               # noqa: E402
from mt5.manager import InstanceManager            # noqa: E402
from mt5.procs import PsutilProcesses              # noqa: E402
from mt5.watchdog import Watchdog                  # noqa: E402
from storage.db import Database                    # noqa: E402


class SecretFilter(logging.Filter):
    """Masque le token Telegram et tout « Password=… » dans les logs."""

    def __init__(self, token: str) -> None:
        super().__init__()
        self.token = token
        self.pw = re.compile(r"(?i)(password\s*[=:]\s*)\S+")

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if self.token:
            msg = msg.replace(self.token, "<token>")
        msg = self.pw.sub(r"\1***", msg)
        record.msg, record.args = msg, ()
        return True


def setup_logging(cfg: Config, console: bool) -> None:
    logdir = cfg.paths.data / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(getattr(logging, cfg.runtime.log_level.upper(), logging.INFO))
    handlers: list[logging.Handler] = [logging.handlers.RotatingFileHandler(logdir / "bot.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")]
    if console:
        handlers.append(logging.StreamHandler())
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(SecretFilter(cfg.token))
        root.addHandler(h)
    for noisy in ("httpx", "httpcore", "telegram", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)       # httpx journalise l'URL (qui contient le token)


def acquire_lock(port: int, takeover: bool = True, wait_s: float = 40.0) -> socket.socket | None:
    """Verrou d'instance unique : un port local réservé.
    Si un ancien bot le tient, on lui demande de s'arrêter (takeover) puis on attend qu'il libère le port :
    relancer le bot remplace donc toujours l'ancien, même lancé en arrière-plan (pythonw / tâche planifiée)."""
    deadline = time.monotonic() + (wait_s if takeover else 0)
    asked = False
    while True:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name != "nt":                      # Linux : n'autorise PAS deux écoutes, seulement la reprise après TIME_WAIT
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            s.listen(1)
            return s
        except OSError:
            s.close()
        if not takeover or time.monotonic() > deadline:
            return None
        if not asked:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=3) as c:
                    c.sendall(b"QUIT\n")
                asked = True
                print("Un ancien bot tourne : demande d'arrêt envoyée, attente…")
            except OSError:
                pass
        time.sleep(0.5)


class QuitListener:
    """Écoute le port du verrou : un nouveau bot qui démarre envoie QUIT → arrêt propre de celui-ci."""

    def __init__(self, lock: socket.socket) -> None:
        self.lock = lock
        self.requested = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.lock.accept()
            except OSError:
                return
            data = b""
            try:
                conn.settimeout(3)
                data = conn.recv(16)
                if data:
                    conn.recv(16)                # attendre que le nouveau bot ferme en premier : le port reste réutilisable
            except OSError:
                pass
            finally:
                conn.close()
            if data.startswith(b"QUIT"):
                logging.getLogger("main").warning("Arrêt demandé par un nouveau bot qui démarre.")
                self.requested.set()
                threading.Timer(25, lambda: os._exit(0)).start()       # filet : arrêt forcé si l'arrêt propre bloque


def make_ctx(cfg: Config) -> "Ctx":
    from bot.actions import Ctx
    db = Database(cfg.paths.data / "scalpx.db")
    common = cfg.paths.common()
    common.mkdir(parents=True, exist_ok=True)
    status = StatusReader(common)
    cmdq = CommandQueue(common)
    mgr = InstanceManager(cfg, db, PsutilProcesses(), status, cmdq)
    creator = InstanceCreator(cfg, db, mgr, status)
    deploy = DeployManager(cfg, db, mgr, status, cmdq)
    watchdog = Watchdog(cfg, db, mgr, status, _noop_alert)          # remplacé par le routeur au démarrage
    return Ctx(cfg, db, status, cmdq, mgr, creator, deploy, watchdog)


async def _noop_alert(iid: str, kind: str, text: str, prio: int) -> None:
    return None


def check_install(cfg: Config) -> int:
    """python main.py --check : contrôle BASE, dossiers, secrets, serveurs. Code retour 0 si tout est prêt."""
    from mt5 import base as basemod
    ok = True

    def line(good: bool, text: str) -> None:
        nonlocal ok
        ok = ok and good
        print(("[OK]   " if good else "[ERREUR] ") + text)

    probs = basemod.check_base(cfg)
    line(not probs, "BASE saine (" + str(cfg.paths.base) + ")" if not probs else "BASE : " + " ; ".join(probs))
    ver = basemod.read_version(cfg.paths.base)
    line(bool(ver), f"version.txt de BASE = {ver or 'ABSENT'}")
    line(cfg.paths.common().exists(), f"dossier Common\\Files : {cfg.paths.common()}")
    line(bool(cfg.token), "TELEGRAM_TOKEN présent dans .env")
    line(bool(cfg.allowed_chats), f"ALLOWED_CHAT_IDS : {cfg.allowed_chats}")
    print(f"[INFO] serveurs pré-enregistrés : {cfg.mt5.servers or 'aucun (saisis au premier compte, puis mémorisés)'}")
    try:
        cfg.paths.instances.mkdir(parents=True, exist_ok=True)
        cfg.paths.archives.mkdir(parents=True, exist_ok=True)
        line(True, f"dossiers instances/archives accessibles en écriture ({basemod.free_disk_gb(cfg.paths.instances):.1f} Go libres)")
    except OSError as exc:
        line(False, f"création des dossiers impossible : {exc}")
    print("\nPrêt." if ok else "\nCorrige les erreurs ci-dessus.")
    return 0 if ok else 1


def main() -> int:
    console = "--console" in sys.argv or sys.stdout is not None and sys.stdout.isatty()
    cfg_path = ROOT / "config.toml"
    for a in sys.argv[1:]:
        if a.startswith("--config="):
            cfg_path = Path(a.split("=", 1)[1])
    cfg = load_config(cfg_path)
    cfg.paths.data = (cfg.paths.data if cfg.paths.data.is_absolute() else ROOT / cfg.paths.data)
    if "--check" in sys.argv:                                  # vérification de l'installation (sans démarrer le bot)
        return check_install(cfg)
    setup_logging(cfg, console)
    log = logging.getLogger("main")
    if not cfg.token or not cfg.allowed_chats:
        log.critical("TELEGRAM_TOKEN ou ALLOWED_CHAT_IDS manquant dans .env")
        return 2
    lock = acquire_lock(cfg.runtime.lock_port)
    if lock is None:
        log.critical("Un autre bot tourne déjà (verrou %s). Arrêt.", cfg.runtime.lock_port)
        return 3
    from bot.app import build_application
    app = build_application(cfg, lambda: make_ctx(cfg))
    quit_l = QuitListener(lock)

    async def j_quit(context) -> None:
        from core.update import STOP
        if quit_l.requested.is_set() or STOP.is_set():
            context.application.stop_running()

    app.job_queue.run_repeating(j_quit, interval=1, first=1)
    log.info("ScalpX central démarré (%d chat autorisé)", len(cfg.allowed_chats))
    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=False)
    lock.close()
    return 0


def run() -> int:
    """main() avec trace écrite dans data\\logs\\crash.log : sous pythonw (tâche planifiée) il n'y a pas de console."""
    try:
        return main()
    except SystemExit:
        raise
    except BaseException:
        import traceback
        txt = traceback.format_exc()
        try:
            d = ROOT / "data" / "logs"
            d.mkdir(parents=True, exist_ok=True)
            with open(d / "crash.log", "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + txt + "\n")
        except OSError:
            pass
        logging.getLogger("main").critical("arrêt sur erreur :\n%s", txt)
        print(txt, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(run())
