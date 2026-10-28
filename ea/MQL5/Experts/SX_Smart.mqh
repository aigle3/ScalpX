//+------------------------------------------------------------------+
//| SX_Smart.mqh - ScalpX                                            |
//| Alertes (priorités), watchdog, spread/swap, news, pause          |
//| programmée, mode vacances, captures d'écran / courbe d'equity.   |
//+------------------------------------------------------------------+

// prio 0 = information (silencieuse), 2 = critique (toujours sonore)
void TgInfo(string text) { TgQueueP(text, 0); }
void TgCrit(string text) { TgQueueP(text, 2); }

//+------------------------------------------------------------------+
//| Pause programmée                                                 |
//+------------------------------------------------------------------+
// "2h", "2h30", "45m", "90" (minutes) = durée ; "14:30" = jusqu'à cette heure (aujourd'hui ou demain)
datetime ParsePauseArg(string a)
{
   StringToLower(a);
   StringTrimLeft(a); StringTrimRight(a);
   if(a == "") return 0;
   datetime now = LocalNow();
   int p = StringFind(a, ":");
   if(p > 0)
   {
      int hh = (int)StringToInteger(StringSubstr(a, 0, p));
      int mm = (int)StringToInteger(StringSubstr(a, p + 1));
      if(hh < 0 || hh > 23 || mm < 0 || mm > 59) return 0;
      MqlDateTime d;
      TimeToStruct(now, d);
      d.hour = hh; d.min = mm; d.sec = 0;
      datetime t = StructToTime(d);
      if(t <= now) t += 86400;
      return t;
   }
   int ph = StringFind(a, "h");
   if(ph > 0)
   {
      int h = (int)StringToInteger(StringSubstr(a, 0, ph));
      int m = (int)StringToInteger(StringSubstr(a, ph + 1));
      if(h < 0 || h > 72 || m < 0 || m > 59 || (h == 0 && m == 0)) return 0;
      return now + h * 3600 + m * 60;
   }
   int pm = StringFind(a, "m");
   string num = (pm > 0) ? StringSubstr(a, 0, pm) : a;
   int mins = (int)StringToInteger(num);
   if(mins <= 0 || mins > 4320) return 0;
   return now + mins * 60;
}

datetime NextLocalHour(int hour)
{
   MqlDateTime d;
   TimeToStruct(LocalNow(), d);
   d.hour = hour; d.min = 0; d.sec = 0;
   datetime t = StructToTime(d);
   if(t <= LocalNow()) t += 86400;
   return t;
}

void PauseInit()
{
   g_pauseUntil = GlobalVariableCheck(GVN("pzuntil")) ? (datetime)(long)GlobalVariableGet(GVN("pzuntil")) : 0;
   if(g_pauseUntil > LocalNow()) g_enabled = false;
   else if(g_pauseUntil != 0 && LocalNow() - g_pauseUntil > 12 * 3600) { g_pauseUntil = 0; GlobalVariableDel(GVN("pzuntil")); }
   // échue pendant un arrêt du terminal (moins de 12 h) : PauseTick fait la reprise au premier passage
}

// Fin de la pause programmée (commande PAUSE 2h / 14:30) : même effet que REPRISE
void PauseTick()
{
   if(g_pauseUntil == 0) return;
   if(g_enabled || g_state != ST_PAUSE) { g_pauseUntil = 0; GlobalVariableDel(GVN("pzuntil")); return; }   // repris à la main
   if(LocalNow() < g_pauseUntil) return;
   string msg;
   bool ok = CmdReprise(msg);
   EvTyped("FIN_PAUSE", 1, "▶️ FIN DE LA PAUSE PROGRAMMÉE\n━━━━━━━━━━━━━━━━━━\n" + msg, "{\"ok\":" + BoolS(ok) + "}");
}

//+------------------------------------------------------------------+
//| Mode vacances                                                    |
//+------------------------------------------------------------------+
void VacationInit() { g_vacation = (GlobalVariableCheck(GVN("vac")) && GlobalVariableGet(GVN("vac")) > 0); }

void VacationSet(bool on)
{
   RefreshState();
   g_vacation = on;
   GlobalVariableSet(GVN("vac"), on ? 1.0 : 0.0);
   string s;
   if(on)
   {
      if(st_posCount == 0) CancelPendingFast();
      s = "🏖 MODE VACANCES ACTIVÉ\n━━━━━━━━━━━━━━━━━━\n" +
          "• Aucune nouvelle série ne démarre\n• La grille n'est plus étendue\n• Les positions ouvertes restent gérées jusqu'à leur TP\n" +
          "• Plafond de risque : tout est fermé si la perte flottante dépasse " + F1(PDbl(P_VAC_RISK)) + " % de la balance";
   }
   else
      s = "🏠 MODE VACANCES DÉSACTIVÉ\n━━━━━━━━━━━━━━━━━━\nLe robot reprend son fonctionnement normal.";
   TgQueue(s);
}

