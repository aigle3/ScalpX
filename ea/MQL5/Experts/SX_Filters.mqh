//+------------------------------------------------------------------+
//| SX_Filters.mqh - ScalpX v4.1                                     |
//| Filtres 0-2 : MÊME timeframe (celui du robot) et MÊME bougie    |
//| (P_BAR), antirebond de 12 s.                                     |
//| Filtres 3-5 (v4.1) : multi-timeframe (EMA inférieur, EMA         |
//| supérieur, bougie supérieure précédente), directionnels : ils    |
//| comparent au sens du signal, sans antirebond, et sont revérifiés |
//| au moment exact de l'entrée (MtfEntryOK).                        |
//+------------------------------------------------------------------+
#define FILTER_DEBOUNCE_MS  12000
#define FILTER_NOTE_GAP_MS  60000

string   f_why[NFILTERS];     // raison courante de chaque filtre
datetime f_lastVolBar = 0;

bool TimeBlockedNow()
{
   if(!PBool(P_F_TIME)) return false;
   MqlDateTime dt;
   TimeToStruct(PBool(P_T_SERVER) ? TimeTradeServer() : LocalNow(), dt);
   int h = dt.hour, s = PInt(P_T_START), e = PInt(P_T_END);
   if(s == e) return false;
   if(s < e) return (h >= s && h < e);
   return (h >= s || h < e);          // plage passant minuit
}

// Retourne +1 si le filtre repasse OK, -1 s'il se bloque, 0 sinon.
int Deb(int i, bool raw)
{
   f_raw[i] = raw;
   if(raw == f_ok[i]) { f_since[i] = 0; return 0; }
   if(f_since[i] == 0) f_since[i] = GetTickCount64();
   if(GetTickCount64() - f_since[i] >= FILTER_DEBOUNCE_MS)
   {
      f_ok[i] = raw;
      f_since[i] = 0;
      return raw ? 1 : -1;
   }
   return 0;
}

// v4.2 : plus de notification par filtre. Le détail est dans le message « Filtres en direct »
// (SX_FilterBoard.mqh) et l'alerte globale est gérée par FilterAlertTick().
void NoteFilter(int i, bool nowOK, string title, string detail) { }

string TfNameP(int pid) { int i = PInt(pid); if(i < 0 || i > 5) i = 0; return g_tfName[i]; }

bool MtfOn(int i)
{
   if(i == 3) return PBool(P_F_LO);
   if(i == 4) return PBool(P_F_HI);
   if(i == 5) return PBool(P_F_CAN);
   return false;
}

// Filtre directionnel i (3, 4 ou 5) pour un sens dir (+1 achat, -1 vente). Renseigne why si bloqué.
bool MtfRaw(int i, int dir, string &why)
{
   why = "";
   if(dir == 0) return true;                       // pas de signal : rien à bloquer
   string sens = (dir > 0) ? "achat" : "vente";
   if(i == 3 || i == 4)
   {
      bool lo = (i == 3);
      string tf = TfNameP(lo ? P_LOTF : P_HITF);
      double f = 0, s = 0;
      if(!ReadEMAsH(lo ? g_hFastLo : g_hFastHi, lo ? g_hSlowLo : g_hSlowHi, PInt(lo ? P_LOBAR : P_HIBAR), f, s))
      { why = "Données " + tf + " indisponibles"; return false; }
      if(lo) { mk_loF = f; mk_loS = s; } else { mk_hiF = f; mk_hiS = s; }
      bool ok = (dir > 0) ? (f > s) : (f < s);
      if(!ok) why = "EMA " + tf + " " + string(f > s ? "haussières" : (f < s ? "baissières" : "à plat")) + " : contre le signal de " + sens;
      return ok;
   }
   if(i == 5)
   {
      string tf = TfNameP(P_HITF);
      double o = iOpen(_Symbol, g_hiTF, 1), c = iClose(_Symbol, g_hiTF, 1);
      double b[1];
      if(o <= 0 || c <= 0 || g_hATRHi == INVALID_HANDLE || CopyBuffer(g_hATRHi, 0, 1, 1, b) != 1)
      { why = "Données bougie " + tf + " indisponibles"; return false; }
      mk_candleBody = (c - o) / _Point;
      mk_candleMin  = PDbl(P_CANBODY) * b[0] / _Point;
      mk_candleDir  = (mk_candleBody > mk_candleMin) ? 1 : ((mk_candleBody < -mk_candleMin) ? -1 : 0);
      bool ok = (mk_candleDir == 0 || mk_candleDir == dir);   // bougie neutre (corps trop petit) : ne bloque pas
      if(!ok) why = "Bougie " + tf + " précédente " + string(mk_candleDir > 0 ? "haussière" : "baissière") +
                    " (" + F0(MathAbs(mk_candleBody)) + " pts) : contre le signal de " + sens;
      return ok;
   }
   return true;
}

