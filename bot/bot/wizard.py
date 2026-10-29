"""Assistant guidé (création / reconnexion) — sans dépendance Telegram : renvoie l'état suivant.

Le mot de passe : message SUPPRIMÉ immédiatement, conservé en mémoire uniquement le temps de la création,
jamais écrit dans les logs, SQLite ni un message du bot.
"""
from __future__ import annotations

import json
import logging
import re

from core.ids import make_id, valid_login, valid_server
from mt5 import base as basemod
from . import render
from .actions import Ctx
from .render import esc
from .ui import Sender, resolve

log = logging.getLogger(__name__)

LOGIN, PASSWORD, SERVER, ALIAS, CONFIRM, SERVER_TEXT = range(6)
CANCEL = [("✖ Annuler", "wz:cancel")]
END = -1
MAX_TRIES = 3                      # essais de mot de passe pour une création
PENDING_TTL_S = 600                # sans réponse après un refus : création abandonnée (dossier archivé)
RETRY_KB = [[("🔑 Ressaisir le mot de passe", "wz:rpw")], [("🌐 Changer de serveur", "wz:rsrv")], [("✖ Abandonner", "wz:rab")]]
_ALIAS_OK = re.compile(r"[^\w .\-éèêàùçÉÈÊÀÙÇ]")