void VacationTick()
{
   if(!g_vacation || g_closing || st_posCount == 0) return;
   double lim = PDbl(P_VAC_RISK);
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   if(lim <= 0 || bal <= 0) return;
   if(st_floating <= -bal * lim / 100.0)
   {
      double fl = st_floating;
      g_enabled = false;
      g_pauseUntil = 0;
      int n = CloseAllFast("mode vacances : plafond de risque");
      TgCrit("🚨 MODE VACANCES — PLAFOND DE RISQUE ATTEINT\n━━━━━━━━━━━━━━━━━━\n📉 Flottant : " + Sgn(fl) + " " + Cur() + " (limite " + F1(lim) + " %)\n🔒 " + IntegerToString(n) +
             " élément(s) fermé(s)\n⏸ Robot en pause.");
   }
}

//+------------------------------------------------------------------+
//| Watchdog (connexion, AutoTrading, ticks, marge)                  |
//+------------------------------------------------------------------+
ulong g_wdDownSince = 0, g_wdAtSince = 0, g_wdSpSince = 0;
bool  g_wdDownSent = false, g_wdAtSent = false, g_wdTickSent = false, g_wdMarginSent = false, g_wdSpSent = false;
int   g_swapDay = 0;

void WatchdogTick()
{
   if(!PBool(P_N_WATCHDOG)) return;
   ulong now = GetTickCount64();

   //--- connexion au broker
   bool conn = (TerminalInfoInteger(TERMINAL_CONNECTED) != 0);
   if(!conn)
   {
      if(g_wdDownSince == 0) g_wdDownSince = now;
      if(!g_wdDownSent && now - g_wdDownSince >= 30000)
      {
         g_wdDownSent = true;
         EvTyped("CONNEXION_PERDUE", 2, "🔌 CONNEXION BROKER PERDUE\n━━━━━━━━━━━━━━━━━━\nMT5 n'est plus connecté au serveur de trading.\n📊 " + IntegerToString(st_posCount) +
                " position(s) ouverte(s) — les ordres restent chez le broker mais le robot ne peut plus agir.");
      }
   }
   else
   {
      if(g_wdDownSent)
         EvTyped("CONNEXION_RETABLIE", 2, "✅ CONNEXION RÉTABLIE\n━━━━━━━━━━━━━━━━━━\nMT5 est de nouveau connecté (coupure d'environ " + FmtDur((double)(now - g_wdDownSince) / 1000.0) + ").");
      g_wdDownSince = 0;
      g_wdDownSent = false;
   }

   //--- AutoTrading
   bool at = (TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) != 0) && (MQLInfoInteger(MQL_TRADE_ALLOWED) != 0);
   if(g_enabled && !at)
   {
      if(g_wdAtSince == 0) g_wdAtSince = now;
      if(!g_wdAtSent && now - g_wdAtSince >= 20000)
      {
         g_wdAtSent = true;
         TgCrit("⛔ AUTOTRADING DÉSACTIVÉ\n━━━━━━━━━━━━━━━━━━\nLe robot est actif mais MT5 refuse de trader.\n👉 Active le bouton « Algo Trading » dans MT5.");
      }
   }
   else
   {
      if(g_wdAtSent && at) TgQueueP("✅ AutoTrading de nouveau actif.", 1);
      g_wdAtSince = 0;
      g_wdAtSent = false;
   }

   //--- ticks absents alors que le marché est ouvert
   if(conn && f_marketOpen && g_lastTickMs > 0 && g_enabled)
   {
      ulong gap = now - g_lastTickMs;
      if(gap > 180000 && !g_wdTickSent)
      {
         g_wdTickSent = true;
         TgQueueP("⚠️ AUCUN TICK DEPUIS " + FmtDur((double)gap / 1000.0) + "\n━━━━━━━━━━━━━━━━━━\nLe marché est censé être ouvert : flux de prix gelé ou symbole sans cotation ?", 1);
      }
      if(gap < 20000 && g_wdTickSent) { g_wdTickSent = false; TgQueueP("✅ Les ticks reprennent.", 0); }
   }
   else if(conn && g_wdTickSent) g_wdTickSent = false;

   //--- marge faible
   if(PBool(P_N_MARKET) && st_posCount > 0)
   {
      double ml = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);
      if(ml > 0 && ml < 150 && !g_wdMarginSent)
      {
         g_wdMarginSent = true;
         TgCrit("🚨 MARGE FAIBLE\n━━━━━━━━━━━━━━━━━━\nNiveau de marge : " + F0(ml) + " %\n💰 Flottant : " + Sgn(st_floating) + " " + Cur() + "\n⚠️ Risque d'appel de marge : réduis l'exposition.");
      }
      if(ml > 250) g_wdMarginSent = false;
   }
}

