"""Installation de TrendPullback depuis Telegram (migration TEMPORAIRE, retirée par une prochaine mise à jour de ScalpX).

Déroulé (bouton « 🧩 Installer TrendPullback » du menu Système) :
  1. questions de l'installateur TrendPullback posées dans Telegram : jeton GitHub du dépôt TrendPullback, jeton du bot
     Telegram TrendPullback (différent de celui de ScalpX), chat_id, symbole, installation MT5 source, serveurs,
     surveillance externe ;
  2. paquet `stable` du dépôt TrendPullback téléchargé dans C:\\TrendPullback\\release ;
  3. `install.ps1 -Silent` lancé : il crée tout (Python, MT5 BASE, compilation de l'EA, bot, .env, tâche planifiée)
     et démarre le bot TrendPullback.

Sécurité :
  - les messages contenant un secret sont supprimés dès réception (app.h_text supprime chaque message texte) ;
  - les secrets restent en mémoire, ne sont jamais réaffichés (masqués dans les messages et le journal), et sont
    passés à l'installateur par l'environnement du SEUL processus d'installation (ni ligne de commande, ni fichier) ;
    seul install.ps1 les écrit, dans C:\\TrendPullback\\bot\\.env ;
  - effacés de la mémoire à la fin (réussite, échec ou annulation) ;
  - rien n'est écrit dans C:\\ScalpX ni C:\\MT5, l'EA ScalpX n'est pas concerné.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from bot.render import esc
from bot.ui import EditResult, Keyboard

log = logging.getLogger(__name__)

REPO = "aigle3/TrendPullback-MT5"
BRANCH = "stable"                                  # comme le bot TrendPullback : uniquement ce qui a passé les tests
API = "https://api.github.com"
TIMEOUT_S = 40 * 60                                # Python + copie de MT5 + compilation + dépendances
PROGRESS_S = 4.0

GH_RE = re.compile(r"^(github_pat_|ghp_)[A-Za-z0-9_]{20,}$")
TG_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{30,}$")
CHAT_RE = re.compile(r"^-?\d{5,}(,-?\d{5,})*$")
SYM_RE = re.compile(r"^[A-Za-z0-9._#-]{3,20}$")
SRV_RE = re.compile(r"^[A-Za-z0-9 ._,-]{3,200}$")
HC_RE = re.compile(r"^https://\S{8,300}$")
SECRET_PATTERNS = (re.compile(r"(github_pat_|ghp_)[A-Za-z0-9_]+"), re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}"))
FORBIDDEN = ("c:\\scalpx", "c:\\mt5")              # dossiers de ScalpX : jamais utilisés comme source ni cible

SYMBOLS = ("EURUSD", "EURUSDc")
TEXT_STEPS = ("gh", "tg", "chat", "sym", "srv", "hc")

Http = Callable[[str, dict], tuple]
Runner = Callable[[list, dict, list, int], int]    # (commande, environnement, lignes de sortie, délai) -> code de sortie


def _default_http(url: str, headers: dict) -> tuple:
    from core.github import http_get
    return http_get(url, headers)


def mask(text: str, secrets: list[str]) -> str:
    """Retire tout secret connu (et tout ce qui ressemble à un jeton) d'un texte destiné à Telegram ou au journal."""
    for s in sorted((s for s in secrets if s and len(s) >= 6), key=len, reverse=True):
        text = text.replace(s, "•••")
    for p in SECRET_PATTERNS:
        text = p.sub("•••", text)
    return text


