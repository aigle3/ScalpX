"""Fenêtre des terminaux MT5 : à chaque ouverture, panneaux masqués, graphique agrandi, puis fenêtre réduite.

Réduire MT5 évite le dessin des graphiques (CPU, mémoire graphique) ; l'EA continue de trader normalement.
Panneaux (observation du marché, navigateur, boîte à outils, fenêtre de données…) : MT5 est une application MFC ;
ses panneaux sont des barres ancrées à gauche, à droite ou en bas de la fenêtre (identifiants MFC standard des zones
d'ancrage). Chacun est masqué par sa commande (WM_COMMAND avec l'identifiant du panneau), sinon masqué directement.
MT5 retient l'état à sa fermeture. Tout passe par l'API Windows (ctypes) ; sur un autre système, rien n'est fait.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol

log = logging.getLogger(__name__)

LAST: dict[str, tuple[float, dict]] = {}              # dernier rangement par terminal (affiché dans 🛠 Système)


@dataclass
class WindowOptions:
    minimize: bool = True
    hide_panels: bool = True
    maximize_chart: bool = True
    settle_s: float = 10.0                            # attente après l'apparition de la fenêtre (graphique et EA chargés)
    timeout_s: float = 120.0


class WinAPI(Protocol):
    def main_window(self, pids: set[int]) -> int | None: ...
    def panels(self, hwnd: int) -> list[tuple[int, int, str]]: ...           # panneaux visibles : (fenêtre, id, titre)
    def hide_panel(self, hwnd: int, panel: int, ctrl_id: int) -> bool: ...
    def maximize_chart(self, hwnd: int) -> bool: ...
    def minimize(self, hwnd: int) -> None: ...


def arrange(api: WinAPI, pids: Callable[[], set[int]], opts: WindowOptions,
            sleep: Callable[[float], None] = time.sleep, wait_window: bool = True) -> dict:
    """Range la fenêtre du terminal (processus `pids`). Renvoie ce qui a été fait (pour le journal et les tests)."""
    done: dict = {"window": False, "hidden": [], "chart": False, "minimized": False}
    end = time.monotonic() + (opts.timeout_s if wait_window else 0)
    hwnd = api.main_window(pids())
    while hwnd is None and time.monotonic() < end:
        sleep(2.0)
        hwnd = api.main_window(pids())
    if hwnd is None:
        return done
    done["window"] = True
    if wait_window and opts.settle_s:
        sleep(opts.settle_s)
    if opts.hide_panels:
        for panel, ctrl_id, title in api.panels(hwnd):
            if api.hide_panel(hwnd, panel, ctrl_id):
                done["hidden"].append(title or str(ctrl_id))
    if opts.maximize_chart:
        done["chart"] = api.maximize_chart(hwnd)
    if opts.minimize:
        api.minimize(hwnd)
        done["minimized"] = True
    return done


def arrange_in_background(api: WinAPI | None, pids: Callable[[], set[int]], opts: WindowOptions, name: str) -> None:
    """Lance arrange() dans un fil séparé (le lancement du terminal ne doit jamais attendre)."""
    if api is None:
        return

    def run() -> None:
        try:
            res = arrange(api, pids, opts)
            LAST[name] = (time.time(), res)
            log.info("fenêtre %s : %s", name, res)
        except Exception as exc:                                   # jamais bloquant pour le bot
            log.warning("fenêtre %s : rangement impossible (%s)", name, exc)

    threading.Thread(target=run, daemon=True, name=f"win-{name}").start()


class Win32(WinAPI):
    """Implémentation Windows (user32 via ctypes)."""

    WM_COMMAND, WM_MDIGETACTIVE, WM_MDIMAXIMIZE = 0x0111, 0x0229, 0x0225
    SW_SHOWMINNOACTIVE = 7

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes as w
        self.c, self.w = ctypes, w
        u = ctypes.WinDLL("user32", use_last_error=True)
        self.u = u
        self.EnumProc = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        u.EnumWindows.argtypes = [self.EnumProc, w.LPARAM]
        u.EnumChildWindows.argtypes = [w.HWND, self.EnumProc, w.LPARAM]
        u.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        u.GetWindowThreadProcessId.restype = w.DWORD
        u.IsWindowVisible.argtypes = [w.HWND]
        u.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
        u.GetParent.argtypes = [w.HWND]
        u.GetParent.restype = w.HWND
        u.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        u.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        u.SendMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        u.SendMessageW.restype = ctypes.c_ssize_t
        u.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
        u.GetDlgCtrlID.argtypes = [w.HWND]
        u.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        u.GetClientRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]

    def main_window(self, pids: set[int]) -> int | None:
        best: list = [None, 0]
        w, c, u = self.w, self.c, self.u

        def cb(hwnd, _):
            pid = w.DWORD()
            u.GetWindowThreadProcessId(hwnd, c.byref(pid))
            if pid.value in pids and u.IsWindowVisible(hwnd) and not u.GetParent(hwnd):
                r = w.RECT()
                u.GetWindowRect(hwnd, c.byref(r))
                area = max(0, r.right - r.left) * max(0, r.bottom - r.top)
                buf = c.create_unicode_buffer(64)
                u.GetClassNameW(hwnd, buf, 64)
                if buf.value.startswith("MetaQuotes::MetaTrader"):            # fenêtre principale de MT5 (pas un dialogue)
                    area += 10 ** 9
                if area > best[1]:
                    best[0], best[1] = hwnd, area
            return True

        u.EnumWindows(self.EnumProc(cb), 0)
        return best[0]

    DOCK_IDS = (0xE81C, 0xE81D, 0xE81E)               # AFX_IDW_DOCKBAR_LEFT / RIGHT / BOTTOM (panneaux, pas les barres d'outils)

    def _children(self, hwnd: int) -> list[int]:
        out: list[int] = []

        def cb(h, _):
            out.append(h)
            return True

        self.u.EnumChildWindows(hwnd, self.EnumProc(cb), 0)
        return out

    def _text(self, h: int) -> str:
        buf = self.c.create_unicode_buffer(128)
        self.u.GetWindowTextW(h, buf, 128)
        return buf.value

    def panels(self, hwnd: int) -> list[tuple[int, int, str]]:
        u = self.u
        out = []
        for h in self._children(hwnd):
            parent = u.GetParent(h)
            if parent and u.GetDlgCtrlID(parent) in self.DOCK_IDS and u.IsWindowVisible(h):
                out.append((h, int(u.GetDlgCtrlID(h)), self._text(h).split(":")[0].strip()))
        return out

    def hide_panel(self, hwnd: int, panel: int, ctrl_id: int) -> bool:
        u = self.u
        if ctrl_id:
            u.PostMessageW(hwnd, self.WM_COMMAND, ctrl_id, 0)          # commande du panneau (comme le menu Affichage)
            time.sleep(0.8)
        if u.IsWindowVisible(panel):                                    # sinon : masqué directement, mise en page refaite
            u.ShowWindow(panel, 0)
            r = self.w.RECT()
            u.GetClientRect(hwnd, self.c.byref(r))
            u.SendMessageW(hwnd, 0x0005, 0, ((r.bottom & 0xFFFF) << 16) | (r.right & 0xFFFF))   # WM_SIZE → MFC RecalcLayout
            time.sleep(0.3)
        return not u.IsWindowVisible(panel)

    def maximize_chart(self, hwnd: int) -> bool:
        u, c = self.u, self.c
        found: list = []

        def cb(h, _):
            buf = c.create_unicode_buffer(64)
            u.GetClassNameW(h, buf, 64)
            if buf.value == "MDIClient":
                found.append(h)
                return False
            return True

        u.EnumChildWindows(hwnd, self.EnumProc(cb), 0)
        if not found:
            return False
        active = u.SendMessageW(found[0], self.WM_MDIGETACTIVE, 0, 0)
        if not active:
            return False
        u.SendMessageW(found[0], self.WM_MDIMAXIMIZE, active, 0)
        return True

    def minimize(self, hwnd: int) -> None:
        self.u.ShowWindow(hwnd, self.SW_SHOWMINNOACTIVE)


def default_api() -> WinAPI | None:
    if os.name != "nt":
        return None
    try:
        return Win32()
    except Exception as exc:
        log.warning("API Windows indisponible : %s", exc)
        return None
