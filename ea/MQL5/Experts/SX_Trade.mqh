//+------------------------------------------------------------------+
//| SX_Trade.mqh - ScalpX v4.1                                       |
//| Instantané d'état, indicateurs, calcul lots/grille/TP,           |
//| exécution des ordres (lot groupé asynchrone, retries, backoff).  |
//+------------------------------------------------------------------+

//+------------------------------------------------------------------+
//| Indicateurs (EMA rapide, EMA lente, ATR) sur le timeframe du     |
//| robot, indépendamment du timeframe du graphique.                 |
//+------------------------------------------------------------------+
void ReleaseIndicators()
{
   if(g_hFast != INVALID_HANDLE) { IndicatorRelease(g_hFast); g_hFast = INVALID_HANDLE; }
   if(g_hSlow != INVALID_HANDLE) { IndicatorRelease(g_hSlow); g_hSlow = INVALID_HANDLE; }
   if(g_hATR  != INVALID_HANDLE) { IndicatorRelease(g_hATR);  g_hATR  = INVALID_HANDLE; }
   if(g_hFastLo != INVALID_HANDLE) { IndicatorRelease(g_hFastLo); g_hFastLo = INVALID_HANDLE; }
   if(g_hSlowLo != INVALID_HANDLE) { IndicatorRelease(g_hSlowLo); g_hSlowLo = INVALID_HANDLE; }
   if(g_hFastHi != INVALID_HANDLE) { IndicatorRelease(g_hFastHi); g_hFastHi = INVALID_HANDLE; }
   if(g_hSlowHi != INVALID_HANDLE) { IndicatorRelease(g_hSlowHi); g_hSlowHi = INVALID_HANDLE; }
   if(g_hATRHi  != INVALID_HANDLE) { IndicatorRelease(g_hATRHi);  g_hATRHi  = INVALID_HANDLE; }
   if(g_hSlowExt != INVALID_HANDLE) { IndicatorRelease(g_hSlowExt); g_hSlowExt = INVALID_HANDLE; }   // [ARCHI] 7.3.0
   if(g_hATRExt  != INVALID_HANDLE) { IndicatorRelease(g_hATRExt);  g_hATRExt  = INVALID_HANDLE; }
}

bool InitIndicators()
{
   g_sigTF = TfFromIndex(PInt(P_TF));
   g_hFast = iMA(_Symbol, g_sigTF, PInt(P_FAST), 0, MODE_EMA, PRICE_CLOSE);
   g_hSlow = iMA(_Symbol, g_sigTF, PInt(P_SLOW), 0, MODE_EMA, PRICE_CLOSE);
   g_hATR  = iATR(_Symbol, g_sigTF, PInt(P_ATRP));
   g_loTF = TfFromIndex(PInt(P_LOTF));
   g_hiTF = TfFromIndex(PInt(P_HITF));
   g_hFastLo = iMA(_Symbol, g_loTF, PInt(P_LOFAST), 0, MODE_EMA, PRICE_CLOSE);
   g_hSlowLo = iMA(_Symbol, g_loTF, PInt(P_LOSLOW), 0, MODE_EMA, PRICE_CLOSE);
   g_hFastHi = iMA(_Symbol, g_hiTF, PInt(P_HIFAST), 0, MODE_EMA, PRICE_CLOSE);
   g_hSlowHi = iMA(_Symbol, g_hiTF, PInt(P_HISLOW), 0, MODE_EMA, PRICE_CLOSE);
   g_hATRHi  = iATR(_Symbol, g_hiTF, PInt(P_ATRP));
   // [ARCHI] début : 7.3.0 extension mesurée sur un autre timeframe (facultatif : sans ces indicateurs, le filtre utilise le TF du robot)
   if(PInt(P_EXTTF) > 0)
   {
      ENUM_TIMEFRAMES etf = TfFromIndex(PInt(P_EXTTF) - 1);
      g_hSlowExt = iMA(_Symbol, etf, PInt(P_SLOW), 0, MODE_EMA, PRICE_CLOSE);
      g_hATRExt  = iATR(_Symbol, etf, PInt(P_ATRP));
   }
   // [ARCHI] fin
   if(g_hFast == INVALID_HANDLE || g_hSlow == INVALID_HANDLE || g_hATR == INVALID_HANDLE ||
      g_hFastLo == INVALID_HANDLE || g_hSlowLo == INVALID_HANDLE || g_hFastHi == INVALID_HANDLE ||
      g_hSlowHi == INVALID_HANDLE || g_hATRHi == INVALID_HANDLE)
   {
      Log("Erreur création des indicateurs (code " + IntegerToString(GetLastError()) + ")");
      return false;
   }
   return true;
}

bool RebuildIndicators()
{
   ReleaseIndicators();
   bool ok = InitIndicators();
   g_tpSig = -1;
   return ok;
}

bool ReadEMAs(int shift, double &f, double &s)
{
   double a[1], b[1];
   if(CopyBuffer(g_hFast, 0, shift, 1, a) != 1) return false;
   if(CopyBuffer(g_hSlow, 0, shift, 1, b) != 1) return false;
   f = a[0];
   s = b[0];
   return (f != 0.0 && s != 0.0);
}

