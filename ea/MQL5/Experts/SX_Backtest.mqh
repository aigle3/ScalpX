//+------------------------------------------------------------------+
//| SX_Backtest.mqh - ScalpX 7.2.0                                   |
//| [ARCHI] BACKTEST PILOTÉ PAR LE BOT (testeur de stratégie MT5).   |
//|                                                                  |
//| Actif seulement dans le testeur avec l'input BacktestId. Le      |
//| robot y joue la VRAIE stratégie : créneaux, objectif, fenêtre    |
//| d'entrée, coupures, seuil 500 (retrait simulé jusqu'au seuil de  |
//| reprise), filtres dont News (calendrier exporté par les comptes  |
//| réels), labo des filtres (scénarios virtuels). Spread : ticks    |
//| réels du broker. Commission / slippage du labo : mesurés en réel.|
//|                                                                  |
//| Fichiers (Common\Files) :                                        |
//|   bt_<id>_params.cfg     réglages (clé=valeur), écrit par le bot |
//|   events_bt_<id>.jsonl   événements (mêmes types qu'en réel)     |
//|   bt_<id>_progress.json  avancement (toutes les 3 s réelles)     |
//|   bt_<id>_fin.json       résultat final                          |
//|   calendrier_<SYMBOLE>.csv  news (écrit 1 fois/jour par l'EA réel)|
//+------------------------------------------------------------------+
//--- compteurs (progression et résultat)
int      g_btGoals = 0, g_btWon = 0, g_btCancel = 0, g_btCut = 0, g_btSL = 0, g_btSkip = 0, g_btReloads = 0, g_btEvents = 0;
double   g_btWd = 0, g_btDep = 0, g_btPeak = 0, g_btMaxDD = 0, g_btStart = 0;
datetime g_btLastSec = 0;
int      g_btLastDay = -1;
ulong    g_btProgMs = 0;
string   g_btNote = "";
//--- calendrier (heures GMT, triées)
datetime g_bcT[];
string   g_bcCur[], g_bcName[];
int      g_bcImp[];
ulong    g_bcId[];
int      g_bcN = 0, g_bcPos = 0;
datetime g_bcScan = 0;

string BtFile(string kind) { return "bt_" + BacktestId + "_" + kind; }
string BtCalFile() { return "calendrier_" + _Symbol + ".csv"; }

// Appelé par EvTyped (backtest seulement)
void BtCount(const string type, const string text)
{
   g_btEvents++;
   if(type == "OBJECTIF_AUTO") g_btGoals++;
   else if(type == "OBJECTIF_ATTEINT") g_btWon++;
   else if(type == "OBJECTIF_ANNULE") g_btCancel++;
   else if(type == "OBJECTIF_COUPE") g_btCut++;
   else if(type == "CRENEAU_SAUTE") g_btSkip++;
   else if(type == "NOTIF" && StringFind(text, "STOP LOSS D'URGENCE") >= 0) g_btSL++;
}

//+------------------------------------------------------------------+
//| Calendrier : export (EA réel) et lecture (backtest)              |
//+------------------------------------------------------------------+
void BtCalendarExport()
{
   string base = SymbolInfoString(_Symbol, SYMBOL_CURRENCY_BASE), prof = SymbolInfoString(_Symbol, SYMBOL_CURRENCY_PROFIT);
   string curs[2];
   curs[0] = base; curs[1] = prof;
   int nc = (base == prof || base == "") ? 1 : 2;
   if(base == "") curs[0] = prof;
   datetime srvNow = TimeTradeServer();
   int off = (int)MathRound((double)(srvNow - SxRealGmt()) / 900.0) * 900;
   int winter = off - (TzUsDst(SxRealGmt()) ? 3600 : 0);
   string tmp = BtCalFile() + ".tmp";
   int h = FileOpen(tmp, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON, 0, CP_UTF8);
   if(h == INVALID_HANDLE) return;
   FileWriteString(h, "#hiver_s=" + IntegerToString(winter) + "\n");
   int n = 0;
   for(int c = 0; c < nc; c++)
   {
      MqlCalendarValue vals[];
      int m = CalendarValueHistory(vals, srvNow - 400 * 86400, srvNow + 86400, NULL, curs[c]);
      for(int j = 0; j < m; j++)
      {
         MqlCalendarEvent ev;
         if(!CalendarEventById(vals[j].event_id, ev)) continue;
         if((int)ev.importance < (int)CALENDAR_IMPORTANCE_MODERATE) continue;
         if(ev.time_mode != CALENDAR_TIMEMODE_DATETIME) continue;
         datetime srv = vals[j].time;
         datetime utc = (datetime)((long)srv - winter);
         datetime gmt = (datetime)((long)srv - winter - (TzUsDst(utc) ? 3600 : 0));
         string nm = ev.name;
         StringReplace(nm, ";", ",");
         StringReplace(nm, "\n", " ");
         FileWriteString(h, IntegerToString((long)gmt) + ";" + curs[c] + ";" + IntegerToString((int)ev.importance) + ";" +
                            IntegerToString((long)vals[j].id) + ";" + nm + "\n");
         n++;
      }
   }
   FileClose(h);
   if(!FileMove(tmp, FILE_COMMON, BtCalFile(), FILE_COMMON | FILE_REWRITE)) { FileDelete(tmp, FILE_COMMON); return; }
   Log("Calendrier exporté pour les backtests : " + IntegerToString(n) + " annonces (" + BtCalFile() + ")");
}

