"""Diagnostic : masquage des panneaux d'un vrai MetaTrader 5 (lancé par .github/workflows/window-check.yml)."""
import sys
import time

import psutil

_print = print


def print(*a):  # noqa: A001  annotations GitHub : lisibles via l'API même quand les journaux ne le sont pas
    line = " ".join(str(x) for x in a).replace("\n", " ")[:3000]
    _print("::notice::" + line)
    import pathlib
    pathlib.Path("diag-out").mkdir(exist_ok=True)
    with open("diag-out/window.txt", "a", encoding="utf-8") as f:
        f.write(line + "\n")

sys.path.insert(0, "bot")
from mt5.window import Win32  # noqa: E402

api = Win32()
pids = {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "terminal64.exe"}
h = api.main_window(pids)
print("fenetre", h, "pids", pids)
u = api.u
m = u.GetMenu(h) if h else None
print("menu", m, [api._menu_text(m, i) for i in range(u.GetMenuItemCount(m))] if m else None)
if m:
    for i in range(u.GetMenuItemCount(m)):
        if api._menu_text(m, i) in api.VIEW_MENU:
            print("avant", [(t, c) for _, t, _, c in api._checked_items(h, u.GetSubMenu(m, i), i)])
print("panneaux (ancienne méthode)", api.panels(h) if h else None)
print("masques", api.view_hide(h) if h else None)
time.sleep(3)
if m:
    for i in range(u.GetMenuItemCount(m)):
        if api._menu_text(m, i) in api.VIEW_MENU:
            print("apres", [(t, c) for _, t, _, c in api._checked_items(h, u.GetSubMenu(m, i), i)])
print("panneaux restants", api.panels(h) if h else None)

# fenêtres secondaires (mise à jour de MT5…) fermées, graphique agrandi : capture du panneau de l'EA
import ctypes
from ctypes import wintypes as w

found = []


def _cb(hwnd, _):
    pid = w.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value in pids and u.IsWindowVisible(hwnd) and hwnd != h:
        found.append(hwnd)
    return True


u.EnumWindows(api.EnumProc(_cb), 0)
for d in found:
    u.PostMessageW(d, 0x0010, 0, 0)                      # WM_CLOSE
print("dialogues fermés", len(found))
time.sleep(2)
print("graphique agrandi", api.maximize_chart(h))
u.ShowWindow(h, 3)                                       # fenêtre MT5 en plein écran (SW_MAXIMIZE)
time.sleep(4)