bool ReadEMAsH(int hf, int hs, int shift, double &f, double &s)
{
   double a[1], b[1];
   if(hf == INVALID_HANDLE || hs == INVALID_HANDLE) return false;
   if(CopyBuffer(hf, 0, shift, 1, a) != 1) return false;
   if(CopyBuffer(hs, 0, shift, 1, b) != 1) return false;
   f = a[0];
   s = b[0];
   return (f != 0.0 && s != 0.0);
}

double ATRPoints(int shift)
{
   double b[1];
   if(CopyBuffer(g_hATR, 0, shift, 1, b) != 1) return 0.0;
   return b[0] / _Point;
}

// +1 = ACHAT (rapide > lente), -1 = VENTE, 0 = aucun signal
int Signal()
{
   double f, s;
   if(!ReadEMAs(BarShift(), f, s)) return 0;
   mk_fast = f;
   mk_slow = s;
   if(f > s) return 1;
   if(f < s) return -1;
   return 0;
}

//+------------------------------------------------------------------+
//| Modes de remplissage                                             |
//+------------------------------------------------------------------+
ENUM_ORDER_TYPE_FILLING NextFilling(ENUM_ORDER_TYPE_FILLING cur)
{
   if(cur == ORDER_FILLING_IOC) return ORDER_FILLING_FOK;
   if(cur == ORDER_FILLING_FOK) return ORDER_FILLING_RETURN;
   return ORDER_FILLING_IOC;
}

void InitFilling()
{
   int f = (int)SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((f & SYMBOL_FILLING_IOC) == SYMBOL_FILLING_IOC)      g_fillMkt = ORDER_FILLING_IOC;
   else if((f & SYMBOL_FILLING_FOK) == SYMBOL_FILLING_FOK) g_fillMkt = ORDER_FILLING_FOK;
   else                                                    g_fillMkt = ORDER_FILLING_RETURN;
   g_fillPend = g_fillMkt;
}

//+------------------------------------------------------------------+
//| Instantané : une seule passe sur positions + ordres              |
//+------------------------------------------------------------------+
// [ARCHI] début 8.3.1 : rapport du 24/11 (TP atteint → ordres refusés 10036 / 10029 / « Invalid request », tous les jours)
// Ordre DE GRILLE (sélectionné par OrderGetTicket) : nos ordres limite/stop. Exclut l'ordre que le SERVEUR crée pour
// exécuter un TP / SL / stop-out (type marché, même magic que la position) : il ne s'annule pas et faussait le compte (9 au lieu de 8).
bool IsGridOrder()
{
   if(OrderGetString(ORDER_SYMBOL) != _Symbol) return false;
   if(OrderGetInteger(ORDER_MAGIC) != MagicNumber) return false;
   ENUM_ORDER_TYPE ot = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
   if(ot != ORDER_TYPE_BUY_LIMIT && ot != ORDER_TYPE_SELL_LIMIT && ot != ORDER_TYPE_BUY_STOP && ot != ORDER_TYPE_SELL_STOP &&
      ot != ORDER_TYPE_BUY_STOP_LIMIT && ot != ORDER_TYPE_SELL_STOP_LIMIT) return false;
   ENUM_ORDER_REASON why = (ENUM_ORDER_REASON)OrderGetInteger(ORDER_REASON);
   return (why != ORDER_REASON_TP && why != ORDER_REASON_SL && why != ORDER_REASON_SO);
}

// Position (sélectionnée par PositionGetTicket) dont le TP courtier est touché ou dans la zone de gel : le serveur
// l'exécute → ne pas envoyer de fermeture au marché (sinon 10029 « gelé » ou 10036 « déjà fermée »).
bool TPExecuting()
{
   double tp = PositionGetDouble(POSITION_TP);
   if(tp <= 0) return false;
   bool buy = ((ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
   double px = buy ? SymbolInfoDouble(_Symbol, SYMBOL_BID) : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   if(px <= 0) return false;
   if(buy ? px >= tp : px <= tp) return true;
   double freeze = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL) * _Point;
   return (freeze > 0 && MathAbs(px - tp) <= freeze);
}
// [ARCHI] fin

void RefreshState()
{
   st_posCount = 0; st_posLots = 0; st_floating = 0; st_posType = -1;
   st_lowOpen = 0; st_highOpen = 0; st_firstTime = 0; st_tpWrong = 0;
   double sumPL = 0, sumP = 0;
   double tp = g_tpPrice;

   int n = PositionsTotal();
   for(int i = 0; i < n; i++)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      double op  = PositionGetDouble(POSITION_PRICE_OPEN);
      double vol = PositionGetDouble(POSITION_VOLUME);
      st_posType = (int)PositionGetInteger(POSITION_TYPE);
      st_posCount++;
      st_posLots += vol;
      st_floating += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
      sumPL += op * vol;
      sumP  += op;
      if(st_lowOpen == 0 || op < st_lowOpen)   st_lowOpen = op;
      if(st_highOpen == 0 || op > st_highOpen) st_highOpen = op;
      datetime pt = (datetime)PositionGetInteger(POSITION_TIME);
      if(st_firstTime == 0 || pt < st_firstTime) st_firstTime = pt;
      if(tp > 0 && MathAbs(PositionGetDouble(POSITION_TP) - tp) > _Point * 0.5) st_tpWrong++;
   }
   if(st_posCount > 0)
      st_avg = (PBool(P_WAVG) && st_posLots > 0) ? (sumPL / st_posLots) : (sumP / st_posCount);
   else
      st_avg = 0;

   st_pendCount = 0; st_lowPend = 0; st_highPend = 0;
   int m = OrdersTotal();
   for(int j = 0; j < m; j++)
   {
      ulong ot = OrderGetTicket(j);
      if(ot == 0) continue;
      if(!IsGridOrder()) continue;                   // [ARCHI] 8.3.1 : ordre serveur d'exécution du TP exclu
      double pr = OrderGetDouble(ORDER_PRICE_OPEN);
      st_pendCount++;
      if(st_lowPend == 0 || pr < st_lowPend)   st_lowPend = pr;
      if(st_highPend == 0 || pr > st_highPend) st_highPend = pr;
   }
}

