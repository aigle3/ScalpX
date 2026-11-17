"""Bot 3.18.0 : 🖥 gestion du serveur Linux, jeton de sauvegarde écrit dans .env."""
from __future__ import annotations

import subprocess

from core import envfile, server as sv


class R:
    def __init__(self, out="", code=0, err=""):
        self.stdout, self.returncode, self.stderr = out, code, err


def test_services_lus_tries_par_memoire():
    calls = []

    def run(args, timeout=30):
        calls.append(args)
        if "list-units" in args:
            return R("ssh.service loaded active running OpenBSD Secure Shell server\n"
                     "snapd.service loaded active running Snap Daemon\n"
                     "cups.service loaded failed failed CUPS\n"
                     "apt-daily.service loaded inactive dead Daily apt\n")
        return R("Id=ssh.service\nMemoryCurrent=5242880\nUnitFileState=enabled\n\n"
                 "Id=snapd.service\nMemoryCurrent=52428800\nUnitFileState=enabled\n\n"
                 "Id=cups.service\nMemoryCurrent=[not set]\nUnitFileState=disabled\n")
    s = sv.services(run)
    assert [x.unit for x in s] == ["cups.service", "snapd.service", "ssh.service"]          # en échec d'abord
    assert s[1].mem_mb == 50 and s[2].enabled == "enabled" and s[1].desc == "Snap Daemon"


def test_actions_limitees_et_services_vitaux_proteges():
    calls = []
    run = lambda a, timeout=30: calls.append(a) or R()                                      # noqa: E731
    assert sv.action("ssh.service", "stop", run)[0] is False and not calls                 # SSH : jamais arrêté
    assert sv.action("scalpx-bot.service", "disable", run)[0] is False
    assert sv.action("x; rm -rf /", "stop", run)[0] is False
    assert sv.action("snapd.service", "format", run)[0] is False
    assert sv.action("snapd.service", "disable", run) == (True, "fait")
    assert calls[-1] == ["sudo", "-n", "/usr/bin/systemctl", "disable", "--now", "snapd.service"]
    assert sv.action("ssh.service", "start", run)[0]                                        # démarrer un vital : permis
    msg = sv.action("snapd.service", "stop", lambda a, timeout=30: R(code=1, err="sudo: a password is required"))[1]
    assert "install.sh" in msg


def test_libelles_des_processus_wine():
    assert sv.proc_label("main", ["/home/scalpx/mt5/instances/5203_Robo/terminal64.exe", "/portable"]) == "terminal64 · 5203_Robo"
    assert sv.proc_label("CrRendererMain", ["C:\\Program Files (x86)\\Microsoft\\EdgeWebView\\msedgewebview2.exe",
                                            "--user-data-dir=Z:\\home\\scalpx\\mt5\\instances\\5203_Robo\\temp"]) == "WebView2 · 5203_Robo"
    assert sv.proc_label("python3", ["/home/scalpx/scalpx/bot/.venv/bin/python", "main.py"]) == "bot Python"
    assert sv.proc_label("sshd", []) == "sshd"


def test_statistiques_du_serveur():
    st = sv.stats(sample_s=0.05)
    assert 0 <= st.cpu <= 100 and st.ram_total_gb > 0 and st.top_mem


def test_cle_ecrite_dans_env(tmp_path):
    p = tmp_path / ".env"
    p.write_bytes(b"\xef\xbb\xbfTELEGRAM_TOKEN=1:a\r\n# BACKUP_TOKEN=github_pat_...\r\nX=1\r\n")
    envfile.set_key(p, "BACKUP_TOKEN", "github_pat_abc")
    assert p.read_bytes() == b"\xef\xbb\xbfTELEGRAM_TOKEN=1:a\r\nBACKUP_TOKEN=github_pat_abc\r\nX=1\r\n"
    envfile.set_key(p, "BACKUP_TOKEN", "github_pat_def")
    assert p.read_bytes().count(b"BACKUP_TOKEN") == 1
    q = tmp_path / "neuf.env"
    envfile.set_key(q, "BACKUP_TOKEN", "t")
    assert q.read_text() == "BACKUP_TOKEN=t\n"


def test_mise_a_jour_root_et_desinstallation_par_sudo_sans_mot_de_passe():
    calls = []
    run = lambda a, timeout=30: calls.append(a) or R()                                      # noqa: E731
    assert sv.root_update(run)[0] and calls[-1] == ["sudo", "-n", "/usr/local/sbin/scalpx-root-update"]
    assert sv.uninstall(run)[0] and calls[-1] == ["sudo", "-n", "/usr/local/sbin/scalpx-uninstall"]
    assert sv.reboot(run)[0] and calls[-1] == ["sudo", "-n", "/usr/bin/systemctl", "reboot"]