double SwapTotal()
{
   double s = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      s += PositionGetDouble(POSITION_SWAP);
   }
   return s;
}

//--- Spread trop large + rollover (swap)
void MarketAlertTick()
{
   if(!PBool(P_N_MARKET)) return;
   ulong now = GetTickCount64();
   int maxSp = PInt(P_MAXSPREAD);
   if(f_marketOpen && g_enabled && mk_spread > maxSp)
   {
      if(g_wdSpSince == 0) g_wdSpSince = now;
      if(!g_wdSpSent && now - g_wdSpSince >= 60000)
      {
         g_wdSpSent = true;
         TgInfo("📶 SPREAD ÉLEVÉ\nSpread " + F0(mk_spread) + " pts > max " + IntegerToString(maxSp) + " pts depuis " + FmtDur((double)(now - g_wdSpSince) / 1000.0) + " : plus de nouvelles entrées tant que ça dure.");
      }
   }
   else
   {
      if(g_wdSpSent && mk_spread <= maxSp) TgInfo("📶 Spread revenu à la normale (" + F0(mk_spread) + " pts).");
      g_wdSpSince = 0;
      g_wdSpSent = false;
   }

   MqlDateTime d;
   TimeToStruct(TimeTradeServer(), d);
   int key = d.year * 10000 + d.mon * 100 + d.day;
   if(d.hour == 23 && d.min >= 58 && st_posCount > 0 && key != g_swapDay)
   {
      g_swapDay = key;
      string s = "🌙 ROLLOVER DANS 2 MIN\n━━━━━━━━━━━━━━━━━━\n📊 " + IntegerToString(st_posCount) + " position(s) ouverte(s) · " + F2(st_posLots) + " lot" +
                 "\n💱 Swap déjà cumulé : " + Sgn(SwapTotal()) + " " + Cur();
      if(d.day_of_week == 3) s += "\n⚠️ Mercredi : swap x3 appliqué sur la plupart des symboles.";
      TgInfo(s);
   }
}

//+------------------------------------------------------------------+
//| News économiques (calendrier MT5)                                |
//+------------------------------------------------------------------+
datetime g_nwT[40];
string   g_nwName[40], g_nwCur[40];
int      g_nwImp[40];
ulong    g_nwId[40];
int      g_nwN = 0;
ulong    g_nwSeen[80];
int      g_nwSeenN = 0;
ulong    g_nwScanMs = 0;
bool     g_nwOK = true;

datetime NewsLocal(datetime srv) { return ZoneFromServer(srv); }

bool NewsSeen(ulong id)
{
   for(int i = 0; i < g_nwSeenN; i++) if(g_nwSeen[i] == id) return true;
   return false;
}

void NewsMarkSeen(ulong id)
{
   if(g_nwSeenN >= 80)
   {
      for(int i = 1; i < 80; i++) g_nwSeen[i - 1] = g_nwSeen[i];
      g_nwSeenN = 79;
   }
   g_nwSeen[g_nwSeenN] = id;
   g_nwSeenN++;
}

void NewsScan()
{
   g_nwScanMs = GetTickCount64();
   g_nwN = 0;
   string base = SymbolInfoString(_Symbol, SYMBOL_CURRENCY_BASE);
   string prof = SymbolInfoString(_Symbol, SYMBOL_CURRENCY_PROFIT);
   string curs[2];
   curs[0] = base; curs[1] = prof;
   int nc = (base == prof || base == "") ? 1 : 2;
   if(base == "") curs[0] = prof;
   datetime now = TimeTradeServer();
   int minImp = (PInt(P_NEWS_IMP) == 0) ? (int)CALENDAR_IMPORTANCE_HIGH : (int)CALENDAR_IMPORTANCE_MODERATE;
   g_nwOK = true;
   for(int c = 0; c < nc; c++)
   {
      MqlCalendarValue vals[];
      int n = CalendarValueHistory(vals, now - 20 * 60, now + 24 * 3600, NULL, curs[c]);
      if(n < 0) { g_nwOK = false; continue; }
      for(int j = 0; j < n && g_nwN < 40; j++)
      {
         MqlCalendarEvent ev;
         if(!CalendarEventById(vals[j].event_id, ev)) continue;
         if((int)ev.importance < minImp) continue;
         if(ev.time_mode != CALENDAR_TIMEMODE_DATETIME) continue;
         int pos = g_nwN;
         while(pos > 0 && g_nwT[pos - 1] > vals[j].time)
         {
            g_nwT[pos] = g_nwT[pos - 1]; g_nwName[pos] = g_nwName[pos - 1]; g_nwCur[pos] = g_nwCur[pos - 1];
            g_nwImp[pos] = g_nwImp[pos - 1]; g_nwId[pos] = g_nwId[pos - 1];
            pos--;
         }
         g_nwT[pos] = vals[j].time; g_nwName[pos] = ev.name; g_nwCur[pos] = curs[c];
         g_nwImp[pos] = (int)ev.importance; g_nwId[pos] = vals[j].id;
         g_nwN++;
      }
   }
}

