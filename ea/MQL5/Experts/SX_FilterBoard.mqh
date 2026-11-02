//+------------------------------------------------------------------+
//| SX_FilterBoard.mqh - ScalpX                                      |
//| Tableau des filtres (bot, panneau, journal, statut) :            |
//|  - 8 filtres dont News (calendrier MT5)                          |
//|  - UNE alerte globale cohérente : « filtres bloquants » puis     |
//|    « tous les filtres OK, le trading reprend » (confirmée 30 s)  |
//+------------------------------------------------------------------+
#define FA_CONFIRM_MS   30000     // un état global doit durer 30 s avant d'être annoncé

// [ARCHI] début : 6.6.0
string FreshText() { return mk_freshAge < 0 ? "croisement trop ancien" : "croisement il y a " + IntegerToString(mk_freshAge) + " bougie(s)"; }
// [ARCHI] fin

//--- Les FB_N lignes : 0 ATR, 1 Prix/EMA, 2 Volume, 3 EMA inf., 4 EMA sup., 5 Bougie sup., 6 Horaire, 7 News, 8 Spread, 9 Extension, 10 Signal récent
//    [ARCHI] 8 Spread : toujours actif (le spread maximum bloque déjà les nouveaux ordres, voir OnTick) ; ici affichage seul
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
      case 6: return PBool(P_F_TIME);
      case 7: return PBool(P_NEWS_BLOCK);
      case 9: return PBool(P_F_EXT);                              // [ARCHI] 6.6.0
      case 10: return PBool(P_F_FRESH);                           // [ARCHI] 6.6.0
      default: return true;                                       // [ARCHI] spread : toujours contrôlé
   }
}

bool FbOK(int i)
{
   if(i == 6) return !f_timeBlocked;
   if(i == 7) return !g_newsBlock;
   if(i == 8) return mk_spread <= PInt(P_MAXSPREAD);              // [ARCHI] même test que OnTick
   if(i == 9) return f_extOK;                                     // [ARCHI] 6.6.0
   if(i == 10) return f_freshOK;                                  // [ARCHI] 6.6.0
   return f_ok[i];
}

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
      case 6: return "Horaire";
      case 7: return "News";
      case 9: return "Extension";                                 // [ARCHI] 6.6.0
      case 10: return "Signal récent";                            // [ARCHI] 6.6.0
      default: return "Spread";
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
      case 6: return "blocage " + IntegerToString(PInt(P_T_START)) + "h–" + IntegerToString(PInt(P_T_END)) + "h";
      case 8: return F0(mk_spread) + " pts (max " + IntegerToString(PInt(P_MAXSPREAD)) + ")";
      case 9: return F1(MathAbs(mk_extAtr)) + " x ATR de l'EMA lente" + ExtTfText() + " (max " + F1(PDbl(P_EXTATR)) + ")";      // [ARCHI] 6.6.0
      case 10: return FreshText() + " (max " + IntegerToString(PInt(P_FRESHBARS)) + ")";                    // [ARCHI] 6.6.0
      default:
      {
         string nx = NewsNextText();
         return "-" + IntegerToString(PInt(P_NEWS_BEFORE)) + "/+" + IntegerToString(PInt(P_NEWS_AFTER)) + " min" + (nx != "" ? " · prochaine : " + nx : " · aucune news dans les 24 h");
      }
   }
}

string FbWhy(int i)
{
   if(i == 6) return f_timeBlocked ? "plage de blocage en cours" : "";
   if(i == 7) return g_newsBlock ? "news : " + g_newsWhy : "";
   if(i == 8) return FbOK(8) ? "" : "spread trop large (" + F0(mk_spread) + " > " + IntegerToString(PInt(P_MAXSPREAD)) + " pts)";
   if(i == 9) return f_extWhy;                                    // [ARCHI] 6.6.0
   if(i == 10) return f_freshWhy;                                 // [ARCHI] 6.6.0
   return f_why[i];
}

int FbOrder(int k) { int o[FB_N] = { 0, 1, 9, 10, 2, 6, 7, 8, 3, 4, 5 }; return o[k]; }   // même ordre que le panneau du graphique ([ARCHI] 6.6.0 : + 9, 10)

bool FiltersAllOK()
{
   for(int i = 0; i < FB_N; i++)
      if(FbOn(i) && !FbOK(i)) return false;
   return true;
}

int FbBlockedCount()
{
   int n = 0;
   for(int i = 0; i < FB_N; i++)
      if(FbOn(i) && !FbOK(i)) n++;
   return n;
}

string FbBlockedList()
{
   string s = "";
   for(int k = 0; k < FB_N; k++)
   {
      int i = FbOrder(k);
      if(!FbOn(i) || FbOK(i)) continue;
      s += (s != "" ? "\n" : "") + "🔴 " + FbName(i) + (FbWhy(i) != "" ? " — " + FbWhy(i) : "");
   }
   return s;
}

//--- Robot lui-même (pause / vacances) : affiché sous les filtres
bool   RobotOK()   { return g_enabled && !g_vacation; }
string RobotLine() { return g_vacation ? "Mode vacances · aucune nouvelle série" : "Robot · en pause"; }

//--- Texte de l'écran « Filtres » (les filtres désactivés sont regroupés sur une ligne)
string FilterBoardText()
{
   string s = "🛡️ FILTRES\n━━━━━━━━━━━━━━━━━━";
   string off = "";
   for(int k = 0; k < FB_N; k++)
   {
      int i = FbOrder(k);
      if(!FbOn(i)) { off += (off != "" ? ", " : "") + FbName(i); continue; }
      bool ok = FbOK(i);
      s += "\n" + string(ok ? "🟢" : "🔴") + " " + FbName(i);
      string inf = FbInfo(i);
      if(inf != "") s += " · " + inf;
      if(!ok && FbWhy(i) != "") s += "\n      ↳ " + FbWhy(i);
   }
   if(!RobotOK()) s += "\n🔴 " + RobotLine();
   if(off != "") s += "\n⚪ Désactivés : " + off;
   s += "\n━━━━━━━━━━━━━━━━━━\n";
   int nb = FbBlockedCount() + (RobotOK() ? 0 : 1);
   if(nb > 0)                  s += "⛔ NOUVELLES SÉRIES BLOQUÉES (" + IntegerToString(nb) + ")";
   else if(g_blockReason == "") s += "✅ TRADING AUTORISÉ";
   else                        s += "✅ Filtres OK\n⏸ Mais : " + g_blockReason;
   s += "\n🕐 " + TimeToString(LocalNow(), TIME_SECONDS);
   return s;
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