class WizardCore:
    def __init__(self, ctx: Ctx, sender: Sender, chat_id: int, on_done) -> None:
        """on_done(result, mode, iid) : appelé (en arrière-plan) à la fin de la création/reconnexion."""
        self.ctx, self.sender, self.chat_id, self.on_done = ctx, sender, chat_id, on_done
        self.sessions: dict[int, dict] = {}
        self.pending: dict[int, dict] = {}         # création refusée en attente (jamais de mot de passe dedans)

    # ---- serveurs : liste du config.toml + serveurs saisis à la main (mémorisés après une création réussie)
    def known_servers(self) -> list[str]:
        out = list(self.ctx.cfg.mt5.servers)
        try:
            extra = json.loads(self.ctx.db.meta_get("servers_extra", "[]") or "[]")
        except ValueError:
            extra = []
        out += [x for x in extra if isinstance(x, str) and x not in out]
        return out

    def _remember_server(self, server: str) -> None:
        if server not in self.known_servers():
            extra = [x for x in self.known_servers() if x not in self.ctx.cfg.mt5.servers] + [server]
            self.ctx.db.meta_set("servers_extra", json.dumps(extra))

    def _forget_server(self, server: str) -> None:
        extra = [x for x in self.known_servers() if x not in self.ctx.cfg.mt5.servers and x != server]
        self.ctx.db.meta_set("servers_extra", json.dumps(extra))

    # ---- entrées
    async def begin_new(self, chat: int) -> int:
        problems = basemod.check_base(self.ctx.cfg)
        if problems:
            await self.sender.send(chat, "❌ <b>BASE non conforme</b> :\n• " + "\n• ".join(esc(p) for p in problems), None, True)
            return END
        self.sessions[chat] = {"mode": "new"}
        await self.sender.send(chat, "➕ <b>Nouveau compte</b>\n\nEnvoie le <b>numéro de compte</b> (chiffres).", [CANCEL], True)
        return LOGIN

    async def begin_recon(self, chat: int, code: str) -> int:
        inst = resolve(self.ctx.db, code)
        if inst is None:
            await self.sender.send(chat, "❌ Compte introuvable.", None, True)
            return END
        self.sessions[chat] = {"mode": "recon", "iid": inst.id, "login": str(inst.login), "server": inst.server, "alias": inst.alias}
        await self.sender.send(chat, f"🔑 <b>Reconnexion de {esc(inst.label)}</b> ({inst.login} · {esc(inst.server)})\n\n"
                                     "Envoie le <b>mot de passe</b> : je supprime ton message aussitôt (je ne le stocke jamais).", [CANCEL], True)
        return PASSWORD

    # ---- étapes
    async def on_login(self, chat: int, text: str) -> int:
        if not valid_login(text):
            await self.sender.send(chat, "❌ Numéro invalide : chiffres uniquement. Réessaie.", [CANCEL], True)
            return LOGIN
        self.sessions.setdefault(chat, {"mode": "new"})["login"] = text.strip()
        await self.sender.send(chat, "🔐 Envoie le <b>mot de passe</b> du compte.\nJe supprime ton message immédiatement et je ne le stocke jamais "
                                     "(c'est MT5 qui le mémorise).", [CANCEL], True)
        return PASSWORD

    async def on_password(self, chat: int, message_id: int, text: str) -> int:
        deleted = await self.sender.delete(chat, message_id)          # AVANT toute autre action
        s = self.sessions.get(chat)
        if s is None:
            return END
        if not deleted:
            await self.sender.send(chat, "⚠️ Je n'ai pas pu supprimer ton message : supprime-le toi-même maintenant.", None, False)
        if s["mode"] == "retry":                         # nouvel essai : même dossier, lancé tout de suite
            self.sessions.pop(chat, None)
            return await self._run_retry(chat, s, text)
        s["pw"] = text
        if s["mode"] == "recon":
            return await self._ask_confirm(chat)
        servers = self.known_servers()
        if not servers:                                   # aucun serveur connu (installation neuve) : saisie directe
            return await self.on_server_other(chat)
        default = self.ctx.cfg.mt5.default_server
        kb = [[(("✔ " if sv == default else "") + sv, f"srv:{i}")] for i, sv in enumerate(servers)]
        kb.append([("✏️ Autre serveur…", "srv:new")])
        kb.append(CANCEL)
        await self.sender.send(chat, "🌐 Choisis le <b>serveur</b> :", kb, True)
        return SERVER

    async def on_server(self, chat: int, idx: int) -> int:
        s = self.sessions.get(chat)
        servers = self.known_servers()
        if s is None or not (0 <= idx < len(servers)):
            return END
        return await self._after_server(chat, servers[idx])

    async def on_server_other(self, chat: int) -> int:
        if chat not in self.sessions:
            return END
        await self.sender.send(chat, "✏️ Envoie le <b>nom exact du serveur</b>, tel que MT5 l'affiche "
                                     "(Fichier → Ouvrir un compte).", [CANCEL], True)
        return SERVER_TEXT

    async def on_server_text(self, chat: int, text: str) -> int:
        if chat not in self.sessions:
            return END
        name = " ".join((text or "").split())
        if not valid_server(name):
            await self.sender.send(chat, "❌ Nom invalide (lettres, chiffres, espace, - . _ ; 2 à 64 caractères). Réessaie.", [CANCEL], True)
            return SERVER_TEXT
        known = {k.lower(): k for k in self.known_servers()}
        return await self._after_server(chat, known.get(name.lower(), name))     # casse d'un serveur connu respectée

    async def _after_server(self, chat: int, server: str) -> int:
        s = self.sessions[chat]
        s["server"] = server
        iid = make_id(s["login"], s["server"])
        if self.ctx.db.exists_active(iid):
            await self.sender.send(chat, f"❌ Ce compte existe déjà ({esc(iid)}).", None, True)
            self._clear(chat)
            return END
        if "tries" in s:                                 # changement de serveur après un refus : alias déjà saisi
            return await self._ask_confirm(chat)
        await self.sender.send(chat, "🏷 <b>Alias</b> facultatif (nom lisible) :", [[("Passer", "alias:skip")], CANCEL], True)
        return ALIAS

    async def on_alias(self, chat: int, text: str | None) -> int:
        s = self.sessions.get(chat)
        if s is None:
            return END
        s["alias"] = _ALIAS_OK.sub("", (text or "").strip())[:32]
        return await self._ask_confirm(chat)

    async def _ask_confirm(self, chat: int) -> int:
        s = self.sessions[chat]
        verb = "Créer" if s["mode"] == "new" else "Reconnecter"
        txt = (f"<b>{verb} ce compte ?</b>\n\nCompte : <code>{esc(s['login'])}</code>\nServeur : <code>{esc(s['server'])}</code>\n"
               f"Alias : {esc(s.get('alias') or '—')}\nMot de passe : reçu (jamais stocké)")
        await self.sender.send(chat, txt, [[(f"✅ {verb}", "wz:go"), ("✖ Annuler", "wz:no")]], True)
        return CONFIRM

    async def on_confirm(self, chat: int, go: bool) -> int:
        s = self.sessions.pop(chat, None)                              # le mot de passe quitte la session ici
        if s is None:
            return END
        if not go:
            s.clear()
            await self.sender.send(chat, "✖ Annulé.", None, True)
            return END
        mode, pw = s["mode"], s.pop("pw", "")
        login, server, alias, iid = s["login"], s["server"], s.get("alias", ""), s.get("iid", "")
        tries = int(s.get("tries", 0)) + 1
        s.clear()
        mid = await self.sender.send(chat, "⏳ " + ("Création" if mode == "new" else "Reconnexion") + " en cours…", None, True)

        async def progress(t: str) -> None:
            await self.sender.edit(chat, mid, "⏳ " + esc(t), None)

        async def task() -> None:
            if mode == "new":
                new_server = server not in self.known_servers()
                if new_server:
                    self._remember_server(server)                 # mémorisé tout de suite : plus besoin de le retaper
                res = await self.ctx.creator.create(login, pw, server, alias, progress, keep_on_refusal=True)
                if res.refused:
                    await self._refused(chat, res, {"login": login, "server": server, "alias": alias, "tries": tries,
                                                    "mid": mid, "new_server": new_server})
                    return
                if new_server and not res.ok and not (res.status is not None and res.status.connecte):
                    self._forget_server(server)                   # jamais connecté à ce serveur : probablement une faute de frappe
            else:
                res = await self.ctx.creator.reconnect(iid, pw, progress)
            await self.on_done(res, mode, mid)

        self._spawn(task())
        return END

    # ---- identifiants refusés à la création : ressaisie du mot de passe / autre serveur / abandon
    async def _refused(self, chat: int, res, p: dict) -> None:
        p["iid"] = res.iid
        if p["tries"] >= MAX_TRIES:
            await self.ctx.creator.abandon(res.iid, f"{MAX_TRIES} essais refusés")
            if p.get("new_server"):
                self._forget_server(p["server"])
            log.warning("création %s : %d essais refusés, abandon", res.iid, p["tries"])
            res.message = (f"le compte ne se connecte pas après {MAX_TRIES} essais (mot de passe ou serveur incorrect). "
                           "Vérifie tes identifiants puis recommence avec ➕ Nouveau compte.")
            res.refused = False
            await self.on_done(res, "new", p["mid"])
            return
        self.pending[chat] = p
        log.info("création %s : identifiants refusés (essai %d/%d), choix demandé", res.iid, p["tries"], MAX_TRIES)
        await self.sender.edit(chat, p["mid"],
                               f"🔐 <b>Le compte ne se connecte pas</b> ({esc(p['login'])} · {esc(p['server'])}) : "
                               f"mot de passe ou serveur incorrect.\n\nEssai {p['tries']}/{MAX_TRIES}. Que veux-tu faire ?\n"
                               f"<i>Sans réponse sous {PENDING_TTL_S // 60} min, la création est abandonnée.</i>", RETRY_KB)
        self._spawn(self._expire(chat, p))

    async def _expire(self, chat: int, p: dict) -> None:
        import asyncio
        await asyncio.sleep(PENDING_TTL_S)
        if self.pending.get(chat) is p:                    # toujours la même attente (pas de nouvel essai entre-temps)
            self.pending.pop(chat, None)
            s = self.sessions.get(chat)
            if s is not None and s.get("mode") == "retry":
                self._clear(chat)
            await self.ctx.creator.abandon(p["iid"], "création abandonnée (sans réponse)")
            await self.sender.edit(chat, p["mid"], "✖ Création abandonnée (sans réponse après le refus du mot de passe).", None)

    async def retry_password(self, chat: int) -> int:
        p = self.pending.get(chat)
        if p is None:
            await self.sender.send(chat, "⌛ Cette création n'est plus en attente : recommence avec ➕ Nouveau compte.", None, True)
            return END
        self.sessions[chat] = {"mode": "retry", **{k: p[k] for k in ("iid", "login", "server", "alias", "tries", "mid", "new_server")}}
        await self.sender.edit(chat, p["mid"], f"🔐 Identifiants refusés ({esc(p['login'])} · {esc(p['server'])}), essai {p['tries']}/{MAX_TRIES}.", None)
        await self.sender.send(chat, "🔑 Envoie le <b>mot de passe</b> : je supprime ton message aussitôt (je ne le stocke jamais).",
                               [[("✖ Abandonner", "wz:rab")]], True)
        return PASSWORD

    async def _run_retry(self, chat: int, s: dict, pw: str) -> int:
        if self.pending.get(chat, {}).get("iid") != s["iid"]:
            await self.sender.send(chat, "⌛ Cette création n'est plus en attente : recommence avec ➕ Nouveau compte.", None, True)
            return END
        p = self.pending.pop(chat)
        p["tries"] += 1
        mid = await self.sender.send(chat, f"⏳ Nouvel essai ({p['tries']}/{MAX_TRIES})…", None, True)
        p["mid"] = mid

        async def progress(t: str) -> None:
            await self.sender.edit(chat, mid, "⏳ " + esc(t), None)

        async def task() -> None:
            res = await self.ctx.creator.retry(p["iid"], pw, progress)
            if res.refused:
                await self._refused(chat, res, p)
                return
            if p.get("new_server") and not res.ok and not (res.status is not None and res.status.connecte):
                self._forget_server(p["server"])
            await self.on_done(res, "new", mid)

        self._spawn(task())
        return END

    async def retry_server(self, chat: int) -> int:
        p = self.pending.pop(chat, None)
        if p is None:
            await self.sender.send(chat, "⌛ Cette création n'est plus en attente : recommence avec ➕ Nouveau compte.", None, True)
            return END
        self._clear(chat)
        await self.ctx.creator.abandon(p["iid"], "changement de serveur")       # le dossier dépend du serveur : nouvelle copie
        if p.get("new_server"):
            self._forget_server(p["server"])
        await self.sender.edit(chat, p["mid"], f"🌐 Changement de serveur pour le compte {esc(p['login'])}.", None)
        self.sessions[chat] = {"mode": "new", "login": p["login"], "alias": p.get("alias", ""), "tries": p["tries"]}
        await self.sender.send(chat, "🔐 Envoie le <b>mot de passe</b> du compte.\nJe supprime ton message immédiatement et je ne le stocke jamais.",
                               [CANCEL], True)
        return PASSWORD

    async def retry_abandon(self, chat: int) -> int:
        p = self.pending.pop(chat, None)
        s = self.sessions.get(chat)
        if s is not None and s.get("mode") == "retry":
            self._clear(chat)
        if p is None:
            await self.sender.send(chat, "✖ Rien à abandonner.", None, True)
            return END
        await self.ctx.creator.abandon(p["iid"], "création abandonnée")
        if p.get("new_server"):
            self._forget_server(p["server"])
        await self.sender.edit(chat, p["mid"], f"✖ Création du compte {esc(p['login'])} abandonnée.", None)
        await self.dash_refresh()
        return END

    async def dash_refresh(self) -> None:
        """Surchargé par l'application (rafraîchit le tableau de bord) ; rien en test."""
        return None

    def _spawn(self, coro) -> None:
        import asyncio
        t = asyncio.get_event_loop().create_task(self._guard(coro))
        self._tasks = getattr(self, "_tasks", set())
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _guard(self, coro) -> None:
        try:
            await coro
        except Exception:
            log.exception("assistant : tâche en erreur")

    def _clear(self, chat: int) -> None:
        s = self.sessions.pop(chat, None)
        if s:
            s.clear()

    async def cancel(self, chat: int) -> int:
        self._clear(chat)
        await self.sender.send(chat, "✖ Annulé.", None, True)
        return END

    def timeout(self, chat: int) -> None:
        """Conversation abandonnée : le mot de passe éventuel est effacé de la mémoire."""
        self._clear(chat)
