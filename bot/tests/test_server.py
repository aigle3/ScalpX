"""Bot 3.18.0 : 🖥 gestion du serveur Linux, jeton de sauvegarde écrit dans .env."""
from __future__ import annotations

import subprocess

from core import envfile, server as sv


class R:
    def __init__(self, out="", code=0, err=""):
        self.stdout, self.returncode, self.stderr = out, code, err


def test_demarrage_auto_limite_aux_services_scalpx():
    calls = []
    run = lambda a, timeout=30: calls.append(a) or R()                                      # noqa: E731
    assert sv.enable_own(run) == (True, "fait")
    assert calls == [["sudo", "-n", "/usr/bin/systemctl", "enable", "--now", "scalpx-bot.service"],
                     ["sudo", "-n", "/usr/bin/systemctl", "enable", "--now", "scalpx-xvfb.service"]]
    ok, msg = sv.enable_own(lambda a, timeout=30: R(code=1, err="sudo: a password is required"))
    assert not ok and "install.sh" in msg


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


def test_installation_systeme_a_mettre_a_jour(tmp_path):
    """Bot 3.19.0 : empreinte du dossier linux/ du paquet ≠ celle écrite par install.sh → 🔔 🔧."""
    pkg = tmp_path / "release"
    (pkg / "linux").mkdir(parents=True)
    (pkg / "linux" / "install.sh").write_text("echo 1\n")
    (pkg / "linux" / "LISEZMOI.md").write_text("doc")
    stamp = tmp_path / "installation"
    want = sv.install_needed(pkg, stamp)
    assert want and len(want) == 16                                     # jamais installé : à faire
    stamp.write_text(want + "\n")
    assert sv.install_needed(pkg, stamp) == ""                          # à jour
    (pkg / "linux" / "LISEZMOI.md").write_text("doc modifiée")
    assert sv.install_needed(pkg, stamp) == ""                          # documentation seule : rien à faire
    (pkg / "linux" / "install.sh").write_text("echo 2\n")
    assert sv.install_needed(pkg, stamp) not in ("", want)
    assert sv.install_needed(None, stamp) == ""


def test_empreinte_identique_a_celle_de_install_sh():
    """Même calcul en Python et dans install.sh (sinon la cloche ne s'éteindrait jamais)."""
    import os
    from pathlib import Path
    linux = Path(__file__).resolve().parents[2] / "linux"
    sh = subprocess.run(["bash", "-c", "cd \"$1\" && for f in $(ls -1 | grep -v '\\.md$' | LC_ALL=C sort); do [ -f \"$f\" ] && "
                         "{ printf '%s\\n' \"$f\"; cat \"$f\"; }; done | sha256sum | cut -c1-16", "x", str(linux)],
                        capture_output=True, text=True).stdout.strip()
    assert sh == sv.linux_digest(linux)
    assert "sha256sum | cut -c1-16 > /etc/scalpx/installation" in (linux / "install.sh").read_text()
