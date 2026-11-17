"""Bot 3.18.0 : écran 🖥 Serveur (Linux) : état, démarrage auto, mise à jour root, redémarrage, désinstallation."""
from __future__ import annotations

from core import server as sv
from .render import ago, esc
from .ui import Keyboard


def _bar(pct: float) -> str:
    n = max(0, min(10, round(pct / 10)))
    return "▓" * n + "░" * (10 - n)


def server_screen(st: sv.Stats | None, auto: dict[str, str], sudo: bool, flash: str = "",
                  webview: bool = False) -> tuple[str, Keyboard]:
    lines = ["🖥 <b>Serveur</b>", ""]
    if st is None:
        lines.append("⏳ Mesure en cours…")
    else:
        lines += [
            f"CPU    {_bar(st.cpu)} <b>{st.cpu:.0f} %</b> ({st.ncpu} cœurs, charge {st.load[0]:.1f} / {st.load[1]:.1f} / {st.load[2]:.1f})",
            f"RAM    {_bar(st.ram_pct)} <b>{st.ram_pct:.0f} %</b> ({st.ram_used_gb:.1f} / {st.ram_total_gb:.1f} Go)"
            + (f" · swap {st.swap_pct:.0f} %" if st.swap_pct else ""),
            f"Disque {_bar(st.disk_pct)} <b>{st.disk_pct:.0f} %</b> ({st.disk_free_gb:.1f} Go libres)",
            f"Allumé depuis {ago(st.uptime_s)}" + (f" · {st.temp:.0f} °C" if st.temp else ""),
            "", "🔥 <b>Processeur</b>",
            *[f"   {p.cpu:5.1f} %  {esc(p.label)}" for p in st.top_cpu],
            "", "🧠 <b>Mémoire</b>",
            *[f"   {p.mem_mb:6.0f} Mo  {esc(p.label)}" for p in st.top_mem],
        ]
    lines.append("")
    bad = [u for u, v in auto.items() if v != "enabled"]
    lines.append("✅ Bot et écran virtuel relancés automatiquement au démarrage du VPS" if not bad else
                 "⚠️ Pas relancé au démarrage du VPS : " + esc(", ".join(bad)) + " (touche ✅ Démarrage auto)")
    if not sudo:
        lines.append("⚠️ Droits administrateur du bot absents : relance <code>sudo bash linux/install.sh</code> "
                     "(services, redémarrage et désinstallation sans effet d'ici là)")
    lines.append("🧩 Pages web de MT5 (WebView2) : " + ("<b>actives</b> (≈ 300 Mo de RAM par terminal)" if webview else
                 "<b>coupées</b> (RAM économisée ; trading et EA non concernés)") + " — pris en compte au prochain lancement de chaque terminal")
    kb: Keyboard = [
        [("🔃 Actualiser", "d:srv"), ("🧩 " + ("Couper" if webview else "Réactiver") + " WebView2", "d:wvw")],
        [("✅ Démarrage auto", "d:sva")] if bad else [],
        [("🔁 Redémarrer le VPS", "d:rbt"), ("♻️ Réinitialiser", "d:rz")],
        [("🧨 Tout désinstaller", "d:uni")],
        [("⬅ Système", "d:sys")],
    ]
    text = "\n".join(lines)
    return (f"{flash}\n\n{text}" if flash else text), [r for r in kb if r]
