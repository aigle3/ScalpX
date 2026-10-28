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
   ChartRemoveATR();           // l'ATR du graphique référence l'ancien handle
   ReleaseIndicators();
   bool ok = InitIndicators();
   g_tpSig = -1;
   g_lastEmaBar = 0;
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
      if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      if(OrderGetInteger(ORDER_MAGIC) != MagicNumber) continue;
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
   RegisterTradeFailure(type == ORDER_TYPE_BUY ? "ouverture ACHAT" : "ouverture VENTE", lastRc);
   return false;
}

//+------------------------------------------------------------------+
//| Grille : tous les niveaux manquants sont envoyés d'un coup       |
//| (OrderSendAsync) au lieu d'un ordre par seconde.                 |
//+------------------------------------------------------------------+
ulong g_nextGridMs = 0;

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
      if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      if(OrderGetInteger(ORDER_MAGIC) != MagicNumber) continue;
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
   g_closing = true;
   RefreshState();
   int before = st_posCount + st_pendCount;
   if(before == 0) { g_closing = false; return 0; }
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
         if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
         if(OrderGetInteger(ORDER_MAGIC) != MagicNumber) continue;
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
            if(!OrderSend(rq, rs) && rs.retcode == TRADE_RETCODE_INVALID_FILL)
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
   return before - after;
}

//+------------------------------------------------------------------+
//| Gestion du TP commun : une seule source de vérité               |
//| (TP broker posé sur toutes les positions + surveillance EA)      |
//+------------------------------------------------------------------+
void ManageTP()
{
   if(st_posCount == 0) { g_tpPrice = 0; g_tpSig = -1; return; }

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
   if(!hit) return false;
   CloseAllFast("TP atteint");
   return true;
}
//+------------------------------------------------------------------+
