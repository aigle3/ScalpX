"""Registre SQLite : instances, état souhaité, historique (soldes, cycles, clôtures 500, retraits,
événements, versions de l'EA, déploiements). Aucun mot de passe n'est jamais stocké ici."""
from __future__ import annotations

import csv
import io
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

# États souhaités d'une instance
EN_CREATION = "EN_CREATION"
DEMARRE = "DEMARRE"
ARRETE = "ARRETE"
MAINTENANCE = "MAINTENANCE"
ARCHIVE = "ARCHIVE"
DESIRED_STATES = (EN_CREATION, DEMARRE, ARRETE, MAINTENANCE, ARCHIVE)

SCHEMA = """
CREATE TABLE IF NOT EXISTS instances(
  id TEXT PRIMARY KEY, login INTEGER NOT NULL, server TEXT NOT NULL, alias TEXT DEFAULT '',
  folder TEXT NOT NULL, created_at INTEGER NOT NULL, ea_version TEXT DEFAULT '',
  desired_state TEXT NOT NULL, archived_at INTEGER, currency TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS balances(
  iid TEXT, ts INTEGER, balance REAL, equity REAL, floating_ea REAL, cycle INTEGER, etat TEXT);
CREATE INDEX IF NOT EXISTS ix_bal ON balances(iid, ts);
CREATE TABLE IF NOT EXISTS events(
  iid TEXT, ts INTEGER, type TEXT, prio INTEGER, text TEXT);
CREATE INDEX IF NOT EXISTS ix_ev ON events(ts);
CREATE TABLE IF NOT EXISTS cycles(
  iid TEXT, cycle INTEGER, started_ts INTEGER, ended_ts INTEGER, final_balance REAL, withdrawn REAL);
CREATE TABLE IF NOT EXISTS closures500(
  iid TEXT, ts INTEGER, kind TEXT, final_balance REAL, delta REAL);
CREATE TABLE IF NOT EXISTS withdrawals(
  iid TEXT, ts INTEGER, amount REAL, balance_before REAL, balance_after REAL);
CREATE TABLE IF NOT EXISTS versions(
  iid TEXT, ts INTEGER, version TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS deployments(
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_ts INTEGER, ended_ts INTEGER, from_version TEXT,
  to_version TEXT, status TEXT, targets TEXT, report TEXT, pending TEXT);
CREATE TABLE IF NOT EXISTS incidents(
  iid TEXT, ts INTEGER, kind TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS actions(
  ts INTEGER, iid TEXT, ok INTEGER, text TEXT, result TEXT);
CREATE INDEX IF NOT EXISTS ix_act ON actions(ts);
CREATE TABLE IF NOT EXISTS labo(
  iid TEXT, ts INTEGER, kind TEXT, sc TEXT, data TEXT);
CREATE INDEX IF NOT EXISTS ix_labo ON labo(ts);
CREATE TABLE IF NOT EXISTS opt_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, grp TEXT, status TEXT, summary TEXT, applied TEXT, bt INTEGER,
  reverted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS backtests(
  id INTEGER PRIMARY KEY AUTOINCREMENT, created INTEGER, kind TEXT, d_from INTEGER, d_to INTEGER, model TEXT,
  source TEXT, status TEXT, result TEXT, ended INTEGER, error TEXT);
"""


@dataclass
class Instance:
    id: str
    login: int
    server: str
    alias: str
    folder: str
    created_at: int
    ea_version: str
    desired_state: str
    archived_at: int | None = None
    currency: str = ""

    @property
    def label(self) -> str:
        """Alias lisible, sinon le login."""
        return self.alias or str(self.login)


