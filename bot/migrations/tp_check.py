"""Vérification complète de l'installation TrendPullback (migration TEMPORAIRE, retirée avec trendpullback.py).

LECTURE SEULE : rien n'est modifié, ni dans C:\\TrendPullback ni ailleurs. Aucun secret n'est affiché : les valeurs du
.env de TrendPullback ne servent qu'à les masquer dans les journaux et à vérifier leur format.

Contrôles : dossiers, versions (paquet / installé), MT5 BASE, bot (fichiers, environnement Python, config.toml, .env),
processus (bot, verrou, terminaux), tâches planifiées, journaux, liaison EA ↔ bot, et `main.py --check` du bot.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .trendpullback import CHAT_RE, GH_RE, REPO, TG_RE, mask

OK, WARN, ERR, INFO = "ok", "warn", "err", "info"
ICON = {OK: "✅", WARN: "⚠️", ERR: "❌", INFO: "ℹ️"}
TASKS = ("TrendPullback Bot", "TrendPullback Watchdog")
SCALPX_DIRS = re.compile(r"(?i)c:\\{1,2}(scalpx|mt5)(\\|\"|$)")

Run = Callable[[list, Path | None, int], tuple[int, str]]      # (commande, dossier, délai) -> (code, sortie)


@dataclass
class Section:
    title: str
    items: list[tuple[str, str]] = field(default_factory=list)
    details: list[str] = field(default_factory=list)          # rapport complet seulement (extraits de journaux)

    def add(self, level: str, text: str) -> None:
        self.items.append((level, text))


def run_cmd(cmd: list, cwd: Path | None, timeout: int) -> tuple[int, str]:
    try:
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")      # accents lisibles (sortie Windows)
        r = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, timeout=timeout, env=env,
                           stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = (r.stdout or b"") + (r.stderr or b"")
        return r.returncode, out.decode("utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, f"{type(exc).__name__}: {exc}"


def list_procs() -> list[dict]:
    import psutil
    out = []
    for p in psutil.process_iter(["pid", "name", "exe", "cmdline", "create_time", "memory_info"]):
        i = p.info
        out.append({"pid": i.get("pid"), "name": (i.get("name") or "").lower(), "exe": i.get("exe") or "",
                    "cmdline": " ".join(i.get("cmdline") or []), "started": i.get("create_time") or 0,
                    "rss": getattr(i.get("memory_info"), "rss", 0) or 0})
    return out


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5):
            return True
    except OSError:
        return False


def read_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    except OSError:
        pass
    return env


def _age(sec: float) -> str:
    sec = max(0, int(sec))
    if sec < 120:
        return f"{sec} s"
    if sec < 7200:
        return f"{sec // 60} min"
    if sec < 172800:
        return f"{sec // 3600} h"
    return f"{sec // 86400} j"


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8-sig", errors="replace").strip()
    except OSError:
        return ""


def _tail(p: Path, n: int = 400) -> list[str]:
    try:
        with open(p, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 200_000))
            return f.read().decode("utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []


MT5_BAD = re.compile(r"(?i)\berror\b|failed|cannot|invalid|removed|critical|not found|out of range|zero divide|"
                     r"no connection|disabled|refused|erreur|init_|unable")
MT5_GOOD = re.compile(r"(?i)loaded successfully|authorized on|initialized|démarré|started|connected")


def _latest_log(folder: Path) -> Path | None:
    try:
        logs = [f for f in folder.glob("*.log") if f.is_file() and f.name[:8].isdigit()]
    except OSError:
        return None
    return max(logs, key=lambda f: f.stat().st_mtime) if logs else None


def _mt5_lines(f: Path) -> list[str]:
    """Journal MT5 (UTF-16 LE en général), lu même verrouillé par le terminal."""
    try:
        raw = f.read_bytes()[-400_000:]
    except OSError:
        return []
    if raw[:2] == b"\xff\xfe" or (len(raw) > 3 and raw[1:2] == b"\x00"):
        txt = raw.decode("utf-16-le", errors="replace")
    else:
        txt = raw.decode("utf-8", errors="replace")
    return [l.strip().lstrip("\ufeff") for l in txt.splitlines() if l.strip()]


def _strip_mt5(line: str) -> str:
    """« XX 0 12:34:56.789 Source texte » -> « 12:34:56 Source texte »."""
    m = re.match(r"^\w{2}\s+\d\s+(\d\d:\d\d:\d\d)\.\d+\s+(.*)$", line)
    return f"{m.group(1)} {m.group(2)}" if m else line


class TPCheck:
    def __init__(self, root: Path, scalpx_token: str = "", scalpx_port: int = 0, scalpx_logs: Path | None = None,
                 run: Run | None = None, procs: Callable[[], list[dict]] | None = None,
                 port_check: Callable[[int], bool] | None = None, appdata: str | None = None,
                 now: float | None = None) -> None:
        self.root = Path(root)
        self.scalpx_token, self.scalpx_port, self.scalpx_logs = scalpx_token, scalpx_port, scalpx_logs
        self.run = run or run_cmd
        self.procs = procs or list_procs
        self.port_check = port_check or port_open
        self.appdata = os.environ.get("APPDATA", "") if appdata is None else appdata
        self.now = now or time.time()
        self.bot = self.root / "bot"
        self.env = read_env(self.bot / ".env")
        self.secrets = [self.env.get(k, "") for k in ("TELEGRAM_TOKEN", "GITHUB_TOKEN", "HEALTHCHECK_URL")]
        self.cfg: dict = {}
        self.sections: list[Section] = []

    # ------------------------------------------------------------------ utilitaires
    def _m(self, s: str) -> str:
        return mask(s, self.secrets)

    def _path(self, key: str, default: Path) -> Path:
        v = (self.cfg.get("paths") or {}).get(key)
        return Path(v) if v else default

    @property
    def base(self) -> Path:
        return self._path("base", self.root / "MT5" / "BASE")

    @property
    def data(self) -> Path:
        d = self._path("data", Path("data"))
        return d if d.is_absolute() else self.bot / d

    @property
    def common(self) -> Path:
        v = (self.cfg.get("paths") or {}).get("common_files")
        return Path(v) if v else Path(self.appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files" / "TrendPullback"

    @property
    def lock_port(self) -> int:
        return int((self.cfg.get("runtime") or {}).get("lock_port", 47663))

    # ------------------------------------------------------------------ contrôles
    def scan(self) -> list[Section]:
        try:
            self.cfg = tomllib.loads(_read(self.bot / "config.toml") or "")
        except tomllib.TOMLDecodeError as exc:
            self.cfg = {"_error": str(exc)}
        for f in (self.c_dirs, self.c_versions, self.c_base, self.c_bot, self.c_procs, self.c_tasks, self.c_logs,
                  self.c_link, self.c_mt5logs, self.c_selfcheck):
            try:
                f()
            except Exception as exc:                                  # un contrôle en erreur n'empêche pas les autres
                s = Section(f.__name__)
                s.add(ERR, self._m(f"contrôle impossible : {type(exc).__name__}: {exc}"))
                self.sections.append(s)
        return self.sections

    def c_dirs(self) -> None:
        s = Section("📁 Dossiers")
        for p, need in ((self.root, True), (self.bot, True), (self.root / "release", True), (self.base, True),
                        (self._path("instances", self.root / "MT5" / "instances"), False),
                        (self._path("archives", self.root / "MT5" / "archives"), False), (self.common, False)):
            if p.is_dir():
                s.add(OK, str(p))
            else:
                s.add(ERR if need else WARN, f"{p} absent" + ("" if need else " (créé au premier compte)"))
        self.sections.append(s)

    def c_versions(self) -> None:
        s = Section("📦 Versions")
        rel = self.root / "release"
        try:
            ci = json.loads(_read(rel / ".commit.json") or "{}")
        except ValueError:
            ci = {}
        msg = (ci.get("message") or "").splitlines()[0][:90] if ci.get("message") else ""
        if ci.get("sha"):
            age = f", téléchargé il y a {_age(self.now - ci['fetched'])}" if ci.get("fetched") else ""
            s.add(OK, f"paquet GitHub {ci['sha'][:7]} « {msg} »{age}")
        else:
            s.add(WARN, "paquet sans .commit.json (téléchargement GitHub inconnu)")
        for what, pkg, inst in (("Bot", rel / "bot" / "version.txt", self.bot / "version.txt"),
                                ("EA", rel / "ea" / "version.txt", self.base / "version.txt")):
            a, b = _read(pkg), _read(inst)
            if not b:
                s.add(ERR, f"{what} : version installée introuvable ({inst})")
            elif a and a != b:
                s.add(WARN, f"{what} : installé {b}, paquet {a} (mise à jour à installer depuis le bot TrendPullback)")
            else:
                s.add(OK, f"{what} {b}" + (" (= paquet)" if a else ""))
        self.sections.append(s)

    def c_base(self) -> None:
        s = Section("🖥️ MT5 BASE")
        b = self.base
        mt5 = self.cfg.get("mt5") or {}
        ea = mt5.get("ea_file", "TrendPullback.ex5")
        setn = mt5.get("set_name", "trendpullback.set")
        for rel, level in (("terminal64.exe", ERR), ("MetaEditor64.exe", ERR), (f"MQL5\\Experts\\{ea}", ERR),
                           ("MQL5\\Experts\\TrendPullback.mq5", WARN), (f"MQL5\\Presets\\{setn}", ERR)):
            p = b.joinpath(*rel.split("\\"))
            if p.is_file():
                extra = f" (compilé il y a {_age(self.now - p.stat().st_mtime)})" if rel.endswith(".ex5") else ""
                s.add(OK, rel + extra)
            else:
                s.add(level, f"{rel} absent")
        if (b / "Config" / "accounts.dat").exists():
            s.add(WARN, "BASE contient un compte mémorisé (Config\\accounts.dat) : BASE doit rester sans compte")
        else:
            s.add(OK, "aucun compte mémorisé dans BASE")
        bad = 0
        charts = b / "Profiles" / "Charts"
        if charts.is_dir():
            for c in charts.rglob("*.chr"):
                try:
                    raw = c.read_bytes()
                    txt = raw.decode("utf-16", errors="ignore") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "ignore")
                    bad += "<expert>" in txt.lower()
                except OSError:
                    pass
        s.add(WARN if bad else OK, f"{bad} graphique(s) de BASE avec un EA attaché" if bad else "aucun EA attaché dans BASE")
        self.sections.append(s)

    def c_bot(self) -> None:
        s = Section("🤖 Bot TrendPullback")
        for rel in ("main.py", "requirements.txt", "watchdog.ps1"):
            s.add(OK if (self.bot / rel).is_file() else ERR, rel + ("" if (self.bot / rel).is_file() else " absent"))
        venv = self.bot / ".venv" / "Scripts"
        py = venv / "python.exe"
        if py.is_file() and (venv / "pythonw.exe").is_file():
            code, out = self.run([str(py), "--version"], None, 20)
            s.add(OK if code == 0 else WARN, f"environnement Python : {out.strip() or code}")
        else:
            s.add(ERR, "environnement Python (.venv) absent")
        if "_error" in self.cfg:
            s.add(ERR, f"config.toml illisible : {self.cfg['_error']}")
        elif not self.cfg:
            s.add(ERR, "config.toml absent")
        else:
            up = self.cfg.get("update") or {}
            s.add(OK if up.get("github") == REPO else ERR, f"mises à jour depuis {up.get('github') or '—'}")
            s.add(OK if up.get("validated_only", True) else WARN,
                  "branche « stable » seulement (testée)" if up.get("validated_only", True) else "validated_only = false")
            raw = _read(self.bot / "config.toml")
            lines = [l for l in raw.splitlines() if not l.lstrip().startswith("#") and SCALPX_DIRS.search(l.split("#")[0])]
            s.add(ERR if lines else OK, "config.toml pointe vers un dossier de ScalpX : " + "; ".join(lines)[:200]
                  if lines else "aucun chemin vers C:\\ScalpX ou C:\\MT5")
            if self.scalpx_port and self.lock_port == self.scalpx_port:
                s.add(ERR, f"verrou {self.lock_port} identique à celui de ScalpX")
            sym = (self.cfg.get("mt5") or {}).get("symbol", "EURUSD")
            s.add(INFO, f"graphique de l'EA : {sym} {(self.cfg.get('mt5') or {}).get('period', 'H4')}")
            srv = (self.cfg.get("mt5") or {}).get("servers") or []
            s.add(INFO, f"serveurs pré-enregistrés : {', '.join(srv) if srv else 'aucun'}")
        e = self.env
        if not (self.bot / ".env").is_file():
            s.add(ERR, ".env absent")
        else:
            tok = e.get("TELEGRAM_TOKEN", "")
            if not tok:
                s.add(ERR, ".env : TELEGRAM_TOKEN manquant")
            elif self.scalpx_token and tok == self.scalpx_token:
                s.add(ERR, ".env : TELEGRAM_TOKEN IDENTIQUE à celui de ScalpX (les deux bots se bloqueraient)")
            else:
                s.add(OK if TG_RE.match(tok) else WARN, ".env : TELEGRAM_TOKEN présent, différent de ScalpX"
                      + ("" if TG_RE.match(tok) else " (format inattendu)"))
            chat = e.get("ALLOWED_CHAT_IDS", "").replace(" ", "")
            s.add(OK if CHAT_RE.match(chat) else ERR, f".env : ALLOWED_CHAT_IDS {chat or 'manquant'}")
            gh = e.get("GITHUB_TOKEN", "")
            s.add(OK if GH_RE.match(gh) else (WARN if gh else ERR),
                  ".env : GITHUB_TOKEN présent" if GH_RE.match(gh) else
                  (".env : GITHUB_TOKEN au format inattendu" if gh else ".env : GITHUB_TOKEN manquant (pas de mises à jour)"))
            hc = e.get("HEALTHCHECK_URL", "")
            s.add(OK if hc.startswith("https://") else INFO,
                  ".env : surveillance externe configurée" if hc.startswith("https://") else ".env : surveillance externe non configurée")
        dbs = list(self.data.glob("*.db")) if self.data.is_dir() else []
        s.add(OK if dbs else WARN, f"base de données : {', '.join(d.name for d in dbs)}" if dbs
              else "base de données absente (créée au premier démarrage du bot)")
        self.sections.append(s)

    def c_procs(self) -> None:
        s = Section("⚙️ Processus")
        botdir = str(self.bot).lower()
        procs = self.procs()
        bots = [p for p in procs if p["name"].startswith("python") and "main.py" in p["cmdline"].lower()
                and (botdir in p["exe"].lower() or botdir in p["cmdline"].lower())]
        if not bots:
            s.add(ERR, "bot TrendPullback NON lancé")
        else:
            for p in bots:
                s.add(OK, f"bot lancé (pid {p['pid']}, depuis {_age(self.now - p['started'])}, {p['rss'] / 1e6:.0f} Mo)")
            if len(bots) > 1:
                s.add(WARN, f"{len(bots)} processus du bot (un seul attendu, le verrou arrête les autres)")
        s.add(OK if self.port_check(self.lock_port) else ERR,
              f"verrou 127.0.0.1:{self.lock_port} " + ("ouvert (bot actif)" if self.port_check(self.lock_port) else "fermé"))
        base = str(self.base).lower()
        terms = [p for p in procs if p["name"] == "terminal64.exe" and str(self.root).lower() in p["exe"].lower()]
        in_base = [p for p in terms if p["exe"].lower().startswith(base)]
        if in_base:
            s.add(WARN, f"{len(in_base)} terminal(aux) lancé(s) depuis BASE (BASE ne doit pas tourner)")
        n = len(terms) - len(in_base)
        s.add(INFO, f"{n} terminal(aux) MT5 TrendPullback lancé(s)" + ("" if n else " (normal tant qu'aucun compte n'est ajouté)"))
        self.sections.append(s)

    def c_tasks(self) -> None:
        s = Section("🗓️ Tâches planifiées")
        ps = ("$r = @(); foreach ($n in @('" + "','".join(TASKS) + "')) { $t = Get-ScheduledTask -TaskName $n "
              "-ErrorAction SilentlyContinue; if ($t) { $i = $t | Get-ScheduledTaskInfo; $r += [pscustomobject]@{"
              "n=$n; s=[string]$t.State; last=$(if ($i.LastRunTime) { $i.LastRunTime.ToString('s') } else { '' }); "
              "res=[int64]$i.LastTaskResult; next=$(if ($i.NextRunTime) { $i.NextRunTime.ToString('s') } else { '' }); "
              "user=[string]$t.Principal.UserId; trig=(($t.Triggers | ForEach-Object { $_.CimClass.CimClassName }) -join ',')} } }; "
              "ConvertTo-Json -InputObject $r -Compress")
        code, out = self.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], None, 60)
        try:
            j = json.loads(out.strip() or "[]")
            rows = {r["n"]: r for r in (j if isinstance(j, list) else [j])}
        except ValueError:
            s.add(ERR, f"lecture des tâches impossible : {self._m(out.strip())[:200]}")
            self.sections.append(s)
            return
        for n in TASKS:
            r = rows.get(n)
            if r is None:
                s.add(ERR if n == TASKS[0] else WARN, f"« {n} » absente" + ("" if n == TASKS[0] else
                                                                           " (créée par le bot à son démarrage)"))
                continue
            st, res = r.get("s", ""), int(r.get("res") or 0)
            detail = f"état {st}, utilisateur {r.get('user', '?')}, déclencheur {r.get('trig') or '—'}"
            if r.get("last"):
                detail += f", dernière exécution {r['last'].replace('T', ' ')} (code {res:#x})"
            if n == TASKS[0]:
                lvl = OK if st == "Running" else (ERR if st == "Disabled" else WARN)
            else:
                lvl = OK if st in ("Ready", "Running") and res in (0, 0x41301, 0x41303) else (ERR if st == "Disabled" else WARN)
            s.add(lvl, f"« {n} » : {detail}")
        self.sections.append(s)

    def c_logs(self) -> None:
        s = Section("📜 Journaux")
        logs = self.data / "logs"
        bl = logs / "bot.log"
        if not bl.is_file():
            s.add(ERR, "bot.log absent (bot jamais démarré ?)")
        else:
            st = bl.stat()
            s.add(OK, f"bot.log : {st.st_size / 1e3:.0f} Ko, écrit il y a {_age(self.now - st.st_mtime)}")
            lines = _tail(bl)
            starts = [l for l in lines if "démarré" in l and "TrendPullback" in l]
            if starts:
                s.add(INFO, "dernier démarrage : " + self._m(starts[-1])[:160])
            errs = [l for l in lines if re.search(r"\b(ERROR|CRITICAL)\b|Traceback", l)]
            if errs:
                s.add(WARN, f"{len(errs)} erreur(s) dans les {len(lines)} dernières lignes ; dernière : " + self._m(errs[-1])[:220])
            else:
                s.add(OK, f"aucune erreur dans les {len(lines)} dernières lignes")
        crash = _read(logs / "crash.log")
        n = len(re.findall(r"(?m)^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$", crash))
        s.add(WARN if n else OK, f"{n} plantage(s) enregistré(s) (crash.log)" if n else "aucun plantage enregistré")
        for name in ("watchdog.log", "restart.log", "update.log"):
            t = _tail(logs / name, 1)
            if t:
                s.add(INFO, f"{name} : " + self._m(t[-1])[:160])
        if self.scalpx_logs:
            first = _read(self.scalpx_logs / "trendpullback_install.log").splitlines()[:1]
            if first:
                code = re.search(r"code (-?\d+)", first[0])
                s.add(OK if code and code.group(1) == "0" else WARN, f"installation depuis ScalpX : {first[0]}")
        self.sections.append(s)

    def c_link(self) -> None:
        s = Section("🔗 Liaison EA ↔ bot")
        c = self.common
        if not c.is_dir():
            s.add(WARN, f"{c} absent")
            self.sections.append(s)
            return
        st = sorted(c.glob("statut_*.json"))
        if not st:
            s.add(INFO, "aucun statut d'EA (normal tant qu'aucun compte n'est ajouté)")
        for p in st[:10]:
            try:
                j = json.loads(p.read_text(encoding="utf-8-sig", errors="replace"))
            except (OSError, ValueError):
                s.add(WARN, f"{p.name} illisible")
                continue
            age = self.now - float(j.get("ts") or p.stat().st_mtime)
            lvl = OK if age < 60 else (WARN if age < 600 else ERR)
            s.add(lvl, f"compte {j.get('login', '?')} ({j.get('serveur', '?')}) : EA {j.get('version', '?')}, "
                       f"{j.get('etat', '?')}, connecté {'oui' if j.get('connecte') else 'NON'}, "
                       f"AutoTrading {'oui' if j.get('algo_terminal') and j.get('algo_ea') else 'NON'}, "
                       f"statut il y a {_age(age)}")
        cmds = list(c.glob("cmd_*.json"))
        if cmds:
            old = [p for p in cmds if self.now - p.stat().st_mtime > 120]
            s.add(WARN if old else INFO, f"{len(cmds)} commande(s) en attente" + (f", dont {len(old)} de plus de 2 min" if old else ""))
        self.sections.append(s)

    def c_mt5logs(self) -> None:
        """Journaux MT5 de chaque compte (terminal + Experts) : pourquoi un EA ne démarre pas."""
        s = Section("🧾 Journaux MT5 des comptes")
        inst = self._path("instances", self.root / "MT5" / "instances")
        arch = self._path("archives", self.root / "MT5" / "archives")
        folders = sorted((d for d in inst.iterdir() if d.is_dir()), key=lambda d: d.name) if inst.is_dir() else []
        old = sorted((d for d in arch.iterdir() if d.is_dir()), key=lambda d: d.stat().st_mtime) if arch.is_dir() else []
        folders += old[-2:]                                   # dernières archives (création échouée, etc.)
        if not folders:
            s.add(INFO, "aucun dossier de compte (instances, archives)")
        for d in folders:
            where = ("archive " if d.parent == arch else "") + d.name
            ea_ok = any((d / "MQL5" / "Experts" / f).is_file() for f in ("TrendPullback.ex5",))
            if not ea_ok:
                s.add(WARN, f"{where} : TrendPullback.ex5 absent de MQL5\\Experts")
            if not (d / "terminal64.exe").is_file():
                s.add(WARN, f"{where} : terminal64.exe absent (copie incomplète ?)")
            for kind, sub in (("terminal", d / "logs"), ("Experts", d / "MQL5" / "Logs")):
                f = _latest_log(sub)
                if f is None:
                    s.add(INFO, f"{where} : aucun journal {kind}")
                    continue
                lines = _mt5_lines(f)
                s.details.append(f"--- {where} : journal {kind} {f.name} ({len(lines)} lignes, 80 dernières) ---")
                s.details += [self._m(l)[:300] for l in lines[-80:]]
                bad = [l for l in lines if MT5_BAD.search(l)]
                good = [l for l in lines if MT5_GOOD.search(l)]
                for l in bad[-3:]:
                    s.add(WARN, f"{where} [{kind}] " + self._m(_strip_mt5(l))[:200])
                if good:
                    s.add(INFO, f"{where} [{kind}] " + self._m(_strip_mt5(good[-1]))[:200])
                if not bad and not good:
                    s.add(INFO, f"{where} [{kind}] {len(lines)} ligne(s), rien de notable")
        procs = [p for p in self.procs() if p["name"] == "metaeditor64.exe" and str(self.root).lower() in p["exe"].lower()]
        for p in procs:
            s.add(WARN, f"MetaEditor lancé depuis {p['exe']} (pid {p['pid']}, depuis {_age(self.now - p['started'])})")
        self.sections.append(s)

    def c_selfcheck(self) -> None:
        s = Section("✔️ Vérification du bot (main.py --check)")
        py = self.bot / ".venv" / "Scripts" / "python.exe"
        if not py.is_file():
            s.add(ERR, "impossible : .venv absent")
            self.sections.append(s)
            return
        code, out = self.run([str(py), "main.py", "--check"], self.bot, 90)
        for line in out.splitlines():
            line = line.strip()
            if not line or line in ("Prêt.",):
                continue
            lvl = ERR if line.startswith("[ERREUR]") else (OK if line.startswith("[OK]") else INFO)
            s.add(lvl, self._m(re.sub(r"^\[(OK|ERREUR|INFO)\]\s*", "", line))[:220])
        if code != 0 and not any(l == ERR for l, _ in s.items):
            s.add(ERR, f"code de sortie {code}")
        self.sections.append(s)


# ------------------------------------------------------------------ présentation
def counts(sections: Iterable[Section]) -> dict[str, int]:
    c = {OK: 0, WARN: 0, ERR: 0, INFO: 0}
    for s in sections:
        for lvl, _ in s.items:
            c[lvl] += 1
    return c


def summary(sections: list[Section], limit: int = 3800) -> str:
    """Message Telegram : chaque section en une ligne si tout va bien, sinon ses avertissements et erreurs."""
    from bot.render import esc
    c = counts(sections)
    verdict = ("✅ <b>Tout est en ordre</b>" if not c[ERR] and not c[WARN] else
               "❌ <b>Problème(s) à corriger</b>" if c[ERR] else "⚠️ <b>À surveiller</b>")
    out = [f"🔎 <b>Vérification de TrendPullback</b>\n{verdict} · ✅ {c[OK]} · ⚠️ {c[WARN]} · ❌ {c[ERR]}"]
    for s in sections:
        bad = [(l, t) for l, t in s.items if l in (WARN, ERR)]
        infos = [t for l, t in s.items if l == INFO]
        n_ok = sum(1 for l, _ in s.items if l == OK)
        head = f"\n<b>{esc(s.title)}</b>" + (f" · ✅ {n_ok}" if n_ok else "")
        body = [f"{ICON[l]} {esc(t)}" for l, t in bad] + [f"ℹ️ {esc(t)}" for t in infos[:3]]
        out.append(head + ("\n" + "\n".join(body) if body else ""))
    text = "\n".join(out)
    return text if len(text) <= limit else text[:limit - 60] + "\n… (suite dans le rapport complet)"


def full_report(sections: list[Section], now: float | None = None) -> str:
    t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now or time.time()))
    c = counts(sections)
    lines = [f"Vérification de TrendPullback - {t}", f"OK {c[OK]} / avertissements {c[WARN]} / erreurs {c[ERR]}", ""]
    tag = {OK: "[OK]    ", WARN: "[ATTENTION]", ERR: "[ERREUR]", INFO: "[INFO]  "}
    for s in sections:
        lines.append(f"== {s.title} ==")
        lines += [f"{tag[l]} {txt}" for l, txt in s.items]
        lines += s.details
        lines.append("")
    return "\n".join(lines)