//+------------------------------------------------------------------+
//| Lots / distance de grille / TP                                   |
//+------------------------------------------------------------------+
double CalcLot(int n)
{
   int tbm = MathMax(1, PInt(P_TBM));
   int mc  = n / tbm;
   double lot = PDbl(P_LOT) * MathPow(PDbl(P_LOTMULT), mc);
   return NormVolume(lot);
}

double CalcGridStep(int n)
{
   double step = (double)PInt(P_GRID);
   if(PBool(P_DYNGRID))
   {
      double atr = ATRPoints(BarShift());
      if(atr > 0)
      {
         step = atr * PDbl(P_GRIDATR);
         if(step < PInt(P_GRIDMIN)) step = PInt(P_GRIDMIN);
         if(step > PInt(P_GRIDMAX)) step = PInt(P_GRIDMAX);
      }
      // ATR indisponible : repli sur la distance fixe
      long sl = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
      if(step < (double)sl) step = (double)sl;   // jamais sous le niveau de stops du broker
   }
   double gm = PDbl(P_GRIDMULT);
   int tbm = MathMax(1, PInt(P_TBM));
   if(gm != 1.0) step *= MathPow(gm, n / tbm);
   return step;
}

double CalcTPDist()
{
   if(!PBool(P_DYNTP)) return PDbl(P_TPFIXED);
   double atr = ATRPoints(BarShift());
   if(atr <= 0) return (g_tpDistPts > 0) ? g_tpDistPts : PDbl(P_TPFIXED);
   double d = atr * PDbl(P_ATRMULT);
   if(d < PInt(P_TPMIN)) d = PInt(P_TPMIN);
   if(d > PInt(P_TPMAX)) d = PInt(P_TPMAX);
   return d;
}

//+------------------------------------------------------------------+
//| Erreurs / backoff                                                |
//+------------------------------------------------------------------+
bool IsRetryable(uint rc)
{
   return (rc == TRADE_RETCODE_REQUOTE || rc == TRADE_RETCODE_PRICE_CHANGED ||
           rc == TRADE_RETCODE_PRICE_OFF || rc == TRADE_RETCODE_TIMEOUT ||
           rc == TRADE_RETCODE_CONNECTION || rc == TRADE_RETCODE_TOO_MANY_REQUESTS);
}

string TradeErrText(uint rc)
{
   switch(rc)
   {
      case TRADE_RETCODE_REQUOTE:          return "Requote (prix changé)";
      case TRADE_RETCODE_PRICE_CHANGED:    return "Prix changé";
      case TRADE_RETCODE_PRICE_OFF:        return "Pas de prix disponible";
      case TRADE_RETCODE_REJECT:           return "Ordre rejeté par le broker";
      case TRADE_RETCODE_NO_MONEY:         return "Marge insuffisante";
      case TRADE_RETCODE_MARKET_CLOSED:    return "Marché fermé";
      case TRADE_RETCODE_TRADE_DISABLED:   return "Trading désactivé";
      case TRADE_RETCODE_INVALID_VOLUME:   return "Volume invalide";
      case TRADE_RETCODE_INVALID_PRICE:    return "Prix invalide";
      case TRADE_RETCODE_INVALID_STOPS:    return "Stops invalides";
      case TRADE_RETCODE_INVALID_FILL:  return "Mode de remplissage refusé";
      case TRADE_RETCODE_TIMEOUT:          return "Délai dépassé";
      case TRADE_RETCODE_CONNECTION:       return "Pas de connexion";
      case TRADE_RETCODE_TOO_MANY_REQUESTS:return "Trop de requêtes";
      case TRADE_RETCODE_LIMIT_ORDERS:     return "Limite d'ordres atteinte";
      case TRADE_RETCODE_LIMIT_VOLUME:     return "Limite de volume atteinte";
   }
   return "Code " + IntegerToString((int)rc);
}

void RegisterTradeSuccess()
{
   if(g_failCount > 0) Log("Ordre exécuté — compteur d'échecs remis à zéro");
   g_failCount = 0;
   g_nextTradeMs = 0;
   BrokerBlockClear();   // [ARCHI] fin d'un éventuel blocage « marché fermé / trading désactivé »
}

