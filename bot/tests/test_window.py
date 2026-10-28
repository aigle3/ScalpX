"""Rangement de la fenêtre MT5 (API Windows simulée)."""
from mt5.window import WindowOptions, arrange


class FakeWin:
    def __init__(self, appears_after=0):
        self.calls, self.n = [], 0
        self.appears_after = appears_after
        self.menu = [("ctrl+t", 101, True), ("ctrl+n", 102, False), ("ctrl+m", 103, True), ("f9", 104, False)]

    def main_window(self, pids):
        self.n += 1
        return 555 if 42 in pids and self.n > self.appears_after else None

    def menu_items(self, hwnd):
        return self.menu

    def command(self, hwnd, cmd_id):
        self.calls.append(("cmd", cmd_id))

    def maximize_chart(self, hwnd):
        self.calls.append(("max",))
        return True

    def minimize(self, hwnd):
        self.calls.append(("min",))


def test_panneaux_masques_graphique_agrandi_puis_reduit():
    api, slept = FakeWin(appears_after=2), []
    res = arrange(api, lambda: {42}, WindowOptions(settle_s=10), sleep=slept.append)
    assert res == {"window": True, "hidden": ["ctrl+t", "ctrl+m"], "chart": True, "minimized": True}
    assert api.calls == [("cmd", 101), ("cmd", 103), ("max",), ("min",)]      # navigateur déjà masqué : pas touché
    assert slept == [2.0, 2.0, 10]                                            # attend la fenêtre, puis le graphique


def test_options_et_fenetre_introuvable():
    api = FakeWin()
    res = arrange(api, lambda: {42}, WindowOptions(hide_panels=False, maximize_chart=False, settle_s=0), sleep=lambda s: None)
    assert api.calls == [("min",)] and res["minimized"]
    res = arrange(FakeWin(), lambda: {7}, WindowOptions(timeout_s=0), sleep=lambda s: None)
    assert res == {"window": False, "hidden": [], "chart": False, "minimized": False}
