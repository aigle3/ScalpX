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