void RegisterTradeFailure(string what, uint rc)
{
   g_failCount++;
   int sec = 3 * (1 << MathMin(g_failCount - 1, 4));   // 3, 6, 12, 24, 48 s
   if(sec > 60) sec = 60;
   g_nextTradeMs = GetTickCount64() + (ulong)sec * 1000;
   Log("Échec " + what + " #" + IntegerToString(g_failCount) + " : " + TradeErrText(rc) +
       " — nouvel essai dans " + IntegerToString(sec) + " s");
   if(PBool(P_N_ERRORS) && (g_failCount == 1 || g_failCount == 3 || g_failCount == 5))
   {
      string m = "⚠️ Échec d'ordre (" + what + ")\n━━━━━━━━━━━━━━━━━━\n" +
                 "❌ " + TradeErrText(rc) + "\n" +
                 "🔁 Échecs consécutifs : " + IntegerToString(g_failCount) + "\n" +
                 "⏳ Nouvel essai dans " + IntegerToString(sec) + " s\n" +
                 "💵 Marge libre : " + F2(AccountInfoDouble(ACCOUNT_MARGIN_FREE)) + " " + Cur();
      if(rc == TRADE_RETCODE_NO_MONEY)      m += "\n\n💡 Balance insuffisante pour ce lot";
      if(rc == TRADE_RETCODE_MARKET_CLOSED) m += "\n\n💡 Le marché est fermé";
      TgCrit(m);
   }
}

//+------------------------------------------------------------------+
//| Environnement de trading (remplace l'ancien ordre de test réel)  |
//+------------------------------------------------------------------+
bool MarketOpenNow()
{
   if(SymbolInfoInteger(_Symbol, SYMBOL_TRADE_MODE) == SYMBOL_TRADE_MODE_DISABLED) return false;
   MqlDateTime dt;
   TimeToStruct(TimeTradeServer(), dt);
   long sod = dt.hour * 3600 + dt.min * 60 + dt.sec;
   datetime from, to;
   for(int i = 0; i < 10; i++)
   {
      if(!SymbolInfoSessionTrade(_Symbol, (ENUM_DAY_OF_WEEK)dt.day_of_week, i, from, to)) break;
      if(sod >= (long)from && sod < (long)to) return true;
   }
   return false;
}

bool TradeEnvOK(string &why)
{
   if(!TerminalInfoInteger(TERMINAL_CONNECTED))     { why = "terminal déconnecté"; return false; }
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)) { why = "AutoTrading désactivé (terminal)"; return false; }
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED))           { why = "trading algo non autorisé pour ce robot"; return false; }
   if(!AccountInfoInteger(ACCOUNT_TRADE_ALLOWED))   { why = "trading interdit sur ce compte"; return false; }
   if(!AccountInfoInteger(ACCOUNT_TRADE_EXPERT))    { why = "trading par robots interdit par le broker"; return false; }
   if(SymbolInfoInteger(_Symbol, SYMBOL_TRADE_MODE) == SYMBOL_TRADE_MODE_DISABLED) { why = "symbole non tradable"; return false; }
   return true;
}

//+------------------------------------------------------------------+
//| Ouverture de la position initiale (marché, synchrone + retries)  |
//+------------------------------------------------------------------+
bool SendMarketEntry(ENUM_ORDER_TYPE type)
{
   double vol = CalcLot(st_posCount + st_pendCount);
   if(vol <= 0) return false;
   uint lastRc = 0;

   for(int a = 0; a <= PInt(P_RETRIES); a++)
   {
      MqlTick tk;
      if(!SymbolInfoTick(_Symbol, tk)) return false;
      MqlTradeRequest rq; MqlTradeResult rs;
      ZeroMemory(rq); ZeroMemory(rs);
      rq.action       = TRADE_ACTION_DEAL;
      rq.symbol       = _Symbol;
      rq.volume       = vol;
      rq.type         = type;
      rq.price        = (type == ORDER_TYPE_BUY) ? tk.ask : tk.bid;
      rq.deviation    = PInt(P_DEVIATION);
      rq.magic        = MagicNumber;
      rq.type_filling = g_fillMkt;
      rq.comment      = "ScalpX4";

      bool sent = OrderSend(rq, rs);
      lastRc = rs.retcode;
      if(sent && (lastRc == TRADE_RETCODE_DONE || lastRc == TRADE_RETCODE_DONE_PARTIAL))
      {
         RegisterTradeSuccess();
         Log(string(type == ORDER_TYPE_BUY ? "ACHAT " : "VENTE ") + F2(vol) + " lot @ " + PX(rs.price));
         return true;
      }
      if(lastRc == TRADE_RETCODE_INVALID_FILL) { g_fillMkt = NextFilling(g_fillMkt); continue; }
      if(IsRetryable(lastRc)) { Sleep(150); continue; }
      break;
   }
   // [ARCHI] début : marché fermé / trading désactivé = pas un échec d'ordre, nouvelles tentatives espacées
   if(IsBlockingRc(lastRc))
   {
      RegisterBrokerBlock(type == ORDER_TYPE_BUY ? "ouverture ACHAT" : "ouverture VENTE", lastRc);
      return false;
   }
   // [ARCHI] fin
   RegisterTradeFailure(type == ORDER_TYPE_BUY ? "ouverture ACHAT" : "ouverture VENTE", lastRc);
   return false;
}