// EA réel, 1 Hz : export du calendrier une fois par jour (le testeur n'a pas accès au calendrier économique)
void BtLiveTick()
{
   if(!g_archiOn) return;
   static ulong last = 0;
   ulong now = SxRealMs();
   if(last == 0)                                     // 7.4.1 : premier contrôle 5 à 60 min après le démarrage, décalé par compte
   {                                                 // (après une mise à jour, les EA ne lisent pas tous le calendrier en même temps)
      last = now - 3600000 + 300000 + (ulong)(AccountInfoInteger(ACCOUNT_LOGIN) % 55) * 60000;
      return;
   }
   if(now - last < 3600000) return;
   last = now;
   long mod = FileGetInteger(BtCalFile(), FILE_MODIFY_DATE, true);
   if(mod > 0 && TimeLocal() - (datetime)mod < 20 * 3600) return;
   BtCalendarExport();
}

void BtLoadCalendar()
{
   g_bcN = 0; g_bcPos = 0;
   int h = FileOpen(BtCalFile(), FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ, 0, CP_UTF8);
   if(h == INVALID_HANDLE) { g_btNote = "calendrier des news absent (aucun compte réel ne l'a encore exporté) : filtre News inactif"; return; }
   while(!FileIsEnding(h))
   {
      string l = FileReadString(h);
      if(StringFind(l, "#hiver_s=") == 0)
      {
         if(BtGmtHiver == -99) g_btWinter = (int)StringToInteger(StringSubstr(l, 9));
         continue;
      }
      string f[];
      if(StringSplit(l, ';', f) < 5) continue;
      datetime t = (datetime)StringToInteger(f[0]);
      int pos = g_bcN;
      ArrayResize(g_bcT, g_bcN + 1, 4096); ArrayResize(g_bcCur, g_bcN + 1, 4096); ArrayResize(g_bcName, g_bcN + 1, 4096);
      ArrayResize(g_bcImp, g_bcN + 1, 4096); ArrayResize(g_bcId, g_bcN + 1, 4096);
      while(pos > 0 && g_bcT[pos - 1] > t)            // tri par heure (insertion)
      {
         g_bcT[pos] = g_bcT[pos - 1]; g_bcCur[pos] = g_bcCur[pos - 1]; g_bcName[pos] = g_bcName[pos - 1];
         g_bcImp[pos] = g_bcImp[pos - 1]; g_bcId[pos] = g_bcId[pos - 1];
         pos--;
      }
      g_bcT[pos] = t; g_bcCur[pos] = f[1]; g_bcImp[pos] = (int)StringToInteger(f[2]); g_bcId[pos] = (ulong)StringToInteger(f[3]); g_bcName[pos] = f[4];
      g_bcN++;
   }
   FileClose(h);
   if(g_bcN == 0) g_btNote = "calendrier des news vide : filtre News inactif";
}

// Remplit la liste des news de l'EA (g_nw*, heures SERVEUR) comme NewsScan, depuis le calendrier exporté
void BtNewsScan()
{
   datetime gmt = TimeGMT();
   g_nwN = 0;
   int minImp = (PInt(P_NEWS_IMP) == 0) ? (int)CALENDAR_IMPORTANCE_HIGH : (int)CALENDAR_IMPORTANCE_MODERATE;
   while(g_bcPos < g_bcN && g_bcT[g_bcPos] < gmt - 3 * 3600) g_bcPos++;
   int off = BtSrvOffset(TimeCurrent());
   for(int i = g_bcPos; i < g_bcN && g_nwN < 40; i++)
   {
      if(g_bcT[i] > gmt + 24 * 3600) break;
      if(g_bcT[i] < gmt - 20 * 60 - PInt(P_NEWS_AFTER) * 60) continue;
      if(g_bcImp[i] < minImp) continue;
      g_nwT[g_nwN] = (datetime)((long)g_bcT[i] + off);
      g_nwName[g_nwN] = g_bcName[i]; g_nwCur[g_nwN] = g_bcCur[i]; g_nwImp[g_nwN] = g_bcImp[i]; g_nwId[g_nwN] = g_bcId[i];
      g_nwN++;
   }
   g_nwOK = (g_bcN > 0);
}

