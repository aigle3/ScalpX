"""Mise à jour automatique depuis le paquet OneDrive."""
import os
import time
from pathlib import Path

import pytest

from core import update as u


def make_pkg(tmp_path, cfg, bot_root):
    src = tmp_path / "release"
    (src / "bot" / "bot").mkdir(parents=True)
    (src / "bot" / "main.py").write_text("print('v2')")
    (src / "bot" / "bot" / "x.py").write_text("X = 2")
    (src / "bot" / "config.toml").write_text("[paths]\n")
    (src / "bot" / ".env").write_text("TELEGRAM_TOKEN=secret")
    (src / "bot" / "data").mkdir()
    (src / "bot" / "data" / "scalpx.db").write_text("db")
    ex = src / "ea" / "MQL5" / "Experts"
    ex.mkdir(parents=True)
    (src / "ea" / "MQL5" / "Presets").mkdir(parents=True)
    (ex / "SX_Core.mqh").write_bytes(b'\xef\xbb\xbf#define SX_VERSION   "5.3"\r\nint a;\r\n')
    (src / "ea" / "MQL5" / "Presets" / "scalpx_v5.set").write_text("A=1\nB=2\n")
    (src / "ea" / "version.txt").write_text("5.3")
    old = time.time() - 300
    for p in src.rglob("*"):
        if p.is_file():
            os.utime(p, (old, old))
    cfg.update.source = str(src)
    # installation actuelle
    (bot_root / "bot").mkdir(parents=True)
    (bot_root / "main.py").write_text("print('v2')")
    (bot_root / "bot" / "x.py").write_text("X = 1")
    (bot_root / "config.toml").write_text("[paths]\nbase='C:/perso'\n")
    (cfg.paths.base / "MQL5" / "Experts" / "SX_Core.mqh").write_bytes(b'#define SX_VERSION   "5.3"\nint a;\n')
    (cfg.paths.base / "version.txt").write_text("5.3")
    return src


def test_plan_ignore_secrets_donnees_et_lignes_de_version(tmp_path, cfg):
    bot_root = tmp_path / "botinst"
    src = make_pkg(tmp_path, cfg, bot_root)
    plan = u.plan_update(cfg, bot_root)
    assert plan.bot_files == ["bot/x.py"]                  # ni .env, ni config.toml, ni data\
    assert plan.ea_files == []                             # SX_Core identique hors BOM/CRLF ; .set identique
    # seule la ligne de version diffère → pas une mise à jour
    (cfg.paths.base / "MQL5" / "Experts" / "SX_Core.mqh").write_bytes(b'#define SX_VERSION   "5.4"\nint a;\n')
    assert u.plan_update(cfg, bot_root).ea_files == []


def test_installation_ea_puis_version_txt_en_dernier(tmp_path, cfg):
    bot_root = tmp_path / "botinst"
    src = make_pkg(tmp_path, cfg, bot_root)
    (src / "ea" / "MQL5" / "Experts" / "SX_Core.mqh").write_bytes(b'#define SX_VERSION   "5.3"\nint b;\n')
    old = time.time() - 300
    os.utime(src / "ea" / "MQL5" / "Experts" / "SX_Core.mqh", (old, old))
    plan = u.plan_update(cfg, bot_root)
    assert plan.ea_files == ["MQL5/Experts/SX_Core.mqh"]
    new = u.install_ea(cfg, plan)
    assert new == "5.4"                                    # paquet 5.3 = BASE 5.3 → version incrémentée pour recompiler
    base = cfg.paths.base
    assert (base / "version.txt").read_text() == "5.4"
    assert (base / "version.txt").stat().st_mtime > (base / "MQL5" / "Experts" / "SX_Core.mqh").stat().st_mtime
    from mt5 import build as b
    (base / "MQL5" / "Experts" / "ScalpX_v5.mq5").write_text("x")
    (base / "MetaEditor64.exe").write_bytes(b"MZ")
    t = time.time() - 30
    os.utime(base / "version.txt", (t, t))
    os.utime(base / "MQL5" / "Experts" / "SX_Core.mqh", (t - 5, t - 5))
    from storage.db import Database
    db = Database(tmp_path / "u.db")
    assert b.pending_build(cfg, db).version == "5.4"       # la compilation automatique prend le relais
    db.close()