string NewsWindowText()
{
   return "de " + IntegerToString(PInt(P_NEWS_BEFORE)) + " min avant à " + IntegerToString(PInt(P_NEWS_AFTER)) + " min après";
}

string NewsText()
{
   string s = "📰 FILTRE NEWS\n━━━━━━━━━━━━━━━━━━" +
              "\nSource : calendrier économique de MetaTrader 5, devises du symbole (" + _Symbol + ")." +
              "\n🛡️ Filtre : " + (PBool(P_NEWS_BLOCK) ? "✅ actif : pas de nouvelle entrée " + NewsWindowText() : "⚪ désactivé : le robot trade pendant les news") +
              "\n🎚 Importance : " + string(PInt(P_NEWS_IMP) == 0 ? "haute" : "moyenne + haute");
   s += g_newsBlock ? "\n⛔ TRADING BLOQUÉ PAR LA NEWS : " + g_newsWhy : (PBool(P_NEWS_BLOCK) ? "\n✅ Aucune news en cours : trading autorisé." : "");
   s += "\n\n📅 PROCHAINES ANNONCES (24 h)";
   if(!g_nwOK && g_nwN == 0) return s + "\nCalendrier économique indisponible sur ce terminal.";
   datetime now = TimeTradeServer();
   int shown = 0;
   for(int i = 0; i < g_nwN && shown < 10; i++)
   {
      if(g_nwT[i] < now - 15 * 60) continue;
      shown++;
      int dm = (int)((g_nwT[i] - now) / 60);
      s += "\n" + string(g_nwImp[i] >= (int)CALENDAR_IMPORTANCE_HIGH ? "🔴" : "🟠") + " " + Hh(NewsLocal(g_nwT[i])) + " " + g_nwCur[i] + " · " + g_nwName[i] +
           (dm >= 0 ? "  (dans " + FmtDur(dm * 60.0) + ")" : "  (en cours)");
   }
   if(shown == 0) s += "\nAucune annonce " + string(PInt(P_NEWS_IMP) == 0 ? "de haute importance" : "importante") + " dans les 24 h.";
   else s += "\n\n🕐 Heures affichées : " + TzLabel() + "";
   return s;
}

// Prochaine annonce non terminée (fenêtre du filtre comprise) : index dans g_nw*, -1 si aucune
int NewsNextIdx(int from = 0)
{
   datetime srv = TimeTradeServer();
   int after = PInt(P_NEWS_AFTER) * 60;
   for(int i = from; i < g_nwN; i++)
      if(g_nwT[i] >= srv - after) return i;
   return -1;
}

string NewsNextText()
{
   int i = NewsNextIdx();
   if(i < 0) return "";
   int dm = (int)((g_nwT[i] - TimeTradeServer()) / 60);
   return Hh(NewsLocal(g_nwT[i])) + " " + g_nwCur[i] + " " + g_nwName[i] + (dm >= 0 ? " (dans " + FmtDur(dm * 60.0) + ")" : " (en cours)");
}

// Prochaines annonces pour le statut du bot (heure du fuseau choisi)
string NewsJson(int maxN)
{
   string j = "[";
   int n = 0;
   datetime srv = TimeTradeServer();
   for(int i = NewsNextIdx(); i >= 0 && i < g_nwN && n < maxN; i++)
   {
      if(n > 0) j += ",";
      j += "{\"heure\":\"" + TimeToString(NewsLocal(g_nwT[i]), TIME_DATE | TIME_MINUTES) + "\"" +
           ",\"dans_min\":" + IntegerToString((int)((g_nwT[i] - srv) / 60)) +
           ",\"devise\":\"" + JsonEsc(g_nwCur[i]) + "\"" +
           ",\"nom\":\"" + JsonEsc(g_nwName[i]) + "\"" +
           ",\"haute\":" + BoolS(g_nwImp[i] >= (int)CALENDAR_IMPORTANCE_HIGH) + "}";
      n++;
   }
   return j + "]";
}