// Comme NewsTick, sans notification
void BtNewsTick()
{
   datetime now = TimeCurrent();
   if(g_bcScan == 0 || now - g_bcScan >= 300) { g_bcScan = now; BtNewsScan(); }
   datetime srv = TimeTradeServer();
   bool blk = false;
   string why = "";
   int before = PInt(P_NEWS_BEFORE) * 60, after = PInt(P_NEWS_AFTER) * 60;
   if(PBool(P_NEWS_BLOCK))
      for(int i = 0; i < g_nwN; i++)
      {
         int dt = (int)(g_nwT[i] - srv);
         if(dt <= before && dt >= -after) { blk = true; why = g_nwCur[i] + " " + g_nwName[i]; break; }
      }
   g_newsBlock = blk;
   g_newsWhy = why;
}

//+------------------------------------------------------------------+
//| Init / tick / fin                                                |
//+------------------------------------------------------------------+
bool BtPreInit()
{
   g_bt = (MQLInfoInteger(MQL_TESTER) != 0 && BacktestId != "");
   if(!g_bt) return false;
   g_aid = "bt_" + BacktestId;
   g_btUsDst = BtHeureEteUS;
   if(BtGmtHiver != -99) g_btWinter = BtGmtHiver * 3600;
   return true;
}

// Réglages du compte de référence (clé=valeur brute, comme la commande PARAMS) : appliqués par-dessus les inputs
int BtLoadParams()
{
   int h = FileOpen(BtFile("params.cfg"), FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ);
   if(h == INVALID_HANDLE) return -1;
   int n = 0;
   while(!FileIsEnding(h))
   {
      string line = FileReadString(h);
      int p = StringFind(line, "=");
      if(p <= 0) continue;
      int id = FindParamByKey(StringSubstr(line, 0, p));
      if(id < 0) continue;
      double v = StringToDouble(StringSubstr(line, p + 1));
      if(v < g_pMin[id]) v = g_pMin[id];
      if(v > g_pMax[id]) v = g_pMax[id];
      g_pVal[id] = v;
      n++;
   }
   FileClose(h);
   return n;
}

// Après InitParams (OnInit) : réglages, état, créneaux, calendrier
void BtInit()
{
   if(!g_bt) return;
   int n = BtLoadParams();
   if(n < 0) g_btNote = "réglages du compte introuvables : valeurs par défaut de l'EA";
   if(g_pVal[P_FAST] >= g_pVal[P_SLOW]) { g_pVal[P_FAST] = g_pDef[P_FAST]; g_pVal[P_SLOW] = g_pDef[P_SLOW]; }
   RebuildIndicators();                              // timeframes / périodes des réglages chargés
   g_configOK = true;
   g_state = ST_ACTIF;
   g_stoppedManually = false;
   SlotsParse();
   BtLoadCalendar();
   g_btStart = AccountInfoDouble(ACCOUNT_BALANCE);
   g_btPeak = AccountInfoDouble(ACCOUNT_EQUITY);
   Log("Backtest " + BacktestId + " : " + IntegerToString(MathMax(n, 0)) + " réglages chargés, " + IntegerToString(g_slotsN) +
       " créneaux, " + IntegerToString(g_bcN) + " news" + (g_btNote != "" ? " (" + g_btNote + ")" : ""));
   EvTyped("BT_DEBUT", 0, "", "{\"reglages\":" + IntegerToString(n) + ",\"creneaux\":" + IntegerToString(g_slotsN) + ",\"news\":" +
           IntegerToString(g_bcN) + ",\"note\":\"" + JsonEsc(g_btNote) + "\",\"solde\":" + NumS(g_btStart) + ",\"version\":\"" + SX_VERSION + "\"}");
}

