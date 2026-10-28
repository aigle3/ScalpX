//+------------------------------------------------------------------+
//| SX_TgExtra.mqh - ScalpX v4.5                                     |
//| Écrans et commandes Telegram supplémentaires : panneau d'état    |
//| (dernier message, actualisé en direct), outils, réglages rapides,|
//| pause programmée,                                                |
//| vacances, stats, journal, multi, captures, confirmation rapide.  |
//+------------------------------------------------------------------+

//+------------------------------------------------------------------+
//| Panneau d'état : petit résumé du compte, TOUJOURS en dernier     |
//|  - actualisé toutes les 30 s                                     |
//|  - toutes les 5 min, s'il n'est plus le dernier message, il est  |
//|    renvoyé en bas (en silence) et l'ancien est supprimé          |
//+------------------------------------------------------------------+
string FilterLeds()
{
   string s = XOK(0) ? "🟢" : "🔴";                                   // robot : actif / en pause
   for(int k = 0; k < 7; k++)
   {
      int i = FbOrder(k);
      if(!FbOn(i)) continue;                                          // filtre désactivé : non affiché
      s += FbOK(i) ? "🟢" : "🔴";
   }
   if(XOn(1)) s += XOK(1) ? "🟢" : "🔴";
   return s;
}

string FbBlockedNames()
{
   string s = "";
   if(!XOK(0)) s += XName(0);
   for(int k = 0; k < 7; k++)
   {
      int i = FbOrder(k);
      if(!FbOn(i) || FbOK(i)) continue;
      s += (s != "" ? ", " : "") + FbName(i);
   }
   if(XOn(1) && !XOK(1)) s += (s != "" ? ", " : "") + XName(1);
   return s;
}

string PanelText()
{
   RefreshState();
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   double eq  = AccountInfoDouble(ACCOUNT_EQUITY);
   string s = "📊 SCALPX · " + _Symbol + "\n" + StateLabel();
   s += "\n💼 Balance " + F2(bal) + " · Equity " + F2(eq) + " " + Cur();
   s += "\n" + string(st_floating >= 0 ? "🟢" : "🔴") + " Flottant " + Sgn(st_floating) + " · " + IntegerToString(st_posCount) + " pos · " + IntegerToString(st_pendCount) + " ordres";
   if(g_goalActive) s += "\n🎯 " + Bar(GoalProgressPct()) + " " + F0(GoalProgressPct()) + " % · reste " + F2(g_goalTarget - bal);
   else             s += "\n🎯 Aucun objectif actif";
   double ddThr = PDbl(P_DDTHR), dd = CurrentDDPct();
   s += "\n📉 " + Bar(ddThr > 0 ? dd / ddThr * 100.0 : 0) + " " + F1(dd) + " % / " + F0(ddThr) + " % " + Dot3(ddThr > 0 ? dd / ddThr : 0);
   int nb = FbBlockedCount() + XBlockedCount() + (XOK(0) ? 0 : 1);
   s += "\n🛡️ " + FilterLeds() + (nb > 0 ? "  ⛔ " + FbBlockedNames() : "  ✅ tout OK");
   s += "\n🕐 " + TimeToString(LocalNow(), TIME_SECONDS);
   return s;
}

//+------------------------------------------------------------------+
//| Réglages rapides                                                 |
//+------------------------------------------------------------------+
string QuickRow(int id, string icon, string label, string view)
{
   string v = IntegerToString(id);
   return KRow(Btn("➖", "qk:" + v + ":-1:" + view), Btn(icon + " " + label + " " + PValStr(id), "e:" + v), Btn("➕", "qk:" + v + ":1:" + view));
}

int QuickGridId() { return PBool(P_DYNGRID) ? P_GRIDATR : P_GRID; }
int QuickTpId()   { return PBool(P_DYNTP) ? P_ATRMULT : P_TPFIXED; }