def find_mt5_sources(roots: list[str] | None = None) -> list[str]:
    """Installations MT5 classiques (Program Files) : dossiers contenant terminal64.exe, hors dossiers ScalpX."""
    if roots is None:
        roots = [r for r in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")) if r]
    out: list[str] = []
    for root in roots:
        try:
            dirs = sorted(Path(root).iterdir())
        except OSError:
            continue
        for d in dirs:
            if d.is_dir() and (d / "terminal64.exe").exists() and not str(d).lower().startswith(FORBIDDEN):
                out.append(str(d))
    out.sort(key=lambda p: (0 if "roboforex" in p.lower() else 1, p.lower()))
    return out


def run_process(cmd: list, env: dict, lines: list, timeout: int) -> int:
    """Lance l'installateur (sans fenêtre) et range sa sortie ligne par ligne dans `lines`. Renvoie son code de sortie."""
    no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000) if os.name == "nt" else 0
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    try:
        p = subprocess.Popen(cmd, creationflags=no_win | breakaway, **kw)
    except OSError:
        p = subprocess.Popen(cmd, creationflags=no_win, **kw)
    timer = threading.Timer(timeout, p.kill)
    timer.start()
    try:
        assert p.stdout is not None
        for raw in p.stdout:
            lines.append(raw.decode("utf-8", errors="replace").rstrip())
        return p.wait()
    finally:
        timer.cancel()


