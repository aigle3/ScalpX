"""Bot 3.21.0 : ☁️ sauvegarde complète (base + presets) et restauration au démarrage."""
from __future__ import annotations

import base64

import pytest

from core import fullbackup as fb
from storage.db import Database, Instance


def _db(path, folder):
    db = Database(path)
    db.add_instance(Instance("1_X", 1, "X", "", str(folder), 0, "", "DEMARRE", None, "EUR"))
    db.add_balance("1_X", 321.0, 321.0, 0.0, 2, "ACTIF")
    db.meta_set("opt_day", "3")
    return db


def test_archive_complete_puis_restauration(tmp_path):
    src = tmp_path / "ancien"
    src.mkdir()
    (src / "presets.json").write_text('{"presets": {"Martin": {"fast": "12"}}}')
    db = _db(src / "scalpx.db", tmp_path / "absent")
    data = fb.build(db, src, "3.21.0")
    man = fb.check(data)
    assert man["comptes"] == 1 and man["version_bot"] == "3.21.0"
    # nouveau serveur : base vide + presets différents
    new = tmp_path / "neuf"
    new.mkdir()
    Database(new / "scalpx.db").meta_set("x", "1")
    (new / "presets.json").write_text("{}")
    fb.stage(new, data)
    note = fb.apply_pending(new)
    assert "1 compte" in note and not (new / fb.PENDING).exists()
    assert "Martin" in (new / "presets.json").read_text() and list(new.glob("presets.json.avant_restauration_*"))
    db2 = Database(new / "scalpx.db")
    assert db2.meta_get("opt_day") == "3" and db2.balances("1_X", 0)
    assert fb.archive_missing(db2, tmp_path / "instances") == ["1_X"] and db2.list() == []
    assert fb.apply_pending(new) == ""                                     # rien à refaire


def test_archive_refusee(tmp_path):
    with pytest.raises(ValueError):
        fb.check(b"pas une archive")
    (tmp_path / fb.PENDING).write_bytes(b"abime")
    assert fb.apply_pending(tmp_path).startswith("ÉCHEC") and (tmp_path / f"{fb.PENDING}.refusee").exists()


def test_envoi_et_lecture_github(tmp_path, cfg):
    cfg.backup_token, cfg.update.github = "tok", "aigle3/ScalpX"
    db = _db(tmp_path / "s.db", tmp_path)
    data = fb.build(db, tmp_path)
    store = {}

    def http(method, url, body=None):
        assert url.endswith("/repos/aigle3/scalpx-sauvegarde/contents/sauvegarde.tar.gz")
        if method == "GET":
            return (200, {"sha": "s", "content": base64.b64encode(store["d"]).decode()}) if "d" in store else (404, {})
        store["d"] = base64.b64decode(body["content"])
        return 201, {}
    assert fb.push(cfg, data, http) == "" and store["d"] == data
    got, err = fb.fetch(cfg, http)
    assert err == "" and got == data