//+------------------------------------------------------------------+
//| Grille : tous les niveaux manquants sont envoyés d'un coup       |
//| (OrderSendAsync) au lieu d'un ordre par seconde.                 |
//+------------------------------------------------------------------+
ulong g_nextGridMs = 0;

// [ARCHI] début : marché fermé / trading désactivé — gestion propre, nouvelles tentatives automatiques.
// Réponse du broker « marché fermé » ou « trading désactivé » : ce n'est pas un échec d'ordre (pas de compteur d'échecs,
// pas de rafale de messages). Entrées : nouvel essai espacé ; un message par épisode (rappel toutes les 30 min) puis
// un message de rétablissement. Fermetures (CloseAllFast) : essais espacés (5 s → 60 s), sans limite, jusqu'à réussite.
uint    g_blkRc              = 0;       // dernier code bloquant du broker (0 = aucun)
int     g_blkCount           = 0;       // refus consécutifs
ulong   g_blkLastMs          = 0;       // dernière fois qu'un code bloquant a été vu
ulong   g_blkNotifyMs        = 0;       // dernier message de l'épisode (0 = aucun envoyé)
bool    g_closeBlocked       = false;   // une fermeture est impossible en ce moment
int     g_closeBlockCount    = 0;
ulong   g_closeBlockUntilMs  = 0;       // CloseAllFast suspendu jusqu'à (tant que la cause persiste)
ulong   g_closeBlockNotifyMs = 0;
string  g_closeBlockWhy      = "";
string  g_closeRetryReason   = "";      // fermeture voulue (manuelle / stop loss d'urgence / vacances) à refaire tant qu'elle est bloquée
ulong   g_closeRetryUntilMs  = 0;       // abandon des essais automatiques au-delà (30 min)

bool IsBlockingRc(uint rc)
{
   return (rc == TRADE_RETCODE_MARKET_CLOSED || rc == TRADE_RETCODE_TRADE_DISABLED);
}

void NoteBrokerBlockRc(uint rc)
{
   g_blkRc     = rc;
   g_blkLastMs = GetTickCount64();
}

string BrokerBlockText()
{
   if(g_blkRc == TRADE_RETCODE_MARKET_CLOSED)  return "marché fermé (réponse du broker)";
   if(g_blkRc == TRADE_RETCODE_TRADE_DISABLED) return "trading désactivé (réponse du broker)";
   return "";
}

void RegisterBrokerBlock(string what, uint rc)
{
   ulong now = GetTickCount64();
   bool first = (g_blkNotifyMs == 0);
   NoteBrokerBlockRc(rc);
   g_blkCount++;
   int sec = 60;                                           // marché fermé : un essai par minute
   if(rc == TRADE_RETCODE_TRADE_DISABLED)
   {
      sec = 30 * (1 << MathMin(g_blkCount - 1, 3));        // trading désactivé : 30, 60, 120, 240 s, puis 300 s
      if(sec > 300) sec = 300;
   }
   g_nextTradeMs = now + (ulong)sec * 1000;
   g_nextGridMs  = g_nextTradeMs;
   if(first || now - g_blkNotifyMs > 1800000)
   {
      g_blkNotifyMs = now;
      Log(what + " impossible : " + BrokerBlockText() + " — nouvel essai dans " + IntegerToString(sec) + " s");
      if(PBool(P_N_ERRORS))
      {
         string m;
         if(rc == TRADE_RETCODE_TRADE_DISABLED)
            m = "🚫 TRADING DÉSACTIVÉ (réponse du broker)\n━━━━━━━━━━━━━━━━━━\n⛔ " + what + " impossible pour l'instant." +
                "\n🔁 Nouvel essai automatique dans " + IntegerToString(sec) + " s (délai croissant, 5 min maximum)." +
                "\n\n💡 Vérifie le compte et le broker si cela dure.";
         else
            m = "🌙 MARCHÉ FERMÉ (réponse du broker)\n━━━━━━━━━━━━━━━━━━\n⏳ " + what + " impossible pour l'instant." +
                "\n🔁 Nouvel essai automatique dans " + IntegerToString(sec) + " s, sans intervention.";
         TgQueueP(m, rc == TRADE_RETCODE_TRADE_DISABLED ? 2 : 0);
      }
   }
}

void BrokerBlockClear()
{
   if(g_blkRc == 0) return;
   g_blkRc = 0;
   g_blkCount = 0;
   if(g_blkNotifyMs > 0)
   {
      g_blkNotifyMs = 0;
      Log("Trading rétabli (fin du blocage broker)");
      if(PBool(P_N_ERRORS)) TgQueueP("✅ TRADING RÉTABLI\n━━━━━━━━━━━━━━━━━━\nLe broker accepte de nouveau les ordres : le robot reprend normalement.", 1);
   }
}