void TgShowQuick(long edit = 0, string note = "")
{
   string s = "⚡ RÉGLAGES RAPIDES\nTouche ➖ / ➕ pour ajuster, ou le nom pour saisir une valeur." + (note != "" ? "\n\n" + note : "");
   string rows = "";
   AddRow(rows, QuickRow(P_LOT, "💰", "Lot", "q"));
   AddRow(rows, QuickRow(P_LOTMULT, "✖️", "Mult.", "q"));
   AddRow(rows, QuickRow(QuickGridId(), "📐", PBool(P_DYNGRID) ? "Grille ATR" : "Grille", "q"));
   AddRow(rows, QuickRow(QuickTpId(), "🎯", PBool(P_DYNTP) ? "TP ATR" : "TP", "q"));
   AddRow(rows, QuickRow(P_MAXSPREAD, "📶", "Spread", "q"));
   AddRow(rows, QuickRow(P_MAXPEND, "🧱", "Ordres", "q"));
   AddRow(rows, KRow(Btn("⬅️ Réglages", "se"), Btn("🏠 Menu", "m")));
   TgQueue(s, KB(rows), edit);
}

//+------------------------------------------------------------------+
//| Outils                                                           |
//+------------------------------------------------------------------+
void TgShowTools(long edit = 0, string note = "")
{
   string s = "🧰 OUTILS";
   if(g_vacation) s += "\n🏖 Mode vacances actif";
   if(InQuietHours()) s += "\n🔇 Mode silencieux en cours";
   if(note != "") s += "\n\n" + note;
   string rows = "";
   AddRow(rows, KRow(Btn("📅 Rapport du jour", "rp"), Btn("🧾 Journal filtres", "fj")));
   AddRow(rows, KRow(Btn("📸 Graphique", "shot"), Btn("📈 Courbe", "curve")));
   AddRow(rows, KRow(Btn("📰 News", "nw"), Btn("🌐 Multi-instances", "mu")));
   AddRow(rows, KRow(Btn(g_vacation ? "🏠 Quitter vacances" : "🏖 Vacances", "vac"), Btn("❓ Aide", "hp")));
   AddRow(rows, KRow(Btn("🏠 Menu", "m")));
   TgQueue(s, KB(rows), edit);
}

string ToolsBack(string refresh = "")
{
   string rows = "";
   if(refresh != "") AddRow(rows, KRow(Btn("🔄 Actualiser", refresh), Btn("⬅️ Outils", "tl"), Btn("🏠 Menu", "m")));
   else AddRow(rows, KRow(Btn("⬅️ Outils", "tl"), Btn("🏠 Menu", "m")));
   return KB(rows);
}

void TgShowVacation(long edit = 0)
{
   string s = "🏖 MODE VACANCES\n━━━━━━━━━━━━━━━━━━\n• Aucune nouvelle série ne démarre\n• La grille n'est plus étendue\n• Les positions ouvertes sont gérées jusqu'à leur TP\n• Plafond de risque : tout est fermé si la perte flottante dépasse " +
              F1(PDbl(P_VAC_RISK)) + " % de la balance\n\nÉtat actuel : " + string(g_vacation ? "🏖 ACTIF" : "désactivé");
   string rows = "";
   if(g_vacation) AddRow(rows, KRow(Btn("🏠 Quitter le mode vacances", "vac0")));
   else           AddRow(rows, KRow(Btn("🏖 Activer", "vac1")));
   AddRow(rows, KRow(Btn("⚙️ Régler le plafond", "e:" + IntegerToString(P_VAC_RISK)), Btn("⬅️ Outils", "tl")));
   TgQueue(s, KB(rows), edit);
}