// Revérification au moment de l'entrée, avec le sens exact du signal (pas de valeur périmée)
bool MtfEntryOK(int dir, string &why)
{
   why = "";
   for(int i = 3; i < NFILTERS; i++)
   {
      if(!MtfOn(i)) continue;
      string w;
      if(!MtfRaw(i, dir, w)) { why = w; return false; }
   }
   return true;
}

void UpdateFilters()
{
   int sh = BarShift();
   mk_bid    = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   mk_ask    = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   mk_spread = (double)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   mk_atrPts = ATRPoints(sh);
   double f = 0, s = 0;
   bool haveEma = ReadEMAs(sh, f, s);
   if(haveEma) { mk_fast = f; mk_slow = s; }
   f_marketOpen  = MarketOpenNow();
   f_timeBlocked = TimeBlockedNow();

   //--- 0 : ATR (volatilité)
   if(!PBool(P_F_ATR) || mk_atrPts <= 0) { f_ok[0] = true; f_since[0] = 0; f_why[0] = ""; }
   else
   {
      bool raw = (mk_atrPts >= PDbl(P_ATRMIN) && mk_atrPts <= PDbl(P_ATRMAX));
      if(!raw)
         f_why[0] = (mk_atrPts < PDbl(P_ATRMIN))
                    ? "ATR trop faible (" + F0(mk_atrPts) + " < " + F0(PDbl(P_ATRMIN)) + " pts)"
                    : "ATR trop élevé (" + F0(mk_atrPts) + " > " + F0(PDbl(P_ATRMAX)) + " pts)";
      else f_why[0] = "";
      int ch = Deb(0, raw);
      if(ch != 0)
      {
         string det = "📈 ATR : " + F0(mk_atrPts) + " pts\n📊 Plage : " + F0(PDbl(P_ATRMIN)) + " – " + F0(PDbl(P_ATRMAX)) + " pts";
         if(ch < 0) det += (mk_atrPts < PDbl(P_ATRMIN)) ? "\n💤 Marché trop calme (consolidation)" : "\n⚡ Marché trop volatil";
         NoteFilter(0, ch > 0, "Filtre de volatilité (ATR)", det);
      }
   }

   //--- 1 : Prix / EMA
   if(!PBool(P_F_EMA) || !haveEma) { f_ok[1] = true; f_since[1] = 0; f_why[1] = ""; }
   else
   {
      bool raw = true;
      string why = "";
      double p = mk_bid;
      if(f > s && p < f)       { raw = false; why = "Hausse mais prix sous l'EMA rapide"; }
      else if(f < s && p > f)  { raw = false; why = "Baisse mais prix au-dessus de l'EMA rapide"; }
      else if((p > f && p < s) || (p < f && p > s)) { raw = false; why = "Prix entre les deux EMA"; }
      f_why[1] = why;
      int ch = Deb(1, raw);
      if(ch != 0)
      {
         string det = "💰 Prix : " + PX(p) + "\n📈 EMA rapide : " + PX(f) + "\n📉 EMA lente : " + PX(s);
         if(ch < 0) det = "🔎 " + why + "\n" + det;
         NoteFilter(1, ch > 0, "Filtre Prix / EMA", det);
      }
   }

   //--- 2 : Volume (dernière bougie CLÔTURÉE vs moyenne des précédentes)
   if(!PBool(P_F_VOL)) { f_ok[2] = true; f_since[2] = 0; f_why[2] = ""; mk_volRatio = 0; }
   else
   {
      datetime bar1 = iTime(_Symbol, g_sigTF, 1);
      if(bar1 != f_lastVolBar)
      {
         int nb = PInt(P_VOLBARS);
         long vols[];
         ArraySetAsSeries(vols, true);
         if(CopyTickVolume(_Symbol, g_sigTF, 1, nb + 1, vols) == nb + 1)
         {
            double sum = 0;
            for(int k = 1; k <= nb; k++) sum += (double)vols[k];
            double avg = sum / nb;
            mk_volRatio = (avg > 0) ? (double)vols[0] / avg : 1.0;
            f_lastVolBar = bar1;
         }
      }
      bool raw = (mk_volRatio <= 0) || (mk_volRatio >= PDbl(P_VOLMIN) && mk_volRatio <= PDbl(P_VOLMAX));
      f_why[2] = raw ? "" : ((mk_volRatio < PDbl(P_VOLMIN)) ? "Volume trop faible (" + F0(mk_volRatio * 100) + " % de la moyenne)"
                                                           : "Volume trop élevé (" + F0(mk_volRatio * 100) + " % de la moyenne)");
      int ch = Deb(2, raw);
      if(ch != 0)
      {
         string det = "📊 Ratio : " + F0(mk_volRatio * 100) + " % de la moyenne\n🎯 Plage : " +
                      F0(PDbl(P_VOLMIN) * 100) + " – " + F0(PDbl(P_VOLMAX) * 100) + " %";
         NoteFilter(2, ch > 0, "Filtre de volume", det);
      }
   }

   //--- 3 à 5 : multi-timeframe (sens du signal du robot)
   int dir = haveEma ? ((f > s) ? 1 : ((f < s) ? -1 : 0)) : 0;
   for(int i = 3; i < NFILTERS; i++)
   {
      if(!MtfOn(i)) { f_ok[i] = true; f_raw[i] = true; f_since[i] = 0; f_why[i] = ""; continue; }
      string why;
      bool raw = MtfRaw(i, dir, why);
      f_why[i] = why;
      f_raw[i] = raw;
      if(raw != f_ok[i])
      {
         f_ok[i] = raw;
         string title, det;
         if(i == 3)      { title = "Filtre EMA " + TfNameP(P_LOTF); det = "📈 EMA rapide : " + PX(mk_loF) + "\n📉 EMA lente : " + PX(mk_loS); }
         else if(i == 4) { title = "Filtre EMA " + TfNameP(P_HITF); det = "📈 EMA rapide : " + PX(mk_hiF) + "\n📉 EMA lente : " + PX(mk_hiS); }
         else            { title = "Filtre bougie " + TfNameP(P_HITF); det = "🕯 Corps : " + F0(mk_candleBody) + " pts (seuil " + F0(mk_candleMin) + " pts)"; }
         if(!raw) det = "🔎 " + why + "\n" + det;
         NoteFilter(i, raw, title, det);
      }
   }
}