// Raison pour laquelle une fermeture ne peut pas aboutir maintenant ("" si rien ne l'en empêche)
string CloseBlockReason()
{
   string w;
   if(!TradeEnvOK(w))   return w;
   if(!MarketOpenNow()) return "marché fermé";
   if(g_blkRc != 0 && GetTickCount64() - g_blkLastMs < 20000) return BrokerBlockText();
   return "";
}

// Fermeture restée incomplète : si la cause est « marché fermé / trading désactivé / connexion », on réessaie plus tard
bool CloseReasonRetries(string reason)
{
   return (StringFind(reason, "fermeture manuelle") == 0 || StringFind(reason, "stop loss d'urgence") == 0 ||
           StringFind(reason, "mode vacances") == 0);
}

void CloseBlockNote(int left, string reason)
{
   string why = CloseBlockReason();
   if(why == "") return;                                    // incomplète pour une autre raison : comportement inchangé
   ulong now = GetTickCount64();
   g_closeBlockCount++;
   int sec = 5 * (1 << MathMin(g_closeBlockCount - 1, 4));  // 5, 10, 20, 40, 80 → 60 s maximum
   if(sec > 60) sec = 60;
   g_closeBlockUntilMs = now + (ulong)sec * 1000;
   g_closeBlockWhy = why;
   if(CloseReasonRetries(reason))               // décision explicite (pas de condition à réévaluer) : on la refait, 30 min maximum
   {
      if(g_closeRetryReason == "") g_closeRetryUntilMs = now + 1800000;
      g_closeRetryReason = reason;
   }
   if(!g_closeBlocked || now - g_closeBlockNotifyMs > 1800000)
   {
      g_closeBlockNotifyMs = now;
      TgQueueP("🚫 FERMETURE IMPOSSIBLE POUR LE MOMENT\n━━━━━━━━━━━━━━━━━━\n📌 " + IntegerToString(left) +
               " élément(s) encore ouvert(s)\n❓ Raison : " + why +
               "\n🔁 Nouvel essai automatique dans " + IntegerToString(sec) + " s (délai croissant, 60 s maximum), jusqu'à réussite.", 2);
   }
   g_closeBlocked = true;
}

// Appelé chaque seconde : refait une fermeture voulue (manuelle, stop loss d'urgence, vacances) restée bloquée
void CloseRetryTick()
{
   if(g_closeRetryReason == "") return;
   ulong now = GetTickCount64();
   if(g_enabled)                                // le robot a été relancé : la décision de fermer n'est plus d'actualité
   {
      g_closeRetryReason = "";
      return;
   }
   RefreshState();
   if(st_posCount + st_pendCount == 0) { g_closeRetryReason = ""; return; }
   if(now > g_closeRetryUntilMs)
   {
      string r = g_closeRetryReason;
      g_closeRetryReason = "";
      TgQueueP("⚠️ FERMETURE NON EFFECTUÉE\n━━━━━━━━━━━━━━━━━━\n📌 " + IntegerToString(st_posCount + st_pendCount) +
               " élément(s) encore ouvert(s) après 30 min d'essais (" + r + ").\n👉 Vérifie dans MT5.", 2);
      return;
   }
   if(g_closing || now < g_closeBlockUntilMs) return;
   CloseAllFast(g_closeRetryReason);
}

void CloseBlockClear()
{
   if(!g_closeBlocked) return;
   g_closeBlocked = false;
   g_closeBlockCount = 0;
   g_closeBlockUntilMs = 0;
   g_closeBlockWhy = "";
   if(g_closeBlockNotifyMs > 0)
   {
      g_closeBlockNotifyMs = 0;
      Log("Fermeture rétablie");
      TgQueueP("✅ FERMETURE RÉTABLIE\n━━━━━━━━━━━━━━━━━━\nLes positions ont pu être fermées.", 1);
   }
}
// [ARCHI] fin

bool SendPendingAsync(ENUM_ORDER_TYPE type, double vol, double price)
{
   MqlTradeRequest rq; MqlTradeResult rs;
   ZeroMemory(rq); ZeroMemory(rs);
   rq.action       = TRADE_ACTION_PENDING;
   rq.symbol       = _Symbol;
   rq.volume       = vol;
   rq.type         = type;
   rq.price        = price;
   rq.magic        = MagicNumber;
   rq.type_filling = g_fillPend;
   rq.type_time    = ORDER_TIME_GTC;
   rq.comment      = "ScalpX4 grid";
   return OrderSendAsync(rq, rs);
}

