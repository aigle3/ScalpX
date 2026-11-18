"""Bot 4.7.0 : courtiers (liste de serveurs MT5 par courtier)."""
from pathlib import Path
from types import SimpleNamespace

from mt5 import brokers


def test_cle_du_courtier():
    assert brokers.key_of("RoboForex-Pro") == "roboforex"
    assert brokers.key_of("RoboForex-ECN") == "roboforex"
    assert brokers.key_of("Exness-MT5Real8") == "exness"
    assert brokers.key_of("ICMarketsSC-Demo") == "icmarketssc"
    assert brokers.key_of("Pepperstone Edge01") == "pepperstone"
    assert brokers.key_of("") == ""


def test_courtier_migration_et_copie_dans_le_compte(tmp_path):
    base = tmp_path / "BASE"
    (base / "Config").mkdir(parents=True)
    (base / "Config" / "servers.dat").write_bytes(b"ROBO")
    (base / "Config" / "servers.dat.metaquotes").write_bytes(b"MQ")
    assert brokers.known(base) == [] and not brokers.has(base, "RoboForex-Pro")
    assert brokers.migrate(base) == ["metaquotes"]                      # pas de marque RoboForex : liste non attribuée
    (base / brokers.RF_MARK).touch()
    assert brokers.migrate(base) == ["roboforex"] and brokers.known(base) == ["metaquotes", "roboforex"]
    inst = tmp_path / "inst"
    assert brokers.apply(base, "RoboForex-Pro", inst) and (inst / "Config" / "servers.dat").read_bytes() == b"ROBO"
    assert not brokers.apply(base, "Exness-MT5Real8", tmp_path / "x")
    assert "inconnu" in brokers.missing_message("Exness-MT5Real8")


def test_ajout_d_un_courtier_par_son_installeur(tmp_path):
    prefix = tmp_path / "wine"
    cfg = SimpleNamespace(paths=SimpleNamespace(base=tmp_path / "BASE"),
                          wine=SimpleNamespace(cmd="wine", options=lambda: SimpleNamespace(
                              prefix_path=lambda: prefix, prefix=str(prefix), display="", webview2=False, cmd="wine")))
    (prefix / "drive_c").mkdir(parents=True)
    seen = {}

    def runner(cmd, env):
        seen["cmd"] = cmd
        d = prefix / "drive_c" / brokers.TMP_DIR / "Config"
        d.mkdir(parents=True)
        (d / "servers.dat").write_bytes(b"EXNESS")
        return SimpleNamespace(terminate=lambda: None)
    import mt5.wine as w
    old = w.env
    w.env = lambda opts: {}
    try:
        ok, msg = brokers.add_from_installer(cfg, "https://exemple/exness5setup.exe", "Exness-MT5Real8", runner=runner,
                                             downloader=lambda url, dest: dest.write_bytes(b"MZ..."), sleep=lambda s: None)
        assert ok and "exness" in msg and seen["cmd"][2:] == ["/auto", f"/path:C:\\{brokers.TMP_DIR}"]
        assert (cfg.paths.base / "Config" / "brokers" / "exness.dat").read_bytes() == b"EXNESS"
        assert not (prefix / "drive_c" / brokers.TMP_DIR).exists()          # dossier à part supprimé
        ok, msg = brokers.add_from_installer(cfg, "https://x", "Autre-Live", runner=lambda c, e: SimpleNamespace(terminate=lambda: None),
                                             downloader=lambda url, dest: dest.write_bytes(b"MZ"), timeout=10,
                                             sleep=lambda s: None, clock=iter(range(0, 1000, 5)).__next__)
        assert not ok and "non obtenue" in msg
    finally:
        w.env = old


def test_lien_qui_n_est_pas_un_installeur(tmp_path):
    class R:
        def __init__(self, data): self.data = data
        def read(self, n): return self.data
        def __enter__(self): return self
        def __exit__(self, *a): return False
    import pytest
    with pytest.raises(ValueError, match="programme Windows"):
        brokers._download("https://exemple/page.html", tmp_path / "s.exe", opener=lambda req, timeout: R(b"<html>"))
    with pytest.raises(ValueError, match="https"):
        brokers._download("http://exemple", tmp_path / "s.exe")


def test_creation_refusee_pour_un_courtier_inconnu(tmp_path, monkeypatch):
    from mt5 import base as basemod, prepare
    from mt5.creator import InstanceCreator
    monkeypatch.setattr(prepare, "is_ready", lambda base, exe: True)
    monkeypatch.setattr(basemod, "check_base", lambda cfg: [])
    monkeypatch.setattr(basemod, "folder_size_gb", lambda p: 0.0)
    monkeypatch.setattr(basemod, "free_disk_gb", lambda p: 100.0)
    cr = InstanceCreator.__new__(InstanceCreator)
    cr.cfg = SimpleNamespace(paths=SimpleNamespace(base=tmp_path, instances=tmp_path / "inst"),
                             mt5=SimpleNamespace(terminal_exe="t.exe"), alerts=SimpleNamespace(min_disk_gb=0))
    cr.db = SimpleNamespace(exists_active=lambda iid: False)
    assert "inconnu" in cr.precheck("12345", "Exness-MT5Real8")
    (tmp_path / "Config" / "brokers").mkdir(parents=True)
    (tmp_path / "Config" / "brokers" / "exness.dat").write_bytes(b"x")
    assert cr.precheck("12345", "Exness-MT5Real8") is None