def test_installation_bot_garde_config_et_secrets(tmp_path, cfg):
    bot_root = tmp_path / "botinst"
    make_pkg(tmp_path, cfg, bot_root)
    plan = u.plan_update(cfg, bot_root)
    u.install_bot(plan, bot_root)
    assert (bot_root / "bot" / "x.py").read_text() == "X = 2"
    assert "C:/perso" in (bot_root / "config.toml").read_text()
    assert not (bot_root / ".env").exists()
    assert [x.name for x in (bot_root / "data").iterdir()] == ["backup_bot.zip"]       # seule la copie de secours
    assert u.plan_update(cfg, bot_root).empty


def test_bump():
    assert u.bump("5.3") == "5.4" and u.bump("5.9") == "5.10" and u.bump("5") == "6"


def _zip(files: dict[str, bytes]) -> bytes:
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in files.items():
            z.writestr("aigle3-scalpx-abc1234/" + k, v)
    return buf.getvalue()


def test_github_telecharge_le_paquet_une_fois_par_commit(tmp_path, cfg):
    import json
    from core import github as gh
    cfg.update.github, cfg.update.source, cfg.github_token = "aigle3/scalpx", str(tmp_path / "release"), "github_pat_x"
    state = {"sha": "s1", "zips": 0}

    def http(url, headers):
        assert headers["Authorization"] == "Bearer github_pat_x"
        if url.endswith("/repos/aigle3/scalpx/commits/stable"):           # seulement les publications validées
            return 200, json.dumps({"sha": state["sha"], "commit": {"message": "Ajout pause news\n\ndétails", "committer": {"date": "2026-10-28"}}}).encode()
        if "/zipball/" in url:
            state["zips"] += 1
            return 200, _zip({"bot/main.py": b"print(1)", "ea/version.txt": b"5.4", "../evil.txt": b"x"})
        return 404, b""

    gh._SEEN.update(sha="", since=0.0)
    r = gh.fetch(cfg, http, now=1000.0)
    assert r.ok and r.waiting and not r.changed and state["zips"] == 0      # nouveau commit : attente de stabilité
    r = gh.fetch(cfg, http, now=1000.0 + 1 * 60)
    assert r.waiting and state["zips"] == 0
    r = gh.fetch(cfg, http, now=1000.0 + 2 * 60)                          # 2 minutes sans autre publication
    assert r.ok and r.changed and (tmp_path / "release" / "bot" / "main.py").read_text() == "print(1)"
    assert not (tmp_path / "evil.txt").exists()
    assert u.commit_info(tmp_path / "release")["message"].startswith("Ajout pause news")
    assert gh.fetch(cfg, http).changed is False and state["zips"] == 1         # même commit : rien retéléchargé
    state["sha"] = "s2"
    assert gh.fetch(cfg, http).waiting                                          # publication suivante : nouvelle attente
    assert gh.fetch(cfg, http, force=True).changed and state["zips"] == 2      # bouton « Mises à jour » : immédiat


def test_github_jeton_refuse(tmp_path, cfg):
    from core import github as gh
    cfg.update.github, cfg.update.source = "aigle3/scalpx", str(tmp_path / "release")
    r = gh.fetch(cfg, lambda url, h: (401, b""))
    assert not r.ok and "jeton" in r.error


def test_fichiers_retires_du_depot_supprimes(tmp_path, cfg):
    bot_root = tmp_path / "botinst"
    make_pkg(tmp_path, cfg, bot_root)
    (bot_root / "scalpx_bot_task.xml").write_text("<old/>")
    (bot_root / "bot" / "ancien.py").write_text("x = 1")
    (bot_root / "data").mkdir()
    (bot_root / "data" / "notes.txt").write_text("à garder")
    plan = u.plan_update(cfg, bot_root)
    assert sorted(plan.bot_remove) == ["bot/ancien.py", "scalpx_bot_task.xml"]
    u.install_bot(plan, bot_root)
    assert not (bot_root / "bot" / "ancien.py").exists() and (bot_root / "data" / "notes.txt").exists()
    assert (bot_root / "config.toml").exists() and u.plan_update(cfg, bot_root).empty