def _row_to_inst(r: sqlite3.Row) -> Instance:
    return Instance(r["id"], r["login"], r["server"], r["alias"] or "", r["folder"], r["created_at"],
                    r["ea_version"] or "", r["desired_state"], r["archived_at"], r["currency"] or "")


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._cx = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self._cx.row_factory = sqlite3.Row
        with self._lock:
            try:
                # journal WAL : chaque écriture (solde, événement, méta) ne force plus deux synchronisations disque,
                # les lectures ne bloquent pas les écritures ; NORMAL reste sûr en WAL (au pire la dernière écriture
                # est perdue sur coupure de courant, la base n'est jamais corrompue)
                self._cx.execute("PRAGMA journal_mode=WAL")
                self._cx.execute("PRAGMA synchronous=NORMAL")
            except sqlite3.DatabaseError:
                pass                                            # système de fichiers sans WAL : mode par défaut
            self._cx.executescript(SCHEMA)
            self._cx.commit()

    def backup_to(self, dest: Path | str) -> None:
        """Copie cohérente de la base (API de sauvegarde SQLite, sans arrêter le bot)."""
        dest = Path(dest)
        tmp = dest.with_name(dest.name + ".tmp")
        with self._lock:
            out = sqlite3.connect(str(tmp))
            try:
                self._cx.backup(out)
            finally:
                out.close()
        os.replace(tmp, dest)

    def close(self) -> None:
        with self._lock:
            self._cx.close()

    def _exec(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._cx.execute(sql, tuple(args))
            self._cx.commit()
            return cur

    def _all(self, sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._cx.execute(sql, tuple(args)).fetchall())

    # ---- méta (message du tableau de bord, serveurs mémorisés, mises à jour…)
    def meta_get(self, key: str, default: str = "") -> str:
        r = self._all("SELECT value FROM meta WHERE key=?", (key,))
        return r[0]["value"] if r else default

    def meta_set(self, key: str, value: str) -> None:
        self._exec("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, value))

    # ---- instances
    def add_instance(self, inst: Instance) -> None:
        """Nouvelle instance. Si le même compte avait été supprimé (ligne ARCHIVE conservée avec son historique), on le réactive."""
        old = self.get(inst.id)
        if old is not None and old.desired_state == ARCHIVE:
            self._exec("UPDATE instances SET login=?, server=?, alias=?, folder=?, created_at=?, ea_version=?, desired_state=?, "
                       "archived_at=NULL, currency=? WHERE id=?",
                       (inst.login, inst.server, inst.alias, inst.folder, inst.created_at, inst.ea_version,
                        inst.desired_state, inst.currency, inst.id))
            return
        self._exec("INSERT INTO instances(id,login,server,alias,folder,created_at,ea_version,desired_state,currency) "
                   "VALUES(?,?,?,?,?,?,?,?,?)",
                   (inst.id, inst.login, inst.server, inst.alias, inst.folder, inst.created_at,
                    inst.ea_version, inst.desired_state, inst.currency))

    def get(self, iid: str) -> Instance | None:
        r = self._all("SELECT * FROM instances WHERE id=?", (iid,))
        return _row_to_inst(r[0]) if r else None

    def exists_active(self, iid: str) -> bool:
        """Vrai si une instance non archivée porte déjà cet identifiant."""
        r = self._all("SELECT 1 FROM instances WHERE id=? AND desired_state<>?", (iid, ARCHIVE))
        return bool(r)

    def list(self, include_archived: bool = False, states: tuple[str, ...] | None = None) -> list[Instance]:
        sql = "SELECT * FROM instances"
        cond: list[str] = []
        args: list[Any] = []
        if not include_archived:
            cond.append("desired_state<>?")
            args.append(ARCHIVE)
        if states:
            cond.append("desired_state IN (%s)" % ",".join("?" * len(states)))
            args += list(states)
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        sql += " ORDER BY created_at, id"
        return [_row_to_inst(r) for r in self._all(sql, args)]

    def set_desired(self, iid: str, state: str) -> None:
        assert state in DESIRED_STATES, state
        self._exec("UPDATE instances SET desired_state=? WHERE id=?", (state, iid))

    def set_version(self, iid: str, version: str, note: str = "") -> None:
        cur = self.get(iid)
        self._exec("UPDATE instances SET ea_version=? WHERE id=?", (version, iid))
        if cur is None or cur.ea_version != version:
            self._exec("INSERT INTO versions(iid,ts,version,note) VALUES(?,?,?,?)", (iid, int(time.time()), version, note))

    def set_currency(self, iid: str, cur: str) -> None:
        self._exec("UPDATE instances SET currency=? WHERE id=?", (cur, iid))

    def archive(self, iid: str) -> None:
        self._exec("UPDATE instances SET desired_state=?, archived_at=? WHERE id=?", (ARCHIVE, int(time.time()), iid))

    def delete_instance(self, iid: str) -> None:
        """Suppression pure du registre (échec de création : aucune instance fantôme)."""
        self._exec("DELETE FROM instances WHERE id=?", (iid,))

    # ---- historique
    def add_balance(self, iid: str, balance: float, equity: float, floating: float, cycle: int, etat: str,
                    ts: int | None = None) -> None:
        self._exec("INSERT INTO balances VALUES(?,?,?,?,?,?,?)",
                   (iid, ts or int(time.time()), balance, equity, floating, cycle, etat))

    def balances(self, iid: str, since: int) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM balances WHERE iid=? AND ts>=? ORDER BY ts", (iid, since))

    def add_event(self, iid: str, ts: int, typ: str, prio: int, text: str) -> None:
        self._exec("INSERT INTO events VALUES(?,?,?,?,?)", (iid, ts, typ, prio, text))

    def events_since(self, since: int, iid: str | None = None) -> list[sqlite3.Row]:
        if iid:
            return self._all("SELECT * FROM events WHERE ts>=? AND iid=? ORDER BY ts", (since, iid))
        return self._all("SELECT * FROM events WHERE ts>=? ORDER BY ts", (since,))

    # ---- labo des filtres (EA 7.1.0) : REEL (objectif réel) / SERIE (objectif virtuel)
    def add_labo(self, iid: str, ts: int, kind: str, sc: str, data: str) -> None:
        self._exec("INSERT INTO labo VALUES(?,?,?,?,?)", (iid, ts, kind, sc, data))

    def labo_since(self, since: int, iid: str | None = None) -> list[sqlite3.Row]:
        if iid:
            return self._all("SELECT * FROM labo WHERE ts>=? AND iid=? ORDER BY ts", (since, iid))
        return self._all("SELECT * FROM labo WHERE ts>=? ORDER BY ts", (since,))

    # ---- backtests (EA 7.2.0)
    def bt_create(self, kind: str, d_from: int, d_to: int, model: str, source: str) -> int:
        with self._lock:
            cur = self._cx.execute("INSERT INTO backtests(created, kind, d_from, d_to, model, source, status, result, ended, error) "
                                   "VALUES(?,?,?,?,?,?,?,?,?,?)", (int(time.time()), kind, d_from, d_to, model, source, "en_cours", "", 0, ""))
            self._cx.commit()
            return int(cur.lastrowid)

    def bt_update(self, rid: int, status: str, result: str = "", error: str = "") -> None:
        self._exec("UPDATE backtests SET status=?, result=?, error=?, ended=? WHERE id=?", (status, result, error, int(time.time()), rid))

    def bt_get(self, rid: int) -> sqlite3.Row | None:
        r = self._all("SELECT * FROM backtests WHERE id=?", (rid,))
        return r[0] if r else None

    def bt_list(self, limit: int = 8) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM backtests ORDER BY id DESC LIMIT ?", (limit,))

    # ---- optimiseur
    def opt_add(self, grp: str, status: str, summary: str, applied: str, bt: int) -> int:
        with self._lock:
            cur = self._cx.execute("INSERT INTO opt_runs(ts, grp, status, summary, applied, bt, reverted) VALUES(?,?,?,?,?,?,0)",
                                   (int(time.time()), grp, status, summary, applied, bt))
            self._cx.commit()
            return int(cur.lastrowid)

    def opt_list(self, limit: int = 10) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM opt_runs ORDER BY id DESC LIMIT ?", (limit,))

    def opt_history(self) -> list[sqlite3.Row]:
        """Toutes les nuits, de la plus ancienne à la plus récente (reset : valeurs d'avant la 1re optimisation)."""
        return self._all("SELECT * FROM opt_runs ORDER BY id ASC")

    def opt_get(self, rid: int) -> sqlite3.Row | None:
        r = self._all("SELECT * FROM opt_runs WHERE id=?", (rid,))
        return r[0] if r else None

    def opt_mark_reverted(self, rid: int) -> None:
        self._exec("UPDATE opt_runs SET reverted=1 WHERE id=?", (rid,))

    def bt_interrupted(self) -> int:
        """Démarrage du bot : un backtest « en cours » ne l'est plus (suivi perdu)."""
        rows = self._all("SELECT id FROM backtests WHERE status='en_cours'")
        for r in rows:
            self.bt_update(int(r["id"]), "interrompu", "", "Bot redémarré pendant le test.")
        return len(rows)

    def add_closure500(self, iid: str, kind: str, final_balance: float, delta: float, ts: int | None = None) -> None:
        self._exec("INSERT INTO closures500 VALUES(?,?,?,?,?)", (iid, ts or int(time.time()), kind, final_balance, delta))

    def closures_since(self, since: int) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM closures500 WHERE ts>=? ORDER BY ts", (since,))

    def add_withdrawal(self, iid: str, amount: float, before: float, after: float, ts: int | None = None) -> None:
        self._exec("INSERT INTO withdrawals VALUES(?,?,?,?,?)", (iid, ts or int(time.time()), amount, before, after))

    def withdrawals_since(self, since: int) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM withdrawals WHERE ts>=? ORDER BY ts", (since,))

    def total_withdrawn(self, iid: str | None = None) -> float:
        if iid:
            r = self._all("SELECT COALESCE(SUM(amount),0) s FROM withdrawals WHERE iid=?", (iid,))
        else:
            r = self._all("SELECT COALESCE(SUM(amount),0) s FROM withdrawals")
        return float(r[0]["s"])

    def add_cycle(self, iid: str, cycle: int, started: int, ended: int, final_balance: float, withdrawn: float) -> None:
        self._exec("INSERT INTO cycles VALUES(?,?,?,?,?,?)", (iid, cycle, started, ended, final_balance, withdrawn))

    def cycles_since(self, since: int) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM cycles WHERE ended_ts>=? ORDER BY ended_ts", (since,))

    def add_incident(self, iid: str, kind: str, text: str) -> None:
        self._exec("INSERT INTO incidents VALUES(?,?,?,?)", (iid, int(time.time()), kind, text))

    def incidents_since(self, since: int) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM incidents WHERE ts>=? ORDER BY ts", (since,))

    # ---- journal des actions demandées depuis Telegram (30 jours conservés)
    def add_action(self, iid: str, text: str, ok: bool, result: str = "") -> None:
        now = int(time.time())
        self._exec("INSERT INTO actions VALUES(?,?,?,?,?)", (now, iid, 1 if ok else 0, text[:120], result[:200]))
        self._exec("DELETE FROM actions WHERE ts<?", (now - 30 * 86400,))

    def actions_recent(self, limit: int = 100) -> list[sqlite3.Row]:
        """Les plus récentes d'abord."""
        return self._all("SELECT * FROM actions ORDER BY ts DESC, rowid DESC LIMIT ?", (limit,))

    # ---- déploiements
    def start_deployment(self, from_v: str, to_v: str, targets: list[str]) -> int:
        cur = self._exec("INSERT INTO deployments(started_ts,from_version,to_version,status,targets,report,pending) "
                         "VALUES(?,?,?,?,?,?,?)", (int(time.time()), from_v, to_v, "EN_COURS", ",".join(targets), "", ",".join(targets)))
        return int(cur.lastrowid or 0)

    def update_deployment(self, dep_id: int, **fields: Any) -> None:
        if not fields:
            return
        keys = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE deployments SET {keys} WHERE id=?", (*fields.values(), dep_id))

    def open_deployment(self) -> sqlite3.Row | None:
        r = self._all("SELECT * FROM deployments WHERE status='EN_COURS' ORDER BY id DESC LIMIT 1")
        return r[0] if r else None

    def deployments(self, limit: int = 10) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM deployments ORDER BY id DESC LIMIT ?", (limit,))

    # ---- export CSV
    def export_csv(self, table: str, since: int = 0) -> str:
        """Export CSV d'une table d'historique (liste blanche, jamais un nom reçu tel quel)."""
        allowed = {"balances": "ts", "events": "ts", "closures500": "ts", "withdrawals": "ts", "cycles": "ended_ts"}
        if table not in allowed:
            raise ValueError("table inconnue")
        rows = self._all(f"SELECT * FROM {table} WHERE {allowed[table]}>=? ORDER BY {allowed[table]}", (since,))
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        if rows:
            w.writerow(rows[0].keys())
            for r in rows:
                w.writerow(list(r))
        return buf.getvalue()

    def prune(self, keep_days: int = 120) -> None:
        """Purge de l'historique des soldes (1 point/min/instance) au-delà de keep_days."""
        self._exec("DELETE FROM balances WHERE ts<?", (int(time.time()) - keep_days * 86400,))
        self._exec("DELETE FROM labo WHERE ts<?", (int(time.time()) - keep_days * 86400,))
