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


def test_prix_traites_par_le_minuteur_si_ontick_muet():
    m = _src("ScalpX_v5.mq5")
    ontick = m[m.index("void OnTick()"):m.index("void TimerTick()")]
    assert "ProcessTick();" in ontick and "g_onTickMs = GetTickCount64();" in ontick
    timer = m[m.index("void OnTimer()"):]
    assert timer.index("TimerTick();") < timer.index("ArchiTimer();")
    tt = m[m.index("void TimerTick()"):m.index("void ProcessTick()")]
    assert "q.time_msc == g_lastQuoteMsc" in tt and "MQL_TESTER" in tt and "ProcessTick();" in tt
    pt = m[m.index("void ProcessTick()"):]
    assert pt.index("g_lastQuoteMsc = pq.time_msc") < pt.index("RefreshState();")
    core = _src("SX_Core.mqh")                       # globales déclarées avant SX_Archi (statut)
    assert "ulong    g_onTickMs" in core and "bool     g_timerTicks" in core


def test_tp_courtier_sans_fermeture_en_double():
    t = _src("SX_Trade.mqh")
    assert "bool IsGridOrder()" in t and "ORDER_REASON_TP" in t and "bool TPExecuting()" in t
    assert t.count("if(!IsGridOrder()) continue;") == 3                 # comptage, annulation rapide, fermeture complète
    assert "if(OrderGetInteger(ORDER_MAGIC) != MagicNumber) continue;" not in t
    assert "if(round < 2 && TPExecuting()) continue;" in t
    chk = t[t.index("bool CheckTPExit()"):]
    assert "if(st_tpWrong == 0)" in chk and chk.index("g_tpWaitMs") < chk.index('CloseAllFast("TP atteint")')
    m = _src("ScalpX_v5.mq5")
    assert "TRADE_RETCODE_POSITION_CLOSED || rc == TRADE_RETCODE_FROZEN" in m
    assert "ulong    g_tpWaitMs" in _src("SX_Core.mqh")


def test_retrait_vaut_reprise_meme_apres_une_pause():
    a = _src("SX_Archi.mqh")
    f = a[a.index("void RepriseApresRetrait(double bal)"):a.index("void RepriseRetraitTimer()")]
    assert f.index("g_stoppedManually = false;") < f.index("SetState(ST_ACTIF, false);")
    assert 'GlobalVariableDel(GVN("stopped"));' in f and "g_pauseUntil = 0;" in f


def test_annulation_de_grille_jamais_renvoyee_trop_tot():
    t = _src("SX_Trade.mqh")
    assert "bool CancelPending(ulong t)" in t and "ORDER_STATE_REQUEST_CANCEL" in t
    assert t.count("if(CancelPending(t)) continue;") == 2 and t.count("CancelMark(t);") == 2


def test_retrait_terminal_arrete_recale_le_resultat_du_jour():
    a = _src("SX_Archi.mqh")
    f = a[a.index("void DayCashResync()"):a.index("void ArchiTimer()")]
    assert "DEAL_TYPE_BALANCE" in f and "AdjustForCashFlow(missed);" in f
    assert "missed * cash < 0" in f and "MathAbs(missed) > MathAbs(cash) + 0.01" in f     # garde-fou
    timer = a[a.index("void ArchiTimer()"):]
    assert "DayCashResync();" in timer
