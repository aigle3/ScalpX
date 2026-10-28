//+------------------------------------------------------------------+
//| SX_Chart.mqh - ScalpX v4.0                                       |
//| Graphique : thème sombre épuré (sans grille ni volumes), EMA     |
//| du robot tracées sur le prix, ATR en sous-fenêtre, lignes de     |
//| prix moyen / TP / prochains niveaux de grille.                   |
//| Le style d'origine du graphique est restauré au retrait.         |
//+------------------------------------------------------------------+
#define CH_EMA_BARS  220

#define C_BG       C'11,16,28'
#define C_TEXT     C'148,163,184'
#define C_BULL     C'38,194,129'
#define C_BEAR     C'239,83,80'
#define C_EMA_F    C'0,209,178'
#define C_EMA_S    C'255,159,28'
#define C_AVG      C'90,160,255'

//+------------------------------------------------------------------+
//| Style du graphique                                               |
//+------------------------------------------------------------------+
void ChartSaveOriginal()
{
   if(g_chartStyled) return;
   // Les réglages d'origine survivent aux rechargements du robot (variables globales),
   // sinon on mémoriserait un graphique déjà modifié.
   if(GlobalVariableCheck(GVN("co0")))
   {
      for(int i = 0; i < 22; i++) g_chOrig[i] = (long)GlobalVariableGet(GVN("co" + IntegerToString(i)));
      if(!GlobalVariableCheck(GVN("co21"))) g_chOrig[21] = 1;   // sauvegarde d'une ancienne version : historique affiché
      g_chartStyled = true;
      return;
   }
   g_chOrig[0]  = ChartGetInteger(0, CHART_COLOR_BACKGROUND);
   g_chOrig[1]  = ChartGetInteger(0, CHART_COLOR_FOREGROUND);
   g_chOrig[2]  = ChartGetInteger(0, CHART_SHOW_GRID);
   g_chOrig[3]  = ChartGetInteger(0, CHART_SHOW_VOLUMES);
   g_chOrig[4]  = ChartGetInteger(0, CHART_COLOR_CANDLE_BULL);
   g_chOrig[5]  = ChartGetInteger(0, CHART_COLOR_CANDLE_BEAR);
   g_chOrig[6]  = ChartGetInteger(0, CHART_COLOR_CHART_UP);
   g_chOrig[7]  = ChartGetInteger(0, CHART_COLOR_CHART_DOWN);
   g_chOrig[8]  = ChartGetInteger(0, CHART_COLOR_CHART_LINE);
   g_chOrig[9]  = ChartGetInteger(0, CHART_COLOR_BID);
   g_chOrig[10] = ChartGetInteger(0, CHART_COLOR_ASK);
   g_chOrig[11] = ChartGetInteger(0, CHART_SHOW_ASK_LINE);
   g_chOrig[12] = ChartGetInteger(0, CHART_SHOW_BID_LINE);
   g_chOrig[13] = ChartGetInteger(0, CHART_SHOW_PERIOD_SEP);
   g_chOrig[14] = ChartGetInteger(0, CHART_SHOW_OHLC);
   g_chOrig[15] = ChartGetInteger(0, CHART_SHOW_TRADE_LEVELS);
   g_chOrig[16] = ChartGetInteger(0, CHART_SHOW_OBJECT_DESCR);
   g_chOrig[17] = ChartGetInteger(0, CHART_SHIFT);
   g_chOrig[18] = ChartGetInteger(0, CHART_MODE);
   g_chOrig[19] = ChartGetInteger(0, CHART_AUTOSCROLL);
   g_chOrig[20] = ChartGetInteger(0, CHART_COLOR_GRID);
   g_chOrig[21] = ChartGetInteger(0, CHART_SHOW_TRADE_HISTORY);
   for(int k = 0; k < 22; k++) GlobalVariableSet(GVN("co" + IntegerToString(k)), (double)g_chOrig[k]);
   g_chartStyled = true;
}