def test_detection_processus_reels():
    import psutil
    from mt5.procs import PsutilProcesses
    p = PsutilProcesses()
    assert p.is_running(psutil.Process().exe()) and not p.is_running("/nulle/part/terminal64.exe")


def test_ping_de_surveillance(cfg):
    from core import monitor
    calls = []
    http = lambda url, h: (calls.append(url) or (200, b"OK"))
    assert monitor.ping(cfg, http) is False and calls == []                        # non configuré : rien envoyé
    cfg.healthcheck_url = "https://hc-ping.com/abc"
    assert monitor.ping(cfg, http) and monitor.ping(cfg, http, fail=True)
    assert calls == ["https://hc-ping.com/abc", "https://hc-ping.com/abc/fail"]
    assert monitor.ping(cfg, lambda u, h: (404, b"")) is False


def test_copie_de_secours_et_retour_arriere(tmp_path, cfg):
    bot_root = tmp_path / "botinst"
    make_pkg(tmp_path, cfg, bot_root)
    (bot_root / "version.txt").write_text("1.2.0")
    (bot_root / "data").mkdir()
    (bot_root / "data" / "scalpx.db").write_text("db")
    (cfg.update.source and Path(cfg.update.source) / "bot" / "version.txt").write_text("1.3.0")
    (Path(cfg.update.source) / "bot" / "bot" / "nouveau.py").write_text("N = 1")
    plan = u.plan_update(cfg, bot_root)
    u.install_bot(plan, bot_root)                                   # copie de secours faite avant d'installer
    assert u.backup_version(bot_root) == "1.2.0" and (bot_root / "version.txt").read_text() == "1.3.0"
    assert (bot_root / "bot" / "nouveau.py").exists()
    assert u.restore_bot(bot_root) == "1.2.0"
    assert (bot_root / "version.txt").read_text() == "1.2.0" and (bot_root / "bot" / "x.py").read_text() == "X = 1"
    assert not (bot_root / "bot" / "nouveau.py").exists()            # fichier de la version récente retiré
    assert (bot_root / "data" / "scalpx.db").read_text() == "db" and "C:/perso" in (bot_root / "config.toml").read_text()


def test_sauvegarde_quotidienne_de_l_historique(tmp_path, cfg):
    import sqlite3
    from core import backup
    from storage.db import Database
    cfg.backup.dir, cfg.backup.keep = str(tmp_path / "sauv"), 3
    db = Database(tmp_path / "scalpx.db")
    db.meta_set("cle", "valeur")
    for i in range(5):
        dest = backup.run_backup(cfg, db, now=1_790_000_000 + i * 86400)
    files = sorted((tmp_path / "sauv").glob("scalpx_*.db"))
    assert len(files) == 3 and backup.last_backup(cfg) == dest == files[-1]
    cx = sqlite3.connect(str(dest))
    assert cx.execute("SELECT value FROM meta WHERE key='cle'").fetchone()[0] == "valeur"
    cx.close()
    db.close()


def test_expiration_du_jeton_github(tmp_path, cfg):
    import json
    from core import github as gh
    cfg.update.github, cfg.update.source = "aigle3/scalpx", str(tmp_path / "release")
    hdr = {"github-authentication-token-expiration": "2027-10-28 12:00:00 UTC"}
    http = lambda url, h: (200, json.dumps({"sha": "", "commit": {"message": ""}}).encode(), hdr) if "/commits/" in url else (404, b"", {})
    r = gh.fetch(cfg, http)
    assert r.token_expires == "2027-10-28"
    import datetime as dt
    now = dt.datetime(2027, 10, 18, 12).timestamp()
    assert gh.days_left("2027-10-28", now) == 10 and gh.days_left("n/a") is None


