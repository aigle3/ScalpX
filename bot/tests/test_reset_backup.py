"""Bot 3.17.0 : ♻️ réinitialisation du serveur et ☁️ sauvegarde GitHub des presets."""
from __future__ import annotations

import base64
import json

from core import ghbackup, reset as rs
from storage.db import Database, Instance


def test_reinitialisation_garde_base_presets_et_reglages_du_bot(tmp_path):
    base, inst, arch, bt, common = (tmp_path / n for n in ("mt5/BASE", "mt5/instances", "mt5/archives", "mt5/backtest", "common"))
    for d in (base, inst / "1_X", arch / "2_Y", bt, common):
        d.mkdir(parents=True)
    (base / "terminal64.exe").write_bytes(b"MZ")
    (inst / "1_X" / "terminal64.exe").write_bytes(b"MZ")
    (common / "statut_1_X.json").write_text("{}")
    (common / "scalpx_preset.cfg").write_text("# preset=Martin\n")
    rep = rs.wipe_files(inst, arch, bt, common, base)
    assert not rep.errors and not any(inst.iterdir()) and not any(arch.iterdir()) and not bt.exists()
    assert (base / "terminal64.exe").is_file() and (common / "scalpx_preset.cfg").is_file() and rep.files == 1
    # garde-fou : un dossier « instances » qui contiendrait BASE n'est jamais vidé
    rep = rs.wipe_files(base.parent, arch, bt, common, base)
    assert rep.errors and (base / "terminal64.exe").is_file()
    db = Database(tmp_path / "x.db")
    db.add_instance(Instance("1_X", 1, "X", "", str(inst / "1_X"), 0, "", "DEMARRE", None, "EUR"))
    db.add_balance("1_X", 100.0, 100.0, 0.0, 1, "ACTIF")
    for k, v in (("installed_commit", "abc"), ("opt_day", "3"), ("cycle_start_1_X", "5"), ("ghb_hash", "h")):
        db.meta_set(k, v)
    rs.wipe_db(db, rep)
    assert db.list(include_archived=True) == [] and db.balances("1_X", 0) == []
    assert db.meta_get("installed_commit") == "abc" and db.meta_get("ghb_hash") == "h"
    assert db.meta_get("opt_day") == "" and db.meta_get("cycle_start_1_X") == ""


def test_sauvegarde_github(cfg):
    """Bot 4.2.0 : dépôt PRIVÉ de l'utilisateur (BACKUP_REPO), presets chiffrés si BACKUP_KEY ; lus bruts."""
    from core import crypt
    assert ghbackup.repo(cfg) == ""                                     # pas de jeton : désactivée
    cfg.backup_token = "tok"
    assert ghbackup.repo(cfg) == ""                                     # dépôt pas encore choisi
    cfg.backup_repo = "moi/scalpx-sauvegarde"
    assert ghbackup.repo(cfg) == "moi/scalpx-sauvegarde"
    store = {}
    calls = []

    def http(method, url, body=None):
        calls.append((method, url, body))
        name = url.rsplit("/", 1)[1]
        if method == "GET":
            return (200, {"sha": "s1", "content": base64.b64encode(store[name]).decode()}) if name in store else (404, {})
        store[name] = base64.b64decode(body["content"])
        return 201, {}

    assert ghbackup.push(cfg, "[preset=Martin]\nfast=12\n", http) == ""
    assert "sha" not in calls[-1][2] and calls[-1][1].endswith("/repos/moi/scalpx-sauvegarde/contents/presets.txt")
    assert ghbackup.push(cfg, "[preset=Martin]\nfast=13\n", http) == ""
    assert calls[-1][2]["sha"] == "s1"                                  # mise à jour du fichier existant
    assert ghbackup.fetch(cfg, http) == (b"[preset=Martin]\nfast=13\n", "")
    cfg.backup_key = crypt.new_key("motdepasse-1")                     # chiffrement : presets.enc, illisible sans clé
    assert ghbackup.push(cfg, "[preset=Martin]\nfast=14\n", http) == ""
    blob, err = ghbackup.fetch(cfg, http)
    assert err == "" and crypt.is_encrypted(blob) and b"fast=14" not in blob
    assert crypt.decrypt(blob, password="motdepasse-1") == b"[preset=Martin]\nfast=14\n"
    assert crypt.decrypt(blob, key_b64=cfg.backup_key) == b"[preset=Martin]\nfast=14\n"
    import pytest
    with pytest.raises(ValueError, match="incorrect"):
        crypt.decrypt(blob, password="mauvais")
    with pytest.raises(ValueError, match="requis"):
        crypt.decrypt(blob)
    assert "écriture" in ghbackup.push(cfg, "x", lambda m, u, b=None: (403, {}))


def test_depot_de_sauvegarde_trouve_ou_cree_avec_noms_de_secours(cfg):
    """Bot 4.2.0 : dépôt privé existant repris ; nom pris par un dépôt public ou refusé (422) → nom suivant ; jeton
    limité → message clair ; jamais d'exception."""
    cfg.backup_token = "tok"
    repos = {"moi/scalpx-sauvegarde": {"private": False}}               # nom déjà pris par un dépôt PUBLIC
    refused = {"scalpx-sauvegarde-2"}                                   # nom refusé par GitHub (récemment supprimé…)

    def http(method, url, body=None):
        if url.endswith("/user"):
            return 200, {"login": "moi"}
        if method == "GET":
            name = url.split("/repos/", 1)[1]
            return (200, repos[name]) if name in repos else (404, {})
        if body["name"] in refused:
            return 422, {}
        repos[f"moi/{body['name']}"] = {"private": body["private"]}
        return 201, {}
    assert ghbackup.ensure_repo(cfg, http) == ("moi/scalpx-sauvegarde-3", "")
    assert repos["moi/scalpx-sauvegarde-3"] == {"private": True}
    assert ghbackup.ensure_repo(cfg, http) == ("moi/scalpx-sauvegarde-3", "")   # repris tel quel ensuite
    limited = lambda m, u, b=None: (200, {"login": "moi"}) if u.endswith("/user") else ((404, {}) if m == "GET" else (403, {}))  # noqa: E731
    name, err = ghbackup.ensure_repo(cfg, limited)
    assert name == "" and "crée toi-même" in err
    assert ghbackup.ensure_repo(cfg, lambda m, u, b=None: (401, {}))[1].startswith("jeton")
    def boom(m, u, b=None):
        raise OSError("réseau coupé")
    assert "injoignable" in ghbackup.ensure_repo(cfg, boom)[1]