int PlaceGridBatch()
{
   if(st_posCount == 0 || st_posType < 0) return 0;
   int need = PInt(P_MAXPEND) - st_pendCount;
   if(need <= 0) return 0;

   bool buy = (st_posType == POSITION_TYPE_BUY);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   long   stops = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double minDist = MathMax(stops * _Point, _Point * 5);

   double anchor;
   if(buy) anchor = (st_lowPend  > 0) ? st_lowPend  : st_lowOpen;
   else    anchor = (st_highPend > 0) ? st_highPend : st_highOpen;

   int n = st_posCount + st_pendCount;
   int sent = 0;
   for(int k = 0; k < need; k++)
   {
      double stepPts = CalcGridStep(n);
      double lot     = CalcLot(n);
      double price   = buy ? anchor - stepPts * _Point : anchor + stepPts * _Point;
      if(buy)
      {
         if(price >= bid - minDist) price = bid - minDist - stepPts * _Point;
         if(price >= bid - minDist) break;
      }
      else
      {
         if(price <= ask + minDist) price = ask + minDist + stepPts * _Point;
         if(price <= ask + minDist) break;
      }
      price = NormalizeDouble(price, _Digits);
      if(!SendPendingAsync(buy ? ORDER_TYPE_BUY_LIMIT : ORDER_TYPE_SELL_LIMIT, lot, price))
      {
         g_nextGridMs = GetTickCount64() + 2000;
         break;
      }
      anchor = price;
      n++;
      sent++;
   }
   if(sent == 0) { g_nextGridMs = GetTickCount64() + 1000; return 0; }

   // attente courte de la confirmation (évite les doublons au passage suivant)
   int expected = st_pendCount + sent;
   ulong t0 = GetTickCount64();
   while(GetTickCount64() - t0 < 1500)
   {
      Sleep(20);
      RefreshState();
      if(st_pendCount >= expected) break;
   }
   g_nextGridMs = GetTickCount64() + ((st_pendCount >= expected) ? 300 : 2500);
   Dbg("Grille : " + IntegerToString(sent) + " ordre(s) envoyé(s), " + IntegerToString(st_pendCount) + " en attente");
   return sent;
}

//+------------------------------------------------------------------+
//| Annulation des ordres en attente (parallèle)                     |
//+------------------------------------------------------------------+
int CancelPendingFast()
{
   int n = 0;
   for(int j = OrdersTotal() - 1; j >= 0; j--)
   {
      ulong t = OrderGetTicket(j);
      if(t == 0) continue;
      if(!IsGridOrder()) continue;                   // [ARCHI] 8.3.1
      MqlTradeRequest rq; MqlTradeResult rs;
      ZeroMemory(rq); ZeroMemory(rs);
      rq.action = TRADE_ACTION_REMOVE;
      rq.order  = t;
      if(OrderSendAsync(rq, rs)) n++;
   }
   return n;
}

//+------------------------------------------------------------------+
//| Fermeture complète : ordres en attente supprimés EN PREMIER      |
//| (plus de remplissage pendant la clôture), puis toutes les        |
//| positions en parallèle. Vérification puis reprise synchrone.     |
//+------------------------------------------------------------------+
int CloseAllFast(string reason)
{
   if(g_closing) return 0;
   // [ARCHI] début : fermeture suspendue (5 s → 60 s) tant que la cause du blocage persiste ; reprise dès qu'elle disparaît
   if(GetTickCount64() < g_closeBlockUntilMs && CloseBlockReason() != "") return 0;
   // [ARCHI] fin
   g_closing = true;
   RefreshState();
   int before = st_posCount + st_pendCount;
   if(before == 0) { g_closing = false; g_closeBlocked = false; g_closeBlockCount = 0; g_closeRetryReason = ""; return 0; }   // [ARCHI] rien à fermer : épisode clos
   Log("Fermeture complète (" + reason + ") : " + IntegerToString(st_posCount) + " position(s), " +
       IntegerToString(st_pendCount) + " ordre(s)");

   for(int round = 0; round < 3; round++)
   {
      RefreshState();
      if(st_posCount == 0 && st_pendCount == 0) break;
      bool async = (round == 0);

      // 1) ordres en attente
      for(int j = OrdersTotal() - 1; j >= 0; j--)
      {
         ulong t = OrderGetTicket(j);
         if(t == 0) continue;
         if(!IsGridOrder()) continue;                // [ARCHI] 8.3.1 : jamais l'ordre d'exécution du TP créé par le serveur
         MqlTradeRequest rq; MqlTradeResult rs;
         ZeroMemory(rq); ZeroMemory(rs);
         rq.action = TRADE_ACTION_REMOVE;
         rq.order  = t;
         bool sentOk = async ? OrderSendAsync(rq, rs) : OrderSend(rq, rs);
         if(!sentOk) Dbg("Suppression d'ordre non envoyée");
      }

      // 2) positions
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong tk = PositionGetTicket(i);
         if(tk == 0) continue;
         if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
         if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
         // [ARCHI] 8.3.1 : TP courtier touché / gelé : le serveur ferme lui-même ; fermeture au marché seulement au dernier tour
         // (TP toujours pas exécuté après ~3 s), pour ne jamais laisser une position ouverte
         if(round < 2 && TPExecuting()) continue;
         ENUM_POSITION_TYPE pt = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
         MqlTradeRequest rq; MqlTradeResult rs;
         ZeroMemory(rq); ZeroMemory(rs);
         rq.action       = TRADE_ACTION_DEAL;
         rq.position     = tk;
         rq.symbol       = _Symbol;
         rq.volume       = PositionGetDouble(POSITION_VOLUME);
         rq.type         = (pt == POSITION_TYPE_BUY) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
         rq.price        = (pt == POSITION_TYPE_BUY) ? bid : ask;
         rq.deviation    = PInt(P_DEVIATION);
         rq.magic        = MagicNumber;
         rq.type_filling = g_fillMkt;
         rq.comment      = "ScalpX4 close";
         if(async) { if(!OrderSendAsync(rq, rs)) Dbg("Clôture asynchrone non envoyée"); }
         else
         {
            bool okSync = OrderSend(rq, rs);
            if(!okSync && IsBlockingRc(rs.retcode)) NoteBrokerBlockRc(rs.retcode);   // [ARCHI]
            if(!okSync && rs.retcode == TRADE_RETCODE_INVALID_FILL)
            {
               g_fillMkt = NextFilling(g_fillMkt);
               rq.type_filling = g_fillMkt;
               if(!OrderSend(rq, rs)) Dbg("Clôture : nouvel essai refusé");
            }
         }
      }

      // 3) attente de la confirmation
      ulong t0 = GetTickCount64();
      while(GetTickCount64() - t0 < 1500)
      {
         Sleep(20);
         RefreshState();
         if(st_posCount == 0 && st_pendCount == 0) break;
      }
   }

   RefreshState();
   int after = st_posCount + st_pendCount;
   g_closing = false;
   g_tpPrice = 0; g_tpSig = -1;
   g_failCount = 0; g_nextTradeMs = 0;
   if(after > 0) Log("⚠️ Fermeture incomplète : " + IntegerToString(after) + " élément(s) restant(s)");
   // [ARCHI] début : blocage (marché fermé / trading désactivé / connexion) → essais espacés ; réussite → épisode clos
   if(after > 0) CloseBlockNote(after, reason);
   else          { CloseBlockClear(); BrokerBlockClear(); g_closeRetryReason = ""; }
   // [ARCHI] fin
   return before - after;
}

