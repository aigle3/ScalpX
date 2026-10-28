"""Fenêtre des terminaux MT5 : à chaque ouverture, panneaux masqués, graphique agrandi, puis fenêtre réduite.

Réduire MT5 évite le dessin des graphiques (CPU, mémoire graphique) ; l'EA continue de trader normalement.
Les panneaux (boîte à outils Ctrl+T, navigateur Ctrl+N, observation du marché Ctrl+M) sont masqués par la commande
de menu correspondante, retrouvée par son raccourci (indépendant de la langue) et seulement si elle est cochée.
Tout est fait par l'API Windows (ctypes) ; sur un autre système, rien n'est fait.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol

log = logging.getLogger(__name__)

PANEL_HOTKEYS = ("ctrl+t", "ctrl+n", "ctrl+m")       # boîte à outils, navigateur, observation du marché


@dataclass
class WindowOptions:
    minimize: bool = True
    hide_panels: bool = True
    maximize_chart: bool = True
    settle_s: float = 10.0                            # attente après l'apparition de la fenêtre (graphique et EA chargés)
    timeout_s: float = 120.0


class WinAPI(Protocol):
    def main_window(self, pids: set[int]) -> int | None: ...
    def menu_items(self, hwnd: int) -> list[tuple[str, int, bool]]: ...      # (raccourci en minuscules, id, coché)
    def command(self, hwnd: int, cmd_id: int) -> None: ...
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
        for hotkey, cmd_id, checked in api.menu_items(hwnd):
            if hotkey in PANEL_HOTKEYS and checked:
                api.command(hwnd, cmd_id)
                done["hidden"].append(hotkey)
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
            log.info("fenêtre %s : %s", name, res)
        except Exception as exc:                                   # jamais bloquant pour le bot
            log.warning("fenêtre %s : rangement impossible (%s)", name, exc)

    threading.Thread(target=run, daemon=True, name=f"win-{name}").start()


class Win32(WinAPI):
    """Implémentation Windows (user32 via ctypes)."""

    WM_COMMAND, WM_MDIGETACTIVE, WM_MDIMAXIMIZE = 0x0111, 0x0229, 0x0225
    MF_BYPOSITION, MF_CHECKED, MF_POPUP = 0x400, 0x8, 0x10
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
        u.GetMenu.argtypes = [w.HWND]
        u.GetMenu.restype = w.HMENU
        u.GetMenuItemCount.argtypes = [w.HMENU]
        u.GetSubMenu.argtypes = [w.HMENU, ctypes.c_int]
        u.GetSubMenu.restype = w.HMENU
        u.GetMenuItemID.argtypes = [w.HMENU, ctypes.c_int]
        u.GetMenuItemID.restype = w.UINT
        u.GetMenuState.argtypes = [w.HMENU, w.UINT, w.UINT]
        u.GetMenuState.restype = w.UINT
        u.GetMenuStringW.argtypes = [w.HMENU, w.UINT, w.LPWSTR, ctypes.c_int, w.UINT]
        u.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        u.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        u.SendMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        u.SendMessageW.restype = ctypes.c_ssize_t
        u.ShowWindow.argtypes = [w.HWND, ctypes.c_int]

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
                if area > best[1]:
                    best[0], best[1] = hwnd, area
            return True

        u.EnumWindows(self.EnumProc(cb), 0)
        return best[0]

    def menu_items(self, hwnd: int) -> list[tuple[str, int, bool]]:
        out: list[tuple[str, int, bool]] = []
        u, c = self.u, self.c
        top = u.GetMenu(hwnd)
        if not top:
            return out

        def walk(menu, depth: int) -> None:
            for pos in range(max(0, u.GetMenuItemCount(menu))):
                sub = u.GetSubMenu(menu, pos)
                if sub and depth < 3:
                    walk(sub, depth + 1)
                    continue
                buf = c.create_unicode_buffer(256)
                u.GetMenuStringW(menu, pos, buf, 256, self.MF_BYPOSITION)
                text = buf.value
                if "\t" not in text:
                    continue
                hotkey = text.split("\t", 1)[1].strip().lower().replace(" ", "")
                state = u.GetMenuState(menu, pos, self.MF_BYPOSITION)
                out.append((hotkey, int(u.GetMenuItemID(menu, pos)), bool(state & self.MF_CHECKED)))

        walk(top, 0)
        return out

    def command(self, hwnd: int, cmd_id: int) -> None:
        self.u.PostMessageW(hwnd, self.WM_COMMAND, cmd_id, 0)
        time.sleep(0.5)

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