class TPInstaller:
    """Assistant d'installation (un seul à la fois, un seul message Telegram édité au fil des étapes)."""

    def __init__(self, sender, chat_id: int, scalpx_token: str, log_dir: Path,
                 root: Path = Path(r"C:\TrendPullback"), http: Http | None = None, runner: Runner | None = None,
                 mt5_roots: list[str] | None = None, is_windows: bool | None = None) -> None:
        self.sender, self.chat_id, self.scalpx_token = sender, chat_id, scalpx_token
        self.log_dir, self.root = Path(log_dir), Path(root)
        self.http = http or _default_http
        self.runner = runner or run_process
        self.mt5_roots = mt5_roots
        self.is_windows = (os.name == "nt") if is_windows is None else is_windows
        self.msg_id: int | None = None
        self.running = False
        self._reset()

    # ------------------------------------------------------------------ état
    def _reset(self) -> None:
        self.step = ""
        self.a: dict[str, str] = {}                 # réponses ; secrets compris : effacé à la fin
        self.info: dict[str, str] = {}              # éléments affichables (jamais un secret)
        self.sources: list[str] = []
        self.flash = ""

    @property
    def awaiting(self) -> bool:
        return self.step in TEXT_STEPS and not self.running

    @property
    def release(self) -> Path:
        return self.root / "release"

    @property
    def env_file(self) -> Path:
        return self.root / "bot" / ".env"

    @property
    def base_ready(self) -> bool:
        return (self.root / "MT5" / "BASE" / "terminal64.exe").exists()

    def _secrets(self) -> list[str]:
        return [self.a.get(k, "") for k in ("gh", "tg", "hc")]

    # ------------------------------------------------------------------ affichage
    async def _show(self, text: str, kb: Keyboard | None = None) -> None:
        text = mask(text, self._secrets())
        if self.msg_id is not None:
            r = await self.sender.edit(self.chat_id, self.msg_id, text, kb)
            if r in (EditResult.OK, EditResult.SAME):
                return
            if r == EditResult.RETRY:
                return
        self.msg_id = await self.sender.send(self.chat_id, text, kb)

    def _head(self) -> str:
        fl = f"\n{self.flash}\n" if self.flash else ""
        self.flash = ""
        return "🧩 <b>Installation de TrendPullback</b>\n" + fl

    async def render(self) -> None:
        cancel = [("✖ Annuler", "mig:cancel")]
        s = self.step
        h = self._head()
        if s == "gh":
            await self._show(h + "\n<b>1. Jeton GitHub du dépôt TrendPullback</b>\n"
                             "Fine-grained (github_pat_…), dépôt <code>TrendPullback-MT5</code> seulement, "
                             "<i>Contents : Read-only</i>. Ce n'est PAS celui de ScalpX.\n\n"
                             "✉️ Envoie-le en message : il est effacé aussitôt et n'est jamais réaffiché.", [cancel])
        elif s == "tg":
            await self._show(h + f"\n✅ GitHub : {esc(self.info.get('gh', ''))}\n\n"
                             "<b>2. Jeton du bot Telegram TrendPullback</b>\n"
                             "Un NOUVEAU bot créé avec @BotFather (pas celui de ScalpX).\n\n"
                             "✉️ Envoie le jeton (123456789:AA…) : message effacé aussitôt.", [cancel])
        elif s == "chat":
            await self._show(h + f"\n✅ Bot Telegram : {esc(self.info.get('tg', ''))}\n\n"
                             "<b>3. chat_id autorisé à piloter TrendPullback</b>\n"
                             "Le même que ce chat, ou envoie un autre numéro (plusieurs : séparés par des virgules).",
                             [[(f"👤 Ce chat ({self.chat_id})", "mig:chat")], cancel])
        elif s == "sym":
            await self._show(h + "\n<b>4. Symbole du graphique qui héberge l'EA</b>\n"
                             "L'EA trade ses 7 paires quel que soit ce graphique. Compte cent : nom exact chez "
                             "RoboForex (ex. EURUSDc). Choisis ou envoie le nom.",
                             [[(x, f"mig:sym:{i}") for i, x in enumerate(SYMBOLS)], cancel])
        elif s == "src":
            rows: Keyboard = [[(Path(p).name[:40], f"mig:src:{i}")] for i, p in enumerate(self.sources[:8])]
            lst = "\n".join(f"• <code>{esc(p)}</code>" for p in self.sources[:8])
            await self._show(h + "\n<b>5. Installation MT5 à copier dans C:\\TrendPullback\\MT5\\BASE</b>\n"
                             f"{lst}\n(copie seulement : l'installation d'origine n'est pas modifiée)", rows + [cancel])
        elif s == "srv":
            await self._show(h + "\n<b>6. Serveur(s) MT5 à pré-enregistrer</b> (facultatif)\n"
                             "Ex. <code>RoboForex-Demo</code>, séparés par des virgules. Sinon : saisi au premier compte.",
                             [[("⏭ Passer", "mig:skip")], cancel])
        elif s == "hc":
            await self._show(h + "\n<b>7. Surveillance externe</b> (facultatif)\n"
                             "URL de ping healthchecks.io (https://hc-ping.com/…) d'un check DÉDIÉ à TrendPullback.",
                             [[("⏭ Passer", "mig:skip")], cancel])
        elif s == "confirm":
            await self._show(h + "\n<b>Récapitulatif</b>\n" + self._recap() +
                             "\n\nL'installation dure de 5 à 30 min (Python, copie de MT5, compilation de l'EA, "
                             "dépendances). ScalpX continue de trader pendant ce temps. "
                             "N'installe pas de mise à jour de ScalpX avant la fin.",
                             [[("✅ Installer", "mig:go")], cancel])

    def _recap(self) -> str:
        a = self.a
        rows = [f"• Dépôt : <code>{REPO}</code> ({BRANCH}) {esc(self.info.get('gh', ''))}",
                f"• Jeton GitHub : {'reçu •••' if a.get('gh') else '—'}"]
        if self.info.get("repair"):
            rows.append("• .env existant : conservé (jetons Telegram inchangés)")
        else:
            rows += [f"• Bot Telegram : {esc(self.info.get('tg', ''))} (jeton reçu •••)",
                     f"• chat_id : <code>{esc(a.get('chat', ''))}</code>"]
        rows.append(f"• Symbole : <code>{esc(a.get('sym', 'EURUSD'))}</code>")
        rows.append("• MT5 BASE : " + ("déjà présent" if self.base_ready else
                                       f"copie de <code>{esc(a.get('src', '?'))}</code>"))
        rows.append(f"• Serveurs : {esc(a.get('srv') or '—')}")
        rows.append(f"• Surveillance : {'oui •••' if a.get('hc') else '—'}")
        return "\n".join(rows)

    # ------------------------------------------------------------------ entrées
    async def start(self) -> str:
        if self.running:
            self.flash = "⏳ Installation déjà en cours."
            return "En cours"
        if not self.is_windows:
            await self.sender.send(self.chat_id, "🧩 Installation de TrendPullback : uniquement sur le VPS Windows.")
            return ""
        self._reset()
        self.msg_id = None
        if self.env_file.exists():
            self.info["repair"] = "1"
            self.flash = ("ℹ️ TrendPullback est déjà installé : son .env est conservé (jetons Telegram inchangés). "
                          "L'installateur sera relancé pour compléter / réparer.")
        self.step = "gh"
        await self.render()
        return ""

    async def cancel(self, why: str = "❎ Installation annulée. Aucun secret conservé.") -> None:
        self._reset()
        await self._show("🧩 <b>Installation de TrendPullback</b>\n\n" + why)
        self.msg_id = None

    async def on_callback(self, parts: list[str]) -> str:
        cmd = parts[0] if parts else ""
        arg = parts[1] if len(parts) > 1 else ""
        if cmd == "start":
            return await self.start()
        if self.running:
            return "Installation en cours"
        if cmd == "cancel":
            await self.cancel()
            return ""
        if cmd == "chat" and self.step == "chat":
            self.a["chat"] = str(self.chat_id)
            return await self._next()
        if cmd == "sym" and self.step == "sym" and arg.isdigit() and int(arg) < len(SYMBOLS):
            self.a["sym"] = SYMBOLS[int(arg)]
            return await self._next()
        if cmd == "src" and self.step == "src" and arg.isdigit() and int(arg) < len(self.sources):
            self.a["src"] = self.sources[int(arg)]
            return await self._next()
        if cmd == "skip" and self.step in ("srv", "hc"):
            self.a[self.step] = ""
            return await self._next()
        if cmd == "go" and self.step == "confirm":
            self.running = True
            asyncio.get_event_loop().create_task(self._install_guarded())
            return "Installation lancée"
        return ""

    async def on_text(self, text: str) -> None:
        v = (text or "").strip()
        s = self.step
        if s == "gh":
            if not GH_RE.match(v):
                self.flash = "❌ Format inattendu : github_pat_… attendu."
            else:
                ok, msg = await asyncio.to_thread(self._check_github, v)
                if ok:
                    self.a["gh"], self.info["gh"] = v, msg
                    await self._next()
                    return
                self.flash = f"❌ {msg}"
        elif s == "tg":
            if not TG_RE.match(v):
                self.flash = "❌ Format inattendu : 123456789:AA… attendu."
            elif self.scalpx_token and v == self.scalpx_token:
                self.flash = "❌ C'est le jeton du bot ScalpX : crée un nouveau bot avec @BotFather."
            else:
                ok, msg = await asyncio.to_thread(self._check_telegram, v)
                if ok:
                    self.a["tg"], self.info["tg"] = v, msg
                    await self._next()
                    return
                self.flash = f"❌ {msg}"
        elif s == "chat":
            v = v.replace(" ", "")
            if CHAT_RE.match(v):
                self.a["chat"] = v
                await self._next()
                return
            self.flash = "❌ chat_id attendu : un nombre (ex. 123456789)."
        elif s == "sym":
            if SYM_RE.match(v):
                self.a["sym"] = v
                await self._next()
                return
            self.flash = "❌ Nom de symbole invalide."
        elif s == "srv":
            if SRV_RE.match(v):
                self.a["srv"] = ",".join(x.strip() for x in v.split(",") if x.strip())
                await self._next()
                return
            self.flash = "❌ Nom de serveur invalide."
        elif s == "hc":
            if HC_RE.match(v):
                self.a["hc"] = v
                await self._next()
                return
            self.flash = "❌ URL attendue : https://hc-ping.com/…"
        await self.render()

    async def _next(self) -> str:
        order = ["gh", "tg", "chat", "sym", "src", "srv", "hc", "confirm"]
        i = order.index(self.step) + 1 if self.step in order else 0
        while i < len(order):
            nxt = order[i]
            if nxt in ("tg", "chat") and self.info.get("repair"):
                i += 1
                continue
            if nxt == "src":
                if self.base_ready:
                    i += 1
                    continue
                self.sources = await asyncio.to_thread(find_mt5_sources, self.mt5_roots)
                if not self.sources:
                    await self.cancel("❌ Aucune installation MT5 trouvée dans Program Files. Installe le MT5 "
                                      "RoboForex (installation classique), puis relance « 🧩 Installer TrendPullback ».")
                    return ""
                if len(self.sources) == 1:
                    self.a["src"] = self.sources[0]
                    i += 1
                    continue
            break
        self.step = order[i]
        await self.render()
        return ""

    # ------------------------------------------------------------------ vérifications (dans un thread)
    def _gh_headers(self, tok: str) -> dict:
        return {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "scalpx-bot", "Authorization": f"Bearer {tok}"}

    def _check_github(self, tok: str) -> tuple[bool, str]:
        try:
            r = self.http(f"{API}/repos/{REPO}/commits/{BRANCH}", self._gh_headers(tok))
        except Exception as exc:                                        # réseau
            return False, f"GitHub injoignable : {type(exc).__name__}"
        code = r[0]
        if code == 200:
            try:
                j = json.loads(r[1].decode("utf-8"))
                self.info["sha"] = j.get("sha", "")
                self.info["msg"] = (j.get("commit", {}).get("message") or "").strip()
                self.info["date"] = j.get("commit", {}).get("committer", {}).get("date", "")
                first = self.info["msg"].splitlines()[0][:80] if self.info["msg"] else ""
                return True, f"accès OK, version « {first} »"
            except ValueError:
                return False, "réponse GitHub illisible"
        if code == 401:
            return False, "jeton refusé par GitHub (invalide ou expiré)."
        if code in (403, 404):
            return False, "ce jeton n'a pas accès au dépôt TrendPullback-MT5 (Contents : Read-only)."
        return False, f"GitHub : erreur {code}"

    def _check_telegram(self, tok: str) -> tuple[bool, str]:
        try:
            r = self.http(f"https://api.telegram.org/bot{tok}/getMe", {"User-Agent": "scalpx-bot"})
        except Exception as exc:
            return False, f"Telegram injoignable : {type(exc).__name__}"
        if r[0] == 200:
            try:
                u = json.loads(r[1].decode("utf-8")).get("result", {}).get("username", "")
                return True, f"@{u}" if u else "jeton valide"
            except ValueError:
                return True, "jeton valide"
        return False, "jeton refusé par Telegram."

    # ------------------------------------------------------------------ installation
    def env_for_child(self) -> dict:
        """Environnement du SEUL processus d'installation : celui du bot + les réponses (TP_*)."""
        env = dict(os.environ)
        a = self.a
        pairs = {"TP_GITHUB_TOKEN": a.get("gh", ""), "TP_TELEGRAM_TOKEN": a.get("tg", ""),
                 "TP_CHAT_IDS": a.get("chat", ""), "TP_SYMBOL": a.get("sym", "EURUSD"),
                 "TP_SERVERS": a.get("srv", ""), "TP_HEALTHCHECK_URL": a.get("hc", ""),
                 "TP_MT5_SOURCE": a.get("src", "")}
        for k, v in pairs.items():
            env.pop(k, None)
            if v:
                env[k] = v
        env.pop("TELEGRAM_TOKEN", None)                  # jamais ceux de ScalpX dans l'installateur TrendPullback
        env.pop("GITHUB_TOKEN", None)
        return env

    def command(self) -> list[str]:
        ps1 = str(self.release / "install.ps1").replace("'", "''")
        script = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; $ProgressPreference = 'SilentlyContinue'; "
                  f"try {{ & '{ps1}' -Silent; exit $LASTEXITCODE }} "
                  "catch { Write-Host ('[ERREUR] ' + $_.Exception.Message); exit 2 }")
        return ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script]

    def _download(self) -> None:
        from core.github import extract_zip
        sha = self.info.get("sha") or BRANCH
        r = self.http(f"{API}/repos/{REPO}/zipball/{sha}", self._gh_headers(self.a["gh"]))
        if r[0] != 200 or not r[1]:
            raise RuntimeError(f"téléchargement refusé par GitHub (code {r[0]})")
        if str(self.release).lower().startswith(FORBIDDEN):
            raise RuntimeError("dossier cible interdit")
        info = {"sha": self.info.get("sha", ""), "message": self.info.get("msg", ""), "date": self.info.get("date", ""),
                "fetched": int(time.time()), "remaining": 0, "later": []}
        self.release.parent.mkdir(parents=True, exist_ok=True)
        extract_zip(r[1], self.release, info)
        if not (self.release / "install.ps1").exists():
            raise RuntimeError("install.ps1 absent du paquet")

    async def _install_guarded(self) -> None:
        try:
            await self._install()
        except Exception as exc:
            log.error("installation TrendPullback en erreur : %s", mask(str(exc), self._secrets()))
            await self._show("🧩 <b>Installation de TrendPullback</b>\n\n❌ " + esc(mask(str(exc), self._secrets())))
        finally:
            self.running = False
            self._reset()                                # secrets effacés de la mémoire
            self.msg_id = None

    async def _install(self) -> None:
        title = "🧩 <b>Installation de TrendPullback</b>\n\n"
        self.step = "run"
        await self._show(title + "⬇️ Téléchargement du paquet TrendPullback…")
        await asyncio.to_thread(self._download)
        lines: list[str] = []
        t0 = time.time()
        task = asyncio.ensure_future(asyncio.to_thread(self.runner, self.command(), self.env_for_child(), lines, TIMEOUT_S))
        last = ""
        while not task.done():
            await asyncio.wait({task}, timeout=PROGRESS_S)
            tail = "\n".join(l for l in lines[-10:] if l.strip())
            if tail != last:
                last = tail
                await self._show(title + f"⏳ Installation en cours ({int(time.time() - t0) // 60} min)…\n"
                                 f"<pre>{esc(mask(tail, self._secrets()))[-3000:]}</pre>")
        code = task.result()
        self._write_log(lines, code)
        tail = esc(mask("\n".join(l for l in lines[-15:] if l.strip()), self._secrets()))[-3000:]
        if code == 0:
            who = self.info.get("tg", "ton bot TrendPullback")
            await self._show(title + f"✅ <b>TrendPullback installé</b> ({int(time.time() - t0) // 60} min).\n"
                             f"<pre>{tail}</pre>\n"
                             f"👉 Ouvre {esc(who)} dans Telegram et envoie-lui un message : il répond avec son tableau "
                             "de bord. Les secrets ont été écrits dans C:\\TrendPullback\\bot\\.env et effacés d'ici.\n"
                             "Journal complet (masqué) : data\\logs\\trendpullback_install.log.")
        else:
            await self._show(title + f"❌ <b>Installation interrompue</b> (code {code}).\n<pre>{tail}</pre>\n"
                             "Corrige le point signalé puis relance « 🧩 Installer TrendPullback » (les jetons seront "
                             "redemandés). Journal complet (masqué) : data\\logs\\trendpullback_install.log.")

    def _write_log(self, lines: list[str], code: int) -> None:
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            body = mask("\n".join(lines), self._secrets())
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            (self.log_dir / "trendpullback_install.log").write_text(f"{stamp} code {code}\n{body}\n", encoding="utf-8")
        except OSError:
            log.exception("journal d'installation TrendPullback non écrit")