//+------------------------------------------------------------------+
//| Gestion du TP commun : une seule source de vérité               |
//| (TP broker posé sur toutes les positions + surveillance EA)      |
//+------------------------------------------------------------------+
void ManageTP()
{
   if(st_posCount == 0) { g_tpPrice = 0; g_tpSig = -1; g_tpWaitMs = 0; return; }   // [ARCHI] 8.3.1

   double sig = st_posCount * 1000000.0 + st_posLots * 10000.0 + st_avg;
   bool recalc = (sig != g_tpSig);
   if(!recalc && st_tpWrong > 0 && GetTickCount64() >= g_nextTpCheckMs) recalc = true;   // auto-réparation
   if(!recalc) return;
   g_nextTpCheckMs = GetTickCount64() + 3000;

   double dist = CalcTPDist();
   g_tpDistPts = dist;
   bool buy = (st_posType == POSITION_TYPE_BUY);
   double tp = NormalizeDouble(buy ? st_avg + dist * _Point : st_avg - dist * _Point, _Digits);
   g_tpPrice = tp;
   g_tpSig   = sig;

   double minD = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   bool valid = buy ? (tp > bid + minD) : (tp < ask - minD);
   if(!valid) return;   // prix déjà au-delà : la sortie sera faite par CheckTPExit()

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      if(MathAbs(PositionGetDouble(POSITION_TP) - tp) <= _Point * 0.5) continue;
      MqlTradeRequest rq; MqlTradeResult rs;
      ZeroMemory(rq); ZeroMemory(rs);
      rq.action   = TRADE_ACTION_SLTP;
      rq.position = tk;
      rq.symbol   = _Symbol;
      rq.sl       = PositionGetDouble(POSITION_SL);
      rq.tp       = tp;
      rq.magic    = MagicNumber;
      if(!OrderSendAsync(rq, rs)) Dbg("Modification du TP non envoyée");
   }
}

bool CheckTPExit()
{
   if(g_tpPrice <= 0 || st_posCount == 0) return false;
   bool hit;
   if(st_posType == POSITION_TYPE_BUY) hit = (SymbolInfoDouble(_Symbol, SYMBOL_BID) >= g_tpPrice);
   else                                hit = (SymbolInfoDouble(_Symbol, SYMBOL_ASK) <= g_tpPrice);
   if(!hit) { g_tpWaitMs = 0; return false; }
   // [ARCHI] début 8.3.1 : TP courtier posé sur TOUTES les positions → c'est le courtier qui ferme. Le robot supprime seulement
   // les ordres de grille et attend (3 s max) ; passé ce délai, fermeture au marché (TP non exécuté : jamais de position oubliée).
   if(st_tpWrong == 0)
   {
      ulong now = GetTickCount64();
      if(g_tpWaitMs == 0)
      {
         g_tpWaitMs = now + 3000;
         Log("TP atteint : fermeture exécutée par le courtier, ordres de grille supprimés");
      }
      if(now < g_tpWaitMs)
      {
         static ulong lastCancel = 0;
         if(st_pendCount > 0 && now - lastCancel > 500) { lastCancel = now; CancelPendingFast(); }
         return true;                                 // aucune entrée tant que la série n'est pas soldée
      }
      Log("TP atteint mais position toujours ouverte après 3 s : fermeture par le robot");
   }
   g_tpWaitMs = 0;
   // [ARCHI] fin
   CloseAllFast("TP atteint");
   return true;
}
//+------------------------------------------------------------------+