def test_github_plusieurs_publications_proposees_une_par_une_dans_l_ordre(tmp_path, cfg):
    import json
    from core import github as gh
    cfg.update.github, cfg.update.source = "aigle3/scalpx", str(tmp_path / "release")
    chain = ["c1", "c2", "c3", "c4"]                                       # c1 installé ; c2, c3, c4 publiés ensuite
    got = []

    def commit(s):
        return {"sha": s, "commit": {"message": f"Bot {s}", "committer": {"date": "2026-10-29"}}}

    def http(url, headers):
        if url.endswith("/commits/stable"):
            return 200, json.dumps(commit("c4")).encode()
        if "/compare/" in url:
            base = url.split("/compare/")[1].split("...")[0]
            after = chain[chain.index(base) + 1:]
            return 200, json.dumps({"status": "ahead", "commits": [commit(s) for s in after]}).encode()
        if "/zipball/" in url:
            got.append(url.rsplit("/", 1)[1])
            return 200, _zip({"bot/main.py": b"print(1)"})
        return 404, b""

    gh._SEEN.update(sha="", since=0.0)
    r = gh.fetch(cfg, http, force=True, installed="c1")
    assert r.changed and r.sha == "c2" and r.remaining == 2 and got == ["c2"]
    assert u.commit_info(tmp_path / "release")["remaining"] == 2
    r = gh.fetch(cfg, http, force=True, installed="c1")                    # c2 pas encore installé : rien de plus
    assert not r.changed and got == ["c2"]
    r = gh.fetch(cfg, http, force=True, installed="c2")                    # c2 installé → c3
    assert r.sha == "c3" and r.remaining == 1 and got == ["c2", "c3"]
    r = gh.fetch(cfg, http, force=True, installed="c3")
    assert r.sha == "c4" and r.remaining == 0
    assert not gh.fetch(cfg, http, installed="c4").changed                 # à jour


def _set(lines):
    return b"\xff\xfe" + "\r\n".join(lines).encode("utf-16-le")


def test_preset_actif_ecrit_dans_le_set_de_base_sans_proposer_de_mise_a_jour_en_boucle(tmp_path, cfg):
    from core import presets as ps
    bot_root = tmp_path / "inst"
    src = make_pkg(tmp_path, cfg, bot_root)
    pkg_set = src / "ea" / "MQL5" / "Presets" / "scalpx_v5.set"
    pkg_set.write_bytes(_set(["; commentaire", "TradingTimeframe=5", "LotSize=0.01", "UseDynamicGrid=true", "MagicNumber=123456", ""]))
    base_set = cfg.paths.base / "MQL5" / "Presets" / "scalpx_v5.set"
    base_set.parent.mkdir(parents=True, exist_ok=True)
    store = ps.PresetStore(cfg.paths.data / "presets.json")
    store.put("Martin", {"tf": "2", "lot": "0.02", "dyngrid": "0", "news_min": "20"})
    store.set_active("Martin")
    plan = u.plan_update(cfg, bot_root)
    assert "MQL5/Presets/scalpx_v5.set" in plan.ea_files
    u.install_ea(cfg, plan)
    txt = base_set.read_bytes()[2:].decode("utf-16-le")
    assert "TradingTimeframe=15" in txt and "LotSize=0.02" in txt and "UseDynamicGrid=false" in txt
    assert "; commentaire" in txt and "MagicNumber=123456" in txt and "news_min" not in txt
    assert base_set.read_bytes()[:2] == b"\xff\xfe"
    assert "MQL5/Presets/scalpx_v5.set" not in u.plan_update(cfg, bot_root).ea_files     # pas de nouvelle proposition


def test_overlay_set_ajoute_un_input_absent_et_garde_l_optimisation():
    from core import presets as ps
    out = ps.overlay_set(_set(["FastEMA=10||10||1||30||N", ""]), {"fast": "12", "slow": "25"})
    txt = out[2:].decode("utf-16-le")
    assert "FastEMA=12||10||1||30||N" in txt and "SlowEMA=25" in txt


def test_presets_reglages_supprimes_de_l_ea_ignores(tmp_path):
    import json
    from core import presets as ps
    f = tmp_path / "presets.json"
    f.write_text(json.dumps({"active": "Martin", "presets": {"Martin": {"lot": "0.01", "autoresume": "20"}}}))
    st = ps.PresetStore(f)
    assert st.presets["Martin"] == {"lot": "0.01"} and st.active == "Martin"     # « autoresume » (EA < 6.0.0) retiré
    assert ps.differences({"lot": "0.01", "autoresume": "20"}, {"lot": "0.01"}) == {}