//+------------------------------------------------------------------+
//| Une nouvelle série peut-elle être ouverte ? (raison sinon)       |
//+------------------------------------------------------------------+
bool EntryAllowed(string &why)
{
   why = "";
   if(!g_enabled)        { why = "Robot en pause"; return false; }
   if(!g_configOK)       { why = "Paramètres non validés"; return false; }
   if(!g_goalActive)     { why = "Aucun objectif actif"; return false; }
   if(GetTickCount64() < g_cooldownUntilMs) { why = "Pause après objectif"; return false; }
   string w;
   if(!TradeEnvOK(w))    { why = w; return false; }
   if(!f_marketOpen)     { why = "Marché fermé"; return false; }
   if(GetTickCount64() < g_nextTradeMs) { why = "Attente après un échec d'ordre"; return false; }
   if(g_vacation)        { why = "Mode vacances"; return false; }
   if(g_newsBlock)       { why = "News : " + g_newsWhy; return false; }
   if(mk_spread > PInt(P_MAXSPREAD))    { why = "Spread trop large (" + F0(mk_spread) + " > " + IntegerToString(PInt(P_MAXSPREAD)) + " pts)"; return false; }
   if(f_timeBlocked)     { why = "Filtre horaire (" + IntegerToString(PInt(P_T_START)) + "h–" + IntegerToString(PInt(P_T_END)) + "h)"; return false; }
   for(int i = 0; i < NFILTERS; i++)
      if(!f_ok[i]) { why = f_why[i] != "" ? f_why[i] : "Filtre actif"; return false; }
   return true;
}
//+------------------------------------------------------------------+