// Le calendrier est toujours lu (prochaines annonces affichées dans le bot) ; le filtre News ne bloque que s'il est actif.
void NewsTick()
{
   ulong now = GetTickCount64();
   if(g_nwScanMs == 0 || now - g_nwScanMs > 300000) NewsScan();
   datetime srv = TimeTradeServer();
   bool blk = false;
   string why = "";
   int before = PInt(P_NEWS_BEFORE) * 60, after = PInt(P_NEWS_AFTER) * 60;
   for(int i = 0; i < g_nwN; i++)
   {
      int dt = (int)(g_nwT[i] - srv);
      if(PBool(P_N_NEWS) && dt > 0 && dt <= PInt(P_NEWS_MIN) * 60 && !NewsSeen(g_nwId[i]))
      {
         NewsMarkSeen(g_nwId[i]);
         string s = "📰 NEWS DANS " + FmtDur((double)dt) + "\n━━━━━━━━━━━━━━━━━━\n" + string(g_nwImp[i] >= (int)CALENDAR_IMPORTANCE_HIGH ? "🔴" : "🟠") + " " + g_nwCur[i] + " · " + g_nwName[i] +
                    "\n🕐 " + Hh(NewsLocal(g_nwT[i])) + " (" + TzLabel() + ")\n⚠️ Volatilité attendue, spreads élargis possibles.";
         s += PBool(P_NEWS_BLOCK) ? "\n⛔ Filtre News actif : pas de nouvelle entrée " + NewsWindowText() + "."
                                  : "\n⚪ Filtre News désactivé : le robot continue de trader pendant l'annonce.";
         TgQueueP(s, 1);
      }
      if(PBool(P_NEWS_BLOCK) && dt <= before && dt >= -after && !blk)
      {
         blk = true;
         why = g_nwCur[i] + " " + g_nwName[i] + " (" + Hh(NewsLocal(g_nwT[i])) + ")";
      }
   }
   g_newsBlock = blk;
   g_newsWhy = why;
}

//+------------------------------------------------------------------+
//| Captures : graphique et courbe d'equity                          |
//+------------------------------------------------------------------+
// [ARCHI] début
// Les captures sont enregistrées dans Common\Files (le bot les récupère) et annoncées par un événement.
void ShotTick()
{
   if(g_shotReq == "" && g_curveReq == "") return;
   if(g_shotReq != "")
   {
      g_shotReq = "";
      string f = "sx_shot_" + g_aid + ".png";
      ChartRedraw(0);
      if(ChartScreenShot(0, f, 1280, 720, ALIGN_RIGHT) && FileCopy(f, 0, f, FILE_COMMON | FILE_REWRITE))
      {
         string cap = "📸 " + _Symbol + " · " + TfLabel() + " · " + TimeToString(LocalNow(), TIME_SECONDS) +
                      "\nEquity " + F2(AccountInfoDouble(ACCOUNT_EQUITY)) + " " + Cur() + " · Flottant " + Sgn(st_floating) + " · " + IntegerToString(st_posCount) + " pos";
         EvTyped("CAPTURE", 1, cap, "{\"fichier\":\"" + f + "\"}");
      }
      else EvTyped("NOTIF", 1, "❌ Capture du graphique impossible.");
   }
   if(g_curveReq != "")
   {
      g_curveReq = "";
      if(g_eqN < 2)
      {
         EvTyped("NOTIF", 0, "📈 Pas encore assez de points pour tracer la courbe du jour (1 point par minute).");
         return;
      }
      string f = "sx_eq_" + g_aid + ".bmp";
      if(EquityBmp(f, 640, 320) && FileCopy(f, 0, f, FILE_COMMON | FILE_REWRITE))
         EvTyped("COURBE", 1, EquityCaption(), "{\"fichier\":\"" + f + "\"}");
      else
         EvTyped("NOTIF", 1, EquityCaption() + "\n" + EquitySpark(30));
   }
}
// [ARCHI] fin

//+------------------------------------------------------------------+
//| Init / tick 1 Hz                                                 |
//+------------------------------------------------------------------+
void SmartInit()
{
   VacationInit();
   PauseInit();
}

void SmartTick1Hz()
{
   PauseTick();
   VacationTick();
   WatchdogTick();
   MarketAlertTick();
   NewsTick();
}
