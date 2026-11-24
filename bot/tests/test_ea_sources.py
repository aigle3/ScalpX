"""Contrôles statiques des sources de l'EA (pas de compilateur MQL5 ici)."""
from pathlib import Path

EA = Path(__file__).resolve().parents[2] / "ea"
EXP = EA / "MQL5" / "Experts"


def _src(name: str) -> str:
    return (EXP / name).read_bytes().decode("utf-8-sig")


def test_versions_ea_coherentes():
    v = (EA / "version.txt").read_text().strip()
    assert f'#define SX_VERSION   "{v}"' in _src("SX_Core.mqh")
    maj, mi, _ = v.split(".")
    assert f'#property version     "{maj}.{int(mi):02d}"' in _src("ScalpX_v5.mq5")


def test_filet_sans_tick_appele_par_le_timer():
    a = _src("SX_Archi.mqh")
    assert "void TickFallback()" in a
    timer = a[a.index("void ArchiTimer()"):]
    assert "TickFallback();" in timer
    # globale déclarée avant son usage dans le statut (MQL5 : pas de déclaration tardive)
    assert a.index("bool     g_tickFb") < a.index("sans_tick")


def test_pause_et_fermer_tout_gardent_arret_500():
    a = _src("SX_Archi.mqh")
    assert 'if(!wasPause && g_state != ST_ARRET) SetState(ST_PAUSE' in a
    assert 'if(g_state != ST_ARRET) SetState(ST_PAUSE, true, "Commande FERMER_TOUT")' in a
    assert "if(g_state == ST_ACTIF || g_state == ST_PAUSE)" in a


def test_sources_crlf():
    for f in ("SX_Archi.mqh", "SX_Smart.mqh", "SX_Core.mqh", "ScalpX_v5.mq5"):
        b = (EXP / f).read_bytes()
        assert b.count(b"\n") == b.count(b"\r\n"), f


def test_filet_supprime_les_ordres_orphelins():
    a = _src("SX_Archi.mqh")
    fb = a[a.index("void TickFallback()"):a.index("void ArchiTimer()")]
    assert "else if(st_pendCount > 0)" in fb and "CancelPendingFast();" in fb


def test_market_watch_plus_reduite_et_reabonnement():
    a = _src("SX_Archi.mqh")
    timer = a[a.index("void ArchiTimer()"):]
    assert "TrimMarketWatchTick();" not in timer
    fb = a[a.index("void TickFallback()"):a.index("void ArchiTimer()")]
    assert "ChartSetSymbolPeriod(0, _Symbol, _Period);" in fb and "SymbolSelect(_Symbol, true);" in fb