void TgShowNews(long edit = 0, string note = "")
{
   NewsScan();
   string rows = "";
   AddRow(rows, KRow(Btn(string(PBool(P_NEWS_BLOCK) ? "✅" : "❌") + " Pause auto autour des news", "t:" + IntegerToString(P_NEWS_BLOCK) + ":n")));
   AddRow(rows, QuickRow(P_NEWS_BEFORE, "⏪", "Avant", "n"));
   AddRow(rows, QuickRow(P_NEWS_AFTER, "⏩", "Après", "n"));
   AddRow(rows, KRow(Btn(string(PInt(P_NEWS_IMP) == 0 ? "✅ " : "") + "Haute", "o:" + IntegerToString(P_NEWS_IMP) + ":0:n"),
                     Btn(string(PInt(P_NEWS_IMP) == 1 ? "✅ " : "") + "Moyenne + Haute", "o:" + IntegerToString(P_NEWS_IMP) + ":1:n")));
   AddRow(rows, KRow(Btn("🔄", "nw"), Btn("⬅️ Filtres", "fl"), Btn("🏠 Menu", "m")));
   TgQueue(NewsText() + (note != "" ? "\n\n" + note : ""), KB(rows), edit);
}

//+------------------------------------------------------------------+
//| Boutons                                                          |
//+------------------------------------------------------------------+
void QuickAdjust(int id, int delta, string view, long msgId)
{
   string err = "";
   double nv = NormalizeDouble(g_pVal[id] + delta * g_pStep[id], 8);
   if(nv < g_pMin[id]) nv = g_pMin[id];
   if(nv > g_pMax[id]) nv = g_pMax[id];
   bool ok = SetParam(id, nv, err);
   if(view == "m")
   {
      if(!ok) { TgShowQuick(msgId, "❌ " + err); return; }
      TgShowMain(msgId);
   }
   else if(view == "n") TgShowNews(msgId, ok ? "" : "❌ " + err);
   else TgShowQuick(msgId, ok ? "" : "❌ " + err);
}

bool TgExtraCallback(long msgId, string c, string &p[], int n)
{
   if(c == "rp")  { TgQueue(TodayReportText("📅 RAPPORT DU JOUR"), ToolsBack("rp"), msgId); return true; }
   if(c == "tl")  { TgShowTools(msgId); return true; }
   if(c == "qs")  { TgShowQuick(msgId); return true; }
   if(c == "qk" && n >= 3) { QuickAdjust((int)StringToInteger(p[1]), (int)StringToInteger(p[2]), (n >= 4 ? p[3] : "q"), msgId); return true; }
   // anciens boutons « Historique » encore visibles dans Telegram : renvoyés vers le rapport du jour
   if(c == "hx" || c == "stt" || c == "his" || c == "hs") { TgQueue(TodayReportText("📅 RAPPORT DU JOUR"), ToolsBack("rp"), msgId); return true; }
   if(c == "fj")  { TgQueue(FilterJournalText(), ToolsBack("fj"), msgId); return true; }
   if(c == "mu")  { TgQueue(MultiText(), ToolsBack("mu"), msgId); return true; }
   if(c == "shot")  { g_shotReq = "1"; TgShowTools(msgId, "📸 Capture en route… elle arrive dans quelques secondes."); return true; }
   if(c == "curve") { g_curveReq = "1"; TgShowTools(msgId, "📈 Courbe en route… elle arrive dans quelques secondes."); return true; }
   if(c == "vac")  { TgShowVacation(msgId); return true; }
   if(c == "vac1") { VacationSet(true, msgId); return true; }
   if(c == "vac0") { VacationSet(false, msgId); return true; }
   if(c == "nw")   { TgShowNews(msgId); return true; }
   if(c == "fln")  { UpdateFilters(); TgShowFilters(); return true; }
   return false;
}

//+------------------------------------------------------------------+
//| Commandes texte                                                  |
//+------------------------------------------------------------------+
void ShortReply(bool ok, string msg)
{
   TgQueue((ok ? "✅ " : "❌ ") + msg, KB(KRow(Btn("⚡ Rapide", "qs"), Btn("🏠 Menu", "m"))));
}

