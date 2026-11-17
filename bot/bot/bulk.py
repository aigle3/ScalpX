"""Bot 3.20.0 : 📄 création de plusieurs comptes depuis un fichier texte envoyé dans Telegram.

Une ligne par compte : `numéro:mot de passe:serveur` (lignes vides et « # » ignorées ; le mot de passe peut contenir
des « : » : numéro = 1er champ, serveur = dernier). Le fichier est effacé de la conversation dès réception ; les mots
de passe restent en mémoire le temps de la création puis sont oubliés (seul MT5 les mémorise, comme avec ➕).
Créations 2 par 2 ; chaque compte : copie de BASE, lancement, connexion — refusé
(mauvais mot de passe / serveur) au bout de ~40 s, abandonné sans signe de l'EA au bout de create_timeout_s : aucune
création n'attend indéfiniment. Preset par défaut (⭐ actif) appliqué à chaque compte créé.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field

from core.ids import make_id, valid_login, valid_server

log = logging.getLogger(__name__)

MAX_LINES = 100
TTL_S = 600                                   # récapitulatif non confirmé : mots de passe oubliés après 10 min
PARALLEL = 2                                  # créations 2 par 2


@dataclass
class Row:
    login: str
    password: str = field(repr=False)
    server: str
    line: int
    state: str = "⏸ en attente"
    ok: bool | None = None


def parse(text: str) -> tuple[list[Row], list[str]]:
    """Texte → (comptes valides, erreurs lisibles SANS mot de passe). Doublons du fichier ignorés."""
    rows: list[Row] = []
    errors: list[str] = []
    seen: set[str] = set()
    for n, raw in enumerate((text or "").replace("\r", "").split("\n"), 1):
        line = raw.strip().lstrip("﻿")
        if not line or line.startswith("#"):
            continue
        parts = line.split(":")
        if len(parts) < 3:
            errors.append(f"ligne {n} : format attendu numéro:mot de passe:serveur")
            continue
        login, server, pw = parts[0].strip(), parts[-1].strip(), ":".join(parts[1:-1])
        if not valid_login(login):
            errors.append(f"ligne {n} : numéro de compte invalide")
            continue
        if not valid_server(server):
            errors.append(f"ligne {n} ({login}) : nom de serveur invalide")
            continue
        if not pw:
            errors.append(f"ligne {n} ({login}) : mot de passe vide")
            continue
        key = make_id(login, server)
        if key in seen:
            errors.append(f"ligne {n} ({login}) : en double dans le fichier")
            continue
        seen.add(key)
        rows.append(Row(login, pw, server, n))
        if len(rows) >= MAX_LINES:
            errors.append(f"au-delà de {MAX_LINES} comptes : lignes suivantes ignorées")
            break
    return rows, errors


@dataclass
class Batch:
    token: str
    rows: list[Row]
    errors: list[str]
    skipped: list[str]
    created_at: float = field(default_factory=time.monotonic)

    def forget(self) -> None:
        for r in self.rows:
            r.password = ""


def new_batch(rows: list[Row], errors: list[str], exists) -> Batch:
    """Comptes déjà présents sur ce serveur : retirés (signalés)."""
    keep, skipped = [], []
    for r in rows:
        if exists(make_id(r.login, r.server)):
            skipped.append(f"{r.login} @ {r.server} : existe déjà")
        else:
            keep.append(r)
    return Batch(secrets.token_hex(4), keep, errors, skipped)


def summary(b: Batch) -> str:
    from .render import esc
    lines = [f"📄 <b>{len(b.rows)} compte(s) à créer</b> <i>(fichier effacé de la conversation)</i>"]
    lines += [f"• <code>{esc(r.login)}</code> @ {esc(r.server)}" for r in b.rows[:40]]
    if len(b.rows) > 40:
        lines.append(f"… et {len(b.rows) - 40} autre(s)")
    if b.skipped:
        lines.append("\n⏭ <b>Ignorés</b>\n" + "\n".join("• " + esc(s) for s in b.skipped[:20]))
    if b.errors:
        lines.append("\n⚠️ <b>Lignes refusées</b>\n" + "\n".join("• " + esc(e) for e in b.errors[:20]))
    if b.rows:
        lines.append("\nChaque compte : copie de BASE, connexion, puis preset par défaut. Un mot de passe ou un serveur faux "
                     "est détecté en ~40 s ; sans signe de l'EA, abandon au bout de quelques minutes. Création 2 par 2.")
    return "\n".join(lines)


def progress_text(b: Batch, done: bool) -> str:
    from .render import esc
    ok = sum(1 for r in b.rows if r.ok)
    ko = sum(1 for r in b.rows if r.ok is False)
    head = (f"{'✅' if not ko else '⚠️'} <b>Création terminée</b> : {ok} connecté(s), {ko} échec(s)" if done else
            f"⏳ <b>Création des comptes</b> : {ok + ko}/{len(b.rows)}")
    return head + "\n" + "\n".join(f"• <code>{esc(r.login)}</code> @ {esc(r.server)} : {esc(r.state)}" for r in b.rows[:60])


async def run(b: Batch, parallel: int, create, after_ok, edit, remember_server=None, tick_s: float = 3.0) -> None:
    """create(login, pw, server, progress) → CreateResult ; after_ok(iid) → ligne du preset ; edit(texte) met à jour
    le message de suivi (au plus toutes les tick_s secondes)."""
    sem = asyncio.Semaphore(max(1, min(4, parallel)))
    dirty = asyncio.Event()

    async def one(r: Row) -> None:
        async with sem:
            async def progress(t: str) -> None:
                r.state = t
                dirty.set()
            r.state = "📁 démarrage…"
            dirty.set()
            pw, r.password = r.password, ""                     # oublié dès qu'il est transmis
            try:
                res = await create(r.login, pw, r.server, progress)
            except Exception as exc:                             # jamais le mot de passe dans un message
                log.exception("création en lot %s", r.login)
                r.ok, r.state = False, f"❌ erreur : {type(exc).__name__}"
                dirty.set()
                return
            finally:
                del pw
            if res.ok:
                r.ok = True
                extra = ""
                try:
                    extra = await after_ok(res.iid)
                except Exception:
                    log.exception("preset du compte %s", res.iid)
                r.state = "✅ connecté" + (f" · {extra.strip()}" if extra else "")
                if remember_server:
                    remember_server(r.server)
            else:
                r.ok, r.state = False, f"❌ {res.message}"
            dirty.set()

    tasks = [asyncio.create_task(one(r)) for r in b.rows]

    async def refresher() -> None:
        while True:
            await dirty.wait()
            dirty.clear()
            try:
                await edit(progress_text(b, False))
            except Exception:
                log.debug("suivi de création non mis à jour", exc_info=True)
            await asyncio.sleep(tick_s)

    ref = asyncio.create_task(refresher())
    try:
        await asyncio.gather(*tasks)
    finally:
        ref.cancel()
        b.forget()
    await edit(progress_text(b, True))