void ChartRestoreOriginal()
{
   if(!g_chartStyled)
   {
      if(!GlobalVariableCheck(GVN("co0"))) return;
      for(int i = 0; i < 22; i++) g_chOrig[i] = (long)GlobalVariableGet(GVN("co" + IntegerToString(i)));
      if(!GlobalVariableCheck(GVN("co21"))) g_chOrig[21] = 1;   // sauvegarde d'une ancienne version : historique affiché
   }
   ChartSetInteger(0, CHART_COLOR_BACKGROUND,  g_chOrig[0]);
   ChartSetInteger(0, CHART_COLOR_FOREGROUND,  g_chOrig[1]);
   ChartSetInteger(0, CHART_SHOW_GRID,         g_chOrig[2]);
   ChartSetInteger(0, CHART_SHOW_VOLUMES,      g_chOrig[3]);
   ChartSetInteger(0, CHART_COLOR_CANDLE_BULL, g_chOrig[4]);
   ChartSetInteger(0, CHART_COLOR_CANDLE_BEAR, g_chOrig[5]);
   ChartSetInteger(0, CHART_COLOR_CHART_UP,    g_chOrig[6]);
   ChartSetInteger(0, CHART_COLOR_CHART_DOWN,  g_chOrig[7]);
   ChartSetInteger(0, CHART_COLOR_CHART_LINE,  g_chOrig[8]);
   ChartSetInteger(0, CHART_COLOR_BID,         g_chOrig[9]);
   ChartSetInteger(0, CHART_COLOR_ASK,         g_chOrig[10]);
   ChartSetInteger(0, CHART_SHOW_ASK_LINE,     g_chOrig[11]);
   ChartSetInteger(0, CHART_SHOW_BID_LINE,     g_chOrig[12]);
   ChartSetInteger(0, CHART_SHOW_PERIOD_SEP,   g_chOrig[13]);
   ChartSetInteger(0, CHART_SHOW_OHLC,         g_chOrig[14]);
   ChartSetInteger(0, CHART_SHOW_TRADE_LEVELS, g_chOrig[15]);
   ChartSetInteger(0, CHART_SHOW_OBJECT_DESCR, g_chOrig[16]);
   ChartSetInteger(0, CHART_SHIFT,             g_chOrig[17]);
   ChartSetInteger(0, CHART_MODE,              g_chOrig[18]);
   ChartSetInteger(0, CHART_AUTOSCROLL,        g_chOrig[19]);
   ChartSetInteger(0, CHART_COLOR_GRID,        g_chOrig[20]);
   ChartSetInteger(0, CHART_SHOW_TRADE_HISTORY, g_chOrig[21]);
   for(int k = 0; k < 22; k++) GlobalVariableDel(GVN("co" + IntegerToString(k)));
   g_chartStyled = false;
}

void ChartApplyStyle()
{
   if(!PBool(P_CH_STYLE)) { ChartRestoreOriginal(); return; }
   ChartSaveOriginal();
   ChartSetInteger(0, CHART_MODE,              CHART_CANDLES);
   ChartSetInteger(0, CHART_COLOR_BACKGROUND,  C_BG);
   ChartSetInteger(0, CHART_COLOR_FOREGROUND,  C_TEXT);
   ChartSetInteger(0, CHART_COLOR_GRID,        C'24,32,50');
   ChartSetInteger(0, CHART_SHOW_GRID,         false);
   ChartSetInteger(0, CHART_SHOW_VOLUMES,      CHART_VOLUME_HIDE);
   ChartSetInteger(0, CHART_COLOR_CANDLE_BULL, C_BULL);
   ChartSetInteger(0, CHART_COLOR_CANDLE_BEAR, C_BEAR);
   ChartSetInteger(0, CHART_COLOR_CHART_UP,    C_BULL);
   ChartSetInteger(0, CHART_COLOR_CHART_DOWN,  C_BEAR);
   ChartSetInteger(0, CHART_COLOR_CHART_LINE,  C_TEXT);
   ChartSetInteger(0, CHART_COLOR_BID,         C'70,86,116');
   ChartSetInteger(0, CHART_COLOR_ASK,         C'255,193,7');
   ChartSetInteger(0, CHART_SHOW_ASK_LINE,     true);
   ChartSetInteger(0, CHART_SHOW_BID_LINE,     false);
   ChartSetInteger(0, CHART_SHOW_PERIOD_SEP,   false);
   ChartSetInteger(0, CHART_SHOW_OHLC,         false);
   ChartSetInteger(0, CHART_SHOW_OBJECT_DESCR, true);
   ChartSetInteger(0, CHART_SHOW_TRADE_LEVELS, !PBool(P_CH_LEVELS));   // nos lignes remplacent celles de MT5
   ChartSetInteger(0, CHART_SHOW_TRADE_HISTORY, false);                // l'historique MT5 se dessine par-dessus le panneau
   ChartSetInteger(0, CHART_SHIFT,             true);
   ChartSetInteger(0, CHART_AUTOSCROLL,        true);
}

