//+------------------------------------------------------------------+
//| SX_FilterBoard.mqh - ScalpX v4.2                                 |
//| Filtres sur Telegram :                                           |
//|  - écran « Filtres » (menu) avec bascules                        |
//|  - UNE alerte globale cohérente : « filtres bloquants » puis     |
//|    « tous les filtres OK, le trading reprend » (confirmée 30 s)  |
//|  - l'état en direct est dans le panneau (SX_TgExtra.mqh)         |
//+------------------------------------------------------------------+
#define FA_CONFIRM_MS   30000     // un état global doit durer 30 s avant d'être annoncé

//--- Les 7 lignes : 0 ATR, 1 Prix/EMA, 2 Volume, 3 EMA inf., 4 EMA sup., 5 Bougie sup., 6 Horaire
bool FbOn(int i)
{
   switch(i)
   {
      case 0: return PBool(P_F_ATR);
      case 1: return PBool(P_F_EMA);
      case 2: return PBool(P_F_VOL);
      case 3: return PBool(P_F_LO);
      case 4: return PBool(P_F_HI);
      case 5: return PBool(P_F_CAN);
      default: return PBool(P_F_TIME);
   }
}

bool FbOK(int i) { return (i == 6) ? !f_timeBlocked : f_ok[i]; }

string FbName(int i)
{
   switch(i)
   {
      case 0: return "Volatilité (ATR)";
      case 1: return "Prix / EMA";
      case 2: return "Volume";
      case 3: return "EMA " + TfNameP(P_LOTF);
      case 4: return "EMA " + TfNameP(P_HITF);
      case 5: return "Bougie " + TfNameP(P_HITF) + " précédente";
      default: return "Horaire";
   }
}

string FbTrend(double f, double s) { return (f > s) ? "▲ haussier" : ((f < s) ? "▼ baissier" : "– à plat"); }

string FbInfo(int i)
{
   switch(i)
   {
      case 0: return F0(mk_atrPts) + " pts [" + F0(PDbl(P_ATRMIN)) + "–" + F0(PDbl(P_ATRMAX)) + "]";
      case 1: return "";
      case 2: return F0(mk_volRatio * 100) + " % de la moyenne";
      case 3: return FbTrend(mk_loF, mk_loS);
      case 4: return FbTrend(mk_hiF, mk_hiS);
      case 5: return (mk_candleDir > 0 ? "▲ haussière" : (mk_candleDir < 0 ? "▼ baissière" : "– neutre")) + ", corps " + F0(MathAbs(mk_candleBody)) + "/" + F0(mk_candleMin) + " pts";
      default: return "blocage " + IntegerToString(PInt(P_T_START)) + "h–" + IntegerToString(PInt(P_T_END)) + "h";
   }
}

string FbWhy(int i)
{
   if(i == 6) return f_timeBlocked ? "plage de blocage en cours" : "";
   return f_why[i];
}

int FbOrder(int k) { int o[7] = { 0, 1, 2, 6, 3, 4, 5 }; return o[k]; }   // même ordre que le panneau du graphique

bool FiltersAllOK()
{
   for(int i = 0; i < 7; i++)
      if(FbOn(i) && !FbOK(i)) return false;
   return true;
}

int FbBlockedCount()
{
   int n = 0;
   for(int i = 0; i < 7; i++)
      if(FbOn(i) && !FbOK(i)) n++;
   return n;
}

string FbBlockedList()
{
   string s = "";
   for(int k = 0; k < 7; k++)
   {
      int i = FbOrder(k);
      if(!FbOn(i) || FbOK(i)) continue;
      s += (s != "" ? "\n" : "") + "🔴 " + FbName(i) + (FbWhy(i) != "" ? " — " + FbWhy(i) : "");
   }
   return s;
}

//--- Lignes supplémentaires : 0 = robot (pause / vacances), 1 = pause auto news (si activée)
bool XOn(int e) { return (e == 0) ? true : PBool(P_NEWS_BLOCK); }
bool XOK(int e) { return (e == 0) ? (g_enabled && !g_vacation) : !g_newsBlock; }
string XName(int e) { return (e == 0) ? (g_vacation ? "Mode vacances" : "Robot (pause)") : "News"; }
string XInfo(int e)
{
   if(e == 0) return XOK(0) ? "actif" : (g_vacation ? "aucune nouvelle série" : "en pause");
   return "-" + IntegerToString(PInt(P_NEWS_BEFORE)) + " / +" + IntegerToString(PInt(P_NEWS_AFTER)) + " min · " + string(PInt(P_NEWS_IMP) == 0 ? "haute" : "moyenne+haute");
}
string XWhy(int e) { return (e == 1 && !XOK(1)) ? g_newsWhy : ""; }

int XBlockedCount()
{
   int n = 0;
   for(int e = 0; e < 2; e++) if(XOn(e) && !XOK(e)) n++;
   return n;
}

