"""Rangement de la fenêtre MT5 (API Windows simulée)."""
from mt5.window import WindowOptions, arrange


class FakeWin:
    def __init__(self, appears_after=0):
        self.calls, self.n = [], 0
        self.appears_after = appears_after
        self.panel_list = [(11, 101, "Market Watch"), (12, 102, "Navigator"), (13, 103, "Toolbox")]

    def main_window(self, pids):
        self.n += 1
        return 555 if 42 in pids and self.n > self.appears_after else None

    def panels(self, hwnd):
        return self.panel_list

    def hide_panel(self, hwnd, panel, ctrl_id):
        self.calls.append(("hide", ctrl_id))
        return ctrl_id != 102                                   # le navigateur résiste (non compté)

    def maximize_chart(self, hwnd):
        self.calls.append(("max",))
        return True

    def minimize(self, hwnd):
        self.calls.append(("min",))


def test_panneaux_masques_graphique_agrandi_puis_reduit():
    api, slept = FakeWin(appears_after=2), []
    res = arrange(api, lambda: {42}, WindowOptions(settle_s=10), sleep=slept.append)
    assert res == {"window": True, "hidden": ["Market Watch", "Toolbox"], "chart": True, "minimized": True}
    assert api.calls == [("hide", 101), ("hide", 102), ("hide", 103), ("max",), ("min",)]
    assert slept == [2.0, 2.0, 10]                                            # attend la fenêtre, puis le graphique


def test_options_et_fenetre_introuvable():
    api = FakeWin()
    res = arrange(api, lambda: {42}, WindowOptions(hide_panels=False, maximize_chart=False, settle_s=0), sleep=lambda s: None)
    assert api.calls == [("min",)] and res["minimized"]
    res = arrange(FakeWin(), lambda: {7}, WindowOptions(timeout_s=0), sleep=lambda s: None)
    assert res == {"window": False, "hidden": [], "chart": False, "minimized": False}



def test_menu_affichage_decoche_tout_sauf_le_graphique():
    class MenuWin(FakeWin):
        def view_hide(self, hwnd):
            self.calls.append(("menu",))
            return ["market watch", "navigator", "toolbox", "standard"]

        def panels(self, hwnd):
            return []
    api = MenuWin()
    res = arrange(api, lambda: {42}, WindowOptions(settle_s=0), sleep=lambda s: None)
    assert res["hidden"] == ["market watch", "navigator", "toolbox", "standard"] and api.calls[0] == ("menu",)


def test_controle_periodique_range_ou_reduit():
    from mt5 import window as wm

    class Api:
        def __init__(self):
            self.mini, self.calls = False, []

        def main_window(self, pids):
            return 42

        def view_hide(self, h):
            self.calls.append("view")
            return ["Navigateur"]

        def panels(self, h):
            return []

        def hide_panel(self, *a):
            return True

        def maximize_chart(self, h):
            self.calls.append("chart")
            return True

        def minimize(self, h):
            self.mini = True
            self.calls.append("min")

        def is_minimized(self, h):
            return self.mini

    api = Api()
    opts = wm.WindowOptions(settle_s=0)
    wm.LAST.pop("t1", None)
    wm.LAST["t1"] = (0, {"window": False})                       # introuvable au lancement
    assert wm.ensure_arranged(api, lambda: {1}, opts, "t1") == "arrange" and "chart" in api.calls and api.mini
    assert wm.ensure_arranged(api, lambda: {1}, opts, "t1") == ""                   # déjà réduite
    api.mini = False                                                                # rouverte à la main
    assert wm.ensure_arranged(api, lambda: {1}, opts, "t1") == "minimize" and api.mini
    assert wm.ensure_arranged(api, lambda: {1}, wm.WindowOptions(minimize=False), "t1") == ""