//+------------------------------------------------------------------+
//| Outils objets                                                    |
//+------------------------------------------------------------------+
void ObjDel(string name)
{
   string nm = SX_PREFIX + name;
   if(ObjectFind(0, nm) >= 0) ObjectDelete(0, nm);
}

void DrawSeg(string pre, int idx, datetime t1, double p1, datetime t2, double p2, color col, int width)
{
   string nm = SX_PREFIX + pre + IntegerToString(idx);
   if(ObjectFind(0, nm) < 0)
   {
      if(!ObjectCreate(0, nm, OBJ_TREND, 0, t1, p1, t2, p2)) return;
      ObjectSetInteger(0, nm, OBJPROP_RAY_RIGHT, false);
      ObjectSetInteger(0, nm, OBJPROP_RAY_LEFT, false);
      ObjectSetInteger(0, nm, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, nm, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, nm, OBJPROP_BACK, false);
      ObjectSetInteger(0, nm, OBJPROP_WIDTH, width);
      ObjectSetInteger(0, nm, OBJPROP_COLOR, col);
   }
   else
   {
      ObjectMove(0, nm, 0, t1, p1);
      ObjectMove(0, nm, 1, t2, p2);
   }
}

void ChartClearEMAs()
{
   for(int i = 0; i < CH_EMA_BARS; i++) { ObjDel("EF" + IntegerToString(i)); ObjDel("ES" + IntegerToString(i)); }
   g_lastEmaBar = 0;
}

//+------------------------------------------------------------------+
//| EMA tracées sur le graphique (indépendantes du timeframe affiché)|
//+------------------------------------------------------------------+
void ChartDrawEMAs(bool force)
{
   if(!PBool(P_CH_EMA)) { if(g_lastEmaBar != 0) ChartClearEMAs(); return; }
   if(g_hFast == INVALID_HANDLE || g_hSlow == INVALID_HANDLE) return;

   datetime t[];
   double f[], s[];
   ArraySetAsSeries(t, true); ArraySetAsSeries(f, true); ArraySetAsSeries(s, true);
   int got = CopyTime(_Symbol, g_sigTF, 0, CH_EMA_BARS + 1, t);
   if(got < 3) return;
   if(CopyBuffer(g_hFast, 0, 0, got, f) != got) return;
   if(CopyBuffer(g_hSlow, 0, 0, got, s) != got) return;

   bool newBar = (t[0] != g_lastEmaBar);
   if(!force && !newBar)
   {
      // seul le segment de la bougie en cours bouge
      if(f[0] != 0 && f[1] != 0) DrawSeg("EF", 0, t[1], f[1], t[0], f[0], C_EMA_F, 2);
      if(s[0] != 0 && s[1] != 0) DrawSeg("ES", 0, t[1], s[1], t[0], s[0], C_EMA_S, 2);
      return;
   }
   for(int i = 0; i < got - 1; i++)
   {
      if(f[i] != 0 && f[i + 1] != 0) DrawSeg("EF", i, t[i + 1], f[i + 1], t[i], f[i], C_EMA_F, 2);
      if(s[i] != 0 && s[i + 1] != 0) DrawSeg("ES", i, t[i + 1], s[i + 1], t[i], s[i], C_EMA_S, 2);
   }
   g_lastEmaBar = t[0];
}

//+------------------------------------------------------------------+
//| ATR dans une sous-fenêtre (si le graphique est au même TF)       |
//+------------------------------------------------------------------+
void ChartRemoveATR()
{
   if(g_atrIndName != "")
   {
      ChartIndicatorDelete(0, g_atrSubWin, g_atrIndName);
      g_atrIndName = "";
      g_atrSubWin = -1;
   }
}

void ChartEnsureATR()
{
   // [ARCHI] début : sous-fenêtre ATR supprimée (affichage seulement ; l'ATR reste calculé pour la grille).
   // Retire aussi une sous-fenêtre ATR restée sur le graphique d'une version précédente.
   ChartRemoveATR();
   for(int w = (int)ChartGetInteger(0, CHART_WINDOWS_TOTAL) - 1; w >= 1; w--)
      for(int k = ChartIndicatorsTotal(0, w) - 1; k >= 0; k--)
      {
         string nm = ChartIndicatorName(0, w, k);
         if(StringFind(nm, "ATR") == 0) ChartIndicatorDelete(0, w, nm);
      }
   return;
   // [ARCHI] fin
   if(!PBool(P_CH_ATR) || ChartPeriod(0) != g_sigTF || g_hATR == INVALID_HANDLE) { ChartRemoveATR(); return; }
   if(g_atrIndName != "") return;
   int sub = (int)ChartGetInteger(0, CHART_WINDOWS_TOTAL);
   if(!ChartIndicatorAdd(0, sub, g_hATR)) { Dbg("ATR : ajout au graphique impossible (code " + IntegerToString(GetLastError()) + ")"); return; }
   int tot = ChartIndicatorsTotal(0, sub);
   if(tot > 0) { g_atrIndName = ChartIndicatorName(0, sub, tot - 1); g_atrSubWin = sub; }
}