// Retrait simulé au seuil 500 (le robot reprend ensuite au seuil de reprise) ; recharge si le solde passe sous la reprise
void BtCash()
{
   if(st_posCount > 0 || g_phase500) return;
   double bal = AccountInfoDouble(ACCOUNT_BALANCE), rep = PDbl(P_SEUIL_REPRISE);
   if(PBool(P_SEUIL_ON) && g_state == ST_ARRET && bal + 0.005 >= PDbl(P_SEUIL_ARRET))
   {
      double w = NormalizeDouble(bal - rep, 2);
      if(w > 0 && TesterWithdrawal(w))
      {
         g_btWd += w;
         g_btPeak = AccountInfoDouble(ACCOUNT_EQUITY);
         EvTyped("BT_RETRAIT", 0, "", "{\"montant\":" + NumS(w) + ",\"solde\":" + NumS(bal - w) + "}");
      }
      return;
   }
   if(bal + 0.005 < rep && !g_goalActive && st_pendCount == 0)
   {
      double d = NormalizeDouble(rep - bal, 2);
      if(d > 0 && TesterDeposit(d))
      {
         g_btDep += d;
         g_btReloads++;
         g_btPeak = AccountInfoDouble(ACCOUNT_EQUITY);
         EvTyped("BT_RECHARGE", 0, "", "{\"montant\":" + NumS(d) + ",\"solde\":" + NumS(bal + d) + "}");
      }
   }
}

string BtJson(bool fin, int reason)
{
   double bal = AccountInfoDouble(ACCOUNT_BALANCE), eq = AccountInfoDouble(ACCOUNT_EQUITY);
   return "{\"id\":\"" + BacktestId + "\",\"sim\":" + IntegerToString((long)TimeGMT()) + ",\"solde\":" + NumS(bal) + ",\"equity\":" + NumS(eq) +
          ",\"depart\":" + NumS(g_btStart) + ",\"objectifs\":" + IntegerToString(g_btGoals) + ",\"atteints\":" + IntegerToString(g_btWon) +
          ",\"annules\":" + IntegerToString(g_btCancel) + ",\"coupes\":" + IntegerToString(g_btCut) + ",\"sl\":" + IntegerToString(g_btSL) +
          ",\"sautes\":" + IntegerToString(g_btSkip) + ",\"cycles\":" + IntegerToString(g_cycle) + ",\"retraits\":" + NumS(g_btWd) +
          ",\"recharges\":" + NumS(g_btDep) + ",\"nb_recharges\":" + IntegerToString(g_btReloads) + ",\"dd_max\":" + NumS(g_btMaxDD) +
          ",\"positions\":" + IntegerToString(st_posCount) + ",\"objectif_actif\":" + BoolS(g_goalActive) +
          ",\"evenements\":" + IntegerToString(g_btEvents) + ",\"note\":\"" + JsonEsc(g_btNote) + "\"" +
          ",\"fin\":" + BoolS(fin) + (fin ? ",\"raison\":" + IntegerToString(reason) : "") + ",\"version\":\"" + SX_VERSION + "\"}";
}

void BtProgress(bool force = false)
{
   ulong now = SxRealMs();
   if(!force && g_btProgMs != 0 && now - g_btProgMs < 3000) return;
   g_btProgMs = now;
   AFileWrite(BtFile("progress.json"), BtJson(false, 0));
}

// Une fois par seconde SIMULÉE (depuis OnTick) : ce que fait OnTimer en réel
void BtSecond()
{
   RefreshState();
   UpdateFilters();
   string why;
   g_blockReason = EntryAllowed(why) ? "" : why;
   BtNewsTick();
   LaboTimer1Hz();
   SlotTick();
   SlotEntryTick();
   CutTick();
   BtCash();
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   if(eq > g_btPeak) g_btPeak = eq;
   if(g_btPeak > 0) { double dd = (g_btPeak - eq) / g_btPeak * 100.0; if(dd > g_btMaxDD) g_btMaxDD = dd; }
   MqlDateTime d;
   TimeToStruct(TimeGMT(), d);
   if(d.day_of_year != g_btLastDay)                  // un point par jour simulé (courbe du solde)
   {
      g_btLastDay = d.day_of_year;
      EvTyped("BT_JOUR", 0, "", "{\"solde\":" + NumS(AccountInfoDouble(ACCOUNT_BALANCE)) + ",\"equity\":" + NumS(eq) + "}");
   }
   BtProgress();
}

// Début de OnTick (backtest seulement)
void BtTick()
{
   datetime t = TimeCurrent();
   if(t == g_btLastSec) return;
   g_btLastSec = t;
   BtSecond();
}

void BtDeinit(int reason)
{
   if(!g_bt) return;
   EvFlushPending();
   AFileWrite(BtFile("progress.json"), BtJson(false, 0));
   AFileWrite(BtFile("fin.json"), BtJson(true, reason));
}
//+------------------------------------------------------------------+