def test_creneaux_masques_et_input_du_set():
    """Créneaux : 4 masques (minutes 00/15/30/45) ↔ minutes depuis minuit ↔ input ObjectifAutoHeures du .set."""
    from core import params as cat, presets as ps
    m = cat.masks_of([180, 360, 570, 765, 1230, 61])                 # 61 = 01:01 : minutes invalides, ignoré
    assert m == [1 << 3 | 1 << 6, 0, 1 << 9 | 1 << 20, 1 << 12]
    assert cat.slots_of(m) == [180, 360, 570, 765, 1230]
    assert cat.slots_text(m) == "03:00, 06:00, 09:30, 12:45, 20:30" and cat.slots_text([0, 0, 0, 0]) == "aucun"
    assert cat.mask_text("sl_m30", str(1 << 9)) == "09:30" and cat.mask_text("lot", "0.01") == "0.01"
    vals = {"sl_m00": str(m[0]), "sl_m15": "0", "sl_m30": str(m[2]), "sl_m45": str(m[3]), "sl_win": "10", "seuil_arret": "600"}
    assert ps.slots_input(vals) == "3,6,9:30,12:45,20:30" and ps.slots_input({"lot": "0.01"}) is None
    txt = ps.overlay_set(_set(["ObjectifAutoHeures=3,6,9,12,17,20", "ObjectifAutoFenetreMin=5", ""]), vals)[2:].decode("utf-16-le")
    assert "ObjectifAutoHeures=3,6,9:30,12:45,20:30" in txt and "ObjectifAutoFenetreMin=10" in txt and "SeuilArret=600" in txt
    assert "sl_m00" not in txt
    # valeurs par défaut de l'EA (3,6,9,12,17,20) : le masque :00 vaut 1184328
    assert cat.masks_of([h * 60 for h in (3, 6, 9, 12, 17, 20)])[0] == 1184328
    p = ps.BY_KEY["sl_m00"]
    assert ps.human(p, "1184328") == "03:00, 06:00, 09:00, 12:00, 17:00, 20:00" and ps.human(ps.BY_KEY["sl_win"], "10") == "10 min"


def test_detection_processus_filtre_par_nom(monkeypatch):
    """Windows : seul le chemin des processus du bon nom est lu (parcours rapide), même résultat."""
    from mt5 import procs as pr

    class P:
        def __init__(self, name, exe):
            self.info, self._exe, self.read = {"name": name}, exe, 0

        def exe(self):
            self.read += 1
            return self._exe

    ps = [P("svchost.exe", "/w/svchost.exe"), P("terminal64.exe", "/mt5/a/terminal64.exe"), P(None, "/mt5/b/terminal64.exe")]
    monkeypatch.setattr(pr, "FAST_NAMES", True)
    monkeypatch.setattr(pr.psutil, "process_iter", lambda attrs=None: iter(ps))
    p = pr.PsutilProcesses()
    assert p.is_running("/mt5/a/terminal64.exe") and p.is_running("/mt5/b/terminal64.exe")
    assert not p.is_running("/mt5/c/terminal64.exe")
    assert ps[0].read == 0 and ps[1].read >= 1                        # svchost : chemin jamais lu
    assert [x._exe for x in p.find("/mt5/a/terminal64.exe")] == ["/mt5/a/terminal64.exe"]


def test_paquet_github_chemins_hors_dossier_refuses(tmp_path):
    import io
    import zipfile
    from core import github as gh
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("o-r-1/bot/main.py", "ok")
        z.writestr("o-r-1/../release.new2/evil.txt", "x")          # voisin dont le nom commence pareil
        z.writestr("o-r-1/../../evil.txt", "x")
    dest = tmp_path / "release"
    gh.extract_zip(buf.getvalue(), dest, {"sha": "1"})
    assert (dest / "bot" / "main.py").read_text() == "ok"
    assert not (tmp_path / "release.new2").exists() and not (tmp_path.parent / "evil.txt").exists()


def test_relance_attend_la_fin_de_l_ancien_bot():
    """La tâche planifiée n'accepte qu'une instance : la relance attend la fin de CE processus (plus de délai fixe de 20 s)."""
    from core import update as u
    sc = u.restart_script(4321)
    assert sc.startswith("Wait-Process -Id 4321 -Timeout 300") and f"schtasks /run /tn '{u.TASK}'" in sc
    assert sc.index("Wait-Process") < sc.index("schtasks /run") and "for ($i = 0; $i -lt 5" in sc