//--- Texte de l'écran « Filtres » (les filtres désactivés sont regroupés sur une ligne)
string FilterBoardText()
{
   string s = "🛡️ FILTRES\n━━━━━━━━━━━━━━━━━━";
   string off = "";
   for(int k = 0; k < 7; k++)
   {
      int i = FbOrder(k);
      if(!FbOn(i)) { off += (off != "" ? ", " : "") + FbName(i); continue; }
      bool ok = FbOK(i);
      s += "\n" + string(ok ? "🟢" : "🔴") + " " + FbName(i);
      string inf = FbInfo(i);
      if(inf != "") s += " · " + inf;
      if(!ok && FbWhy(i) != "") s += "\n      ↳ " + FbWhy(i);
   }
   for(int e = 0; e < 2; e++)
   {
      if(!XOn(e)) { off += (off != "" ? ", " : "") + XName(e); continue; }
      bool ok = XOK(e);
      s += "\n" + string(ok ? "🟢" : "🔴") + " " + XName(e) + " · " + XInfo(e);
      if(!ok && XWhy(e) != "") s += "\n      ↳ " + XWhy(e);
   }
   if(off != "") s += "\n⚪ Désactivés : " + off;
   s += "\n━━━━━━━━━━━━━━━━━━\n";
   int nb = FbBlockedCount() + XBlockedCount();
   if(nb > 0)                  s += "⛔ NOUVELLES SÉRIES BLOQUÉES (" + IntegerToString(nb) + ")";
   else if(g_blockReason == "") s += "✅ TRADING AUTORISÉ";
   else                        s += "✅ Filtres OK\n⏸ Mais : " + g_blockReason;
   s += "\n🕐 " + TimeToString(LocalNow(), TIME_SECONDS);
   return s;
}

//--- Clavier : bascules des filtres (le message est modifié sur place)
string FilterBoardMarkup()
{
   string rows = "";
   AddRow(rows, KRow(Btn(string(PBool(P_F_ATR) ? "✅" : "❌") + " Volatilité", "t:" + IntegerToString(P_F_ATR) + ":f"),
                     Btn(string(PBool(P_F_EMA) ? "✅" : "❌") + " Prix/EMA", "t:" + IntegerToString(P_F_EMA) + ":f")));
   AddRow(rows, KRow(Btn(string(PBool(P_F_VOL) ? "✅" : "❌") + " Volume", "t:" + IntegerToString(P_F_VOL) + ":f"),
                     Btn(string(PBool(P_F_TIME) ? "✅" : "❌") + " Horaire", "t:" + IntegerToString(P_F_TIME) + ":f")));
   AddRow(rows, KRow(Btn(string(PBool(P_F_LO) ? "✅" : "❌") + " EMA " + TfNameP(P_LOTF), "t:" + IntegerToString(P_F_LO) + ":f"),
                     Btn(string(PBool(P_F_HI) ? "✅" : "❌") + " EMA " + TfNameP(P_HITF), "t:" + IntegerToString(P_F_HI) + ":f")));
   AddRow(rows, KRow(Btn(string(PBool(P_F_CAN) ? "✅" : "❌") + " Bougie " + TfNameP(P_HITF), "t:" + IntegerToString(P_F_CAN) + ":f"),
                     Btn(string(PBool(P_NEWS_BLOCK) ? "✅" : "❌") + " News", "t:" + IntegerToString(P_NEWS_BLOCK) + ":f")));
   AddRow(rows, KRow(Btn("⚙️ Seuils", "g:" + IntegerToString(G_FILTERS)), Btn("⏰ Horaire", "g:" + IntegerToString(G_TIME)), Btn("🔭 Multi-TF", "g:" + IntegerToString(G_MTF))));
   AddRow(rows, KRow(Btn("📰 Réglages news", "nw"), Btn("🔄", "fl"), Btn("🏠 Menu", "m")));
   return KB(rows);
}

//--- Signature de l'état : change seulement quand une LED ou le statut global bouge
string FilterBoardSig()
{
   string g = "";
   for(int i = 0; i < 7; i++) g += !FbOn(i) ? "o" : (FbOK(i) ? "g" : "r");
   g += (XOK(0) ? "g" : "r") + string(XOn(1) ? (XOK(1) ? "g" : "r") : "o");
   return g + (g_blockReason != "" ? "B" : "-") + (g_enabled ? "E" : "P");
}

//--- Alerte globale : une seule notification à chaque vrai changement d'état
void FilterAlertTick()
{
   int c = FiltersAllOK() ? 1 : 0;
   ulong now = GetTickCount64();
   if(g_faState < 0) { g_faState = c; g_faSince = 0; return; }       // état initial : aucune alerte
   if(c == g_faState) { g_faSince = 0; return; }
   if(g_faSince == 0) g_faSince = now;
   if(now - g_faSince < FA_CONFIRM_MS) return;                       // anti-rebond : l'état doit durer
   g_faState = c;
   g_faSince = 0;
   if(!PBool(P_N_FILTERS) || !g_enabled || !g_goalActive) return;    // robot à l'arrêt : le tableau de bord suffit

   string m;
   if(c == 1)
   {
      m = "✅ FILTRES OK — LE TRADING REPREND\n━━━━━━━━━━━━━━━━━━\nTous les filtres sont au vert : les nouvelles séries sont de nouveau autorisées.";
      if(g_blockReason != "") m += "\n\nℹ️ Mais : " + g_blockReason;
   }
   else
      m = "⛔ NOUVELLES SÉRIES SUSPENDUES\n━━━━━━━━━━━━━━━━━━\n" + FbBlockedList() +
          "\n\nLa grille et les sorties continuent. Je te préviens dès que tout repasse au vert.";
   TgInfo(m);                                                        // information : silencieuse, regroupable en résumé
}
//+------------------------------------------------------------------+