bool ShortSetNum(int id, string val)
{
   double v;
   string err = "";
   if(val == "") { ShortReply(false, "Valeur manquante."); return false; }
   if(!ParseValue(id, val, v)) { ShortReply(false, "Valeur non reconnue pour " + g_pName[id]); return false; }
   if(!SetParam(id, v, err)) { ShortReply(false, err); return false; }
   return true;
}

bool TgExtraCommand(string cmd, string arg, string text)
{
   //--- réglages rapides
   if(cmd == "/lot")
   {
      if(arg == "") { TgShowQuick(); return true; }
      if(ShortSetNum(P_LOT, arg)) ShortReply(true, "Lot : " + PValStr(P_LOT));
      return true;
   }
   if(cmd == "/tp")
   {
      if(arg == "") { TgShowQuick(); return true; }
      string a = arg;
      StringToLower(a);
      bool atr = (StringFind(a, "atr") == 0);
      if(atr) { a = StringSubstr(a, 3); StringTrimLeft(a); }
      string err = "";
      if(atr) { SetParam(P_DYNTP, 1.0, err); if(ShortSetNum(P_ATRMULT, a)) ShortReply(true, "TP dynamique : ATR " + PValStr(P_ATRMULT)); }
      else    { SetParam(P_DYNTP, 0.0, err); if(ShortSetNum(P_TPFIXED, a)) ShortReply(true, "TP fixe : " + PValStr(P_TPFIXED)); }
      return true;
   }
   if(cmd == "/grille" || cmd == "/grid")
   {
      if(arg == "") { TgShowQuick(); return true; }
      string a = arg;
      StringToLower(a);
      bool atr = (StringFind(a, "atr") == 0);
      if(atr) { a = StringSubstr(a, 3); StringTrimLeft(a); }
      string err = "";
      if(atr) { SetParam(P_DYNGRID, 1.0, err); if(ShortSetNum(P_GRIDATR, a)) ShortReply(true, "Grille dynamique : ATR " + PValStr(P_GRIDATR)); }
      else    { SetParam(P_DYNGRID, 0.0, err); if(ShortSetNum(P_GRID, a)) ShortReply(true, "Grille fixe : " + PValStr(P_GRID)); }
      return true;
   }
   if(cmd == "/spread")
   {
      if(arg == "") { TgShowQuick(); return true; }
      if(ShortSetNum(P_MAXSPREAD, arg)) ShortReply(true, "Spread max : " + PValStr(P_MAXSPREAD));
      return true;
   }

   //--- journal / stats / outils
   if(cmd == "/stats" || cmd == "/statistiques" || cmd == "/historique" || cmd == "/series") { TgQueue(TodayReportText("📅 RAPPORT DU JOUR"), ToolsBack("rp")); return true; }
   if(cmd == "/journal")                         { TgQueue(FilterJournalText(), ToolsBack("fj")); return true; }
   if(cmd == "/multi")                           { TgQueue(MultiText(), ToolsBack("mu")); return true; }
   if(cmd == "/outils" || cmd == "/tools")       { TgShowTools(); return true; }
   if(cmd == "/rapide")                          { TgShowQuick(); return true; }
   if(cmd == "/news")                            { TgShowNews(); return true; }
   if(cmd == "/graph" || cmd == "/graphique" || cmd == "/capture")
   {
      g_shotReq = "1";
      TgQueue("📸 Capture en route…");
      return true;
   }
   if(cmd == "/courbe" || cmd == "/equity")
   {
      g_curveReq = "1";
      TgQueue("📈 Courbe en route…");
      return true;
   }
   if(cmd == "/vacances")
   {
      string a = arg;
      StringToLower(a);
      if(a == "on")  { VacationSet(true); return true; }
      if(a == "off") { VacationSet(false); return true; }
      TgShowVacation();
      return true;
   }
   if(cmd == "/rapport")
   {
      TgQueue(TodayReportText("📅 RAPPORT DU JOUR"), ToolsBack("rp"));
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
