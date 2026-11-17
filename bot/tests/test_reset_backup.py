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
    assert ghbackup.repo(cfg) == ""                                     # pas de jeton : désactivée
    cfg.backup_token, cfg.update.github = "tok", "aigle3/ScalpX"
    assert ghbackup.repo(cfg) == "aigle3/scalpx-sauvegarde"
    cfg.backup.github = "moi/autre"
    assert ghbackup.repo(cfg) == "moi/autre"
    store = {}
    calls = []

    def http(method, url, body=None):
        calls.append((method, url, body))
        if method == "GET":
            return (200, {"sha": "s1", "content": base64.b64encode(store["t"].encode()).decode()}) if "t" in store else (404, {})
        store["t"] = base64.b64decode(body["content"]).decode()
        return 201, {}

    assert ghbackup.push(cfg, "[preset=Martin]\nfast=12\n", http) == ""
    assert "sha" not in calls[-1][2] and calls[-1][1].endswith("/repos/moi/autre/contents/presets.txt")
    assert ghbackup.push(cfg, "[preset=Martin]\nfast=13\n", http) == ""
    assert calls[-1][2]["sha"] == "s1"                                  # mise à jour du fichier existant
    assert ghbackup.fetch(cfg, http) == ("[preset=Martin]\nfast=13\n", "")
    assert "écriture" in ghbackup.push(cfg, "x", lambda m, u, b=None: (403, {}))