//+------------------------------------------------------------------+
//| Niveaux : prix moyen, TP, prochains ordres de grille             |
//+------------------------------------------------------------------+
void HLine(string name, double price, color col, ENUM_LINE_STYLE style, int width, string txt)
{
   string nm = SX_PREFIX + name;
   if(ObjectFind(0, nm) < 0)
   {
      if(!ObjectCreate(0, nm, OBJ_HLINE, 0, 0, price)) return;
      ObjectSetInteger(0, nm, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, nm, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, nm, OBJPROP_BACK, true);
   }
   if(MathAbs(ObjectGetDouble(0, nm, OBJPROP_PRICE) - price) > _Point * 0.5) ObjectSetDouble(0, nm, OBJPROP_PRICE, price);
   ObjectSetInteger(0, nm, OBJPROP_COLOR, col);
   ObjectSetInteger(0, nm, OBJPROP_STYLE, style);
   ObjectSetInteger(0, nm, OBJPROP_WIDTH, width);
   if(ObjectGetString(0, nm, OBJPROP_TEXT) != txt) ObjectSetString(0, nm, OBJPROP_TEXT, txt);
}

void ChartClearLevels()
{
   ObjDel("L_AVG"); ObjDel("L_TP");
   for(int i = 0; i < 6; i++) ObjDel("L_G" + IntegerToString(i));
}

void ChartDrawLevels()
{
   if(!PBool(P_CH_LEVELS) || st_posCount == 0) { ChartClearLevels(); return; }
   bool buy = (st_posType == POSITION_TYPE_BUY);
   HLine("L_AVG", st_avg, C_AVG, STYLE_DOT, 1, "Prix moyen " + PX(st_avg) + "  (" + F2(st_posLots) + " lot)");
   if(g_tpPrice > 0)
      HLine("L_TP", g_tpPrice, C_BULL, STYLE_SOLID, 1, "TP " + PX(g_tpPrice) + "  (" + F0(g_tpDistPts) + " pts)");
   else ObjDel("L_TP");

   // prochains niveaux de grille (les 6 plus proches du prix)
   double lv[];
   int n = 0;
   for(int j = 0; j < OrdersTotal(); j++)
   {
      ulong t = OrderGetTicket(j);
      if(t == 0) continue;
      if(OrderGetString(ORDER_SYMBOL) != _Symbol || OrderGetInteger(ORDER_MAGIC) != MagicNumber) continue;
      ArrayResize(lv, n + 1);
      lv[n++] = OrderGetDouble(ORDER_PRICE_OPEN);
   }
   double ref = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   for(int a = 1; a < n; a++)                       // tri par distance au prix
   {
      double v = lv[a];
      int b = a - 1;
      while(b >= 0 && MathAbs(lv[b] - ref) > MathAbs(v - ref)) { lv[b + 1] = lv[b]; b--; }
      lv[b + 1] = v;
   }
   for(int i = 0; i < 6; i++)
   {
      if(i < n) HLine("L_G" + IntegerToString(i), lv[i], C'96,110,135', STYLE_DOT, 1, (i == 0 ? "Grille ▸ " : "") + PX(lv[i]));
      else ObjDel("L_G" + IntegerToString(i));
   }
}

//+------------------------------------------------------------------+
//| Rafraîchissement global                                          |
//+------------------------------------------------------------------+
void ChartRefreshAll(bool full)
{
   if(full)
   {
      ChartApplyStyle();
      ChartRemoveATR();
      ChartEnsureATR();
      g_lastEmaBar = 0;
   }
   ChartDrawEMAs(true);
   ChartDrawLevels();
   if(!PBool(P_CH_PANEL)) PanelClear();
   ChartRedraw(0);
}

void ChartShutdown(bool restoreStyle)
{
   ChartRemoveATR();
   ObjectsDeleteAll(0, SX_PREFIX);
   if(restoreStyle) ChartRestoreOriginal();
   ChartRedraw(0);
}
//+------------------------------------------------------------------+