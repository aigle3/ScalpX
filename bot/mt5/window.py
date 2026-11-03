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
    timeout_s: float = 300.0                          # VPS chargé : MT5 peut mettre plusieurs minutes à ouvrir sa fenêtre


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
        # 1) menu Affichage de MT5 : chaque panneau coché (observation du marché, navigateur, boîte à outils, barres
        #    d'outils, barre d'état…) est décoché par sa commande. Fiable : MT5 dit lui-même ce qui est affiché.
        via_menu = api.view_hide(hwnd) if hasattr(api, "view_hide") else None
        if via_menu:
            done["hidden"] += via_menu
        # 2) en plus (ancienne méthode) : panneaux ancrés encore visibles
        for panel, ctrl_id, title in api.panels(hwnd):
            if api.hide_panel(hwnd, panel, ctrl_id):
                done["hidden"].append(title or str(ctrl_id))
    if opts.maximize_chart:
        done["chart"] = api.maximize_chart(hwnd)
    if opts.minimize:
        api.minimize(hwnd)
        done["minimized"] = True
    return done


def ensure_arranged(api: WinAPI, pids: Callable[[], set[int]], opts: WindowOptions, name: str) -> str:
    """Contrôle périodique : fenêtre jamais rangée (introuvable au lancement) → rangement complet ; fenêtre ouverte
    (restaurée ou jamais réduite) → réduite. Renvoie "arrange", "minimize" ou ""."""
    if not opts.minimize or api is None:
        return ""
    hwnd = api.main_window(pids())
    if hwnd is None:
        return ""
    last = LAST.get(name)
    if last is None or not last[1].get("window"):
        res = arrange(api, pids, opts, wait_window=False)
        LAST[name] = (time.time(), res)
        log.info("fenêtre %s : rangée au contrôle périodique %s", name, res)
        return "arrange"
    if hasattr(api, "is_minimized") and not api.is_minimized(hwnd):
        api.minimize(hwnd)
        log.info("fenêtre %s : réduite (elle était ouverte)", name)
        return "minimize"
    return ""


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
        u.GetMenu.argtypes = [w.HWND]
        u.GetMenu.restype = w.HMENU
        u.GetSubMenu.argtypes = [w.HMENU, ctypes.c_int]
        u.GetSubMenu.restype = w.HMENU
        u.GetMenuItemCount.argtypes = [w.HMENU]
        u.GetMenuItemID.argtypes = [w.HMENU, ctypes.c_int]
        u.GetMenuItemID.restype = w.UINT
        u.GetMenuState.argtypes = [w.HMENU, w.UINT, w.UINT]
        u.GetMenuState.restype = w.UINT
        u.GetMenuStringW.argtypes = [w.HMENU, w.UINT, w.LPWSTR, ctypes.c_int, w.UINT]
        u.IsIconic.argtypes = [w.HWND]

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

    # menu Affichage (anglais / français) : éléments à décocher pour ne garder que le graphique
    VIEW_MENU = ("view", "affichage")
    VIEW_HIDE = ("market watch", "observation du marché", "data window", "fenêtre des données", "navigator", "navigateur",
                 "toolbox", "boîte à outils", "strategy tester", "testeur de stratégie", "status bar", "barre d'état",
                 "depth of market", "profondeur du marché", "charts bar", "barre des graphiques", "chats", "reports", "rapports")
    VIEW_TOOLBARS = ("toolbars", "barres d'outils", "barre d'outils")
    WM_INITMENUPOPUP, MF_BYPOSITION, MF_CHECKED = 0x0117, 0x400, 0x8

    def _menu_text(self, hmenu, i: int) -> str:
        buf = self.c.create_unicode_buffer(128)
        self.u.GetMenuStringW(hmenu, i, buf, 128, self.MF_BYPOSITION)
        return buf.value.replace("&", "").split("\t")[0].strip().lower()

    def _checked_items(self, hwnd: int, sub, pos: int) -> list[tuple[int, str, object]]:
        """(id, texte, sous-menu) des éléments du menu ; MT5 met à jour les coches à l'ouverture (WM_INITMENUPOPUP)."""
        u = self.u
        u.SendMessageW(hwnd, self.WM_INITMENUPOPUP, sub, pos)
        out = []
        for j in range(max(0, u.GetMenuItemCount(sub))):
            state = u.GetMenuState(sub, j, self.MF_BYPOSITION)
            iid = u.GetMenuItemID(sub, j)
            out.append((iid if iid != 0xFFFFFFFF else -1, self._menu_text(sub, j), u.GetSubMenu(sub, j),
                        bool(state != 0xFFFFFFFF and state & self.MF_CHECKED)))
        return out

    def view_hide(self, hwnd: int) -> list[str] | None:
        """Décoche dans le menu Affichage tout ce qui n'est pas le graphique. None : pas de menu Windows (autre méthode)."""
        u = self.u
        try:
            menu = u.GetMenu(hwnd)
            if not menu:
                return None
            view = next(((u.GetSubMenu(menu, i), i) for i in range(u.GetMenuItemCount(menu))
                         if self._menu_text(menu, i) in self.VIEW_MENU), None)
            if view is None or not view[0]:
                log.info("menu Affichage de MT5 introuvable")
                return None
            hidden: list[str] = []
            todo = []
            for iid, text, sub, checked in self._checked_items(hwnd, view[0], view[1]):
                if iid > 0 and checked and any(text.startswith(k) for k in self.VIEW_HIDE):
                    todo.append((iid, text))
                elif iid == -1 and sub and any(text.startswith(k) for k in self.VIEW_TOOLBARS):
                    for tid, ttext, _, tchecked in self._checked_items(hwnd, sub, 0):
                        if tid > 0 and tchecked:
                            todo.append((tid, ttext))
            for iid, text in todo:
                u.PostMessageW(hwnd, self.WM_COMMAND, iid, 0)               # comme un clic dans le menu Affichage
                time.sleep(0.3)
                hidden.append(text)
            return hidden
        except Exception as exc:                                          # jamais bloquant
            log.warning("menu Affichage : %s", exc)
            return None

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

    def is_minimized(self, hwnd: int) -> bool:
        return bool(self.u.IsIconic(hwnd))


def default_api() -> WinAPI | None:
    if os.name != "nt":
        return None
    try:
        return Win32()
    except Exception as exc:
        log.warning("API Windows indisponible : %s", exc)
        return None
