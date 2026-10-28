//+------------------------------------------------------------------+
//| SX_TgBot.mqh - ScalpX v4.5                                       |
//| Bot Telegram 100 % français, menus à boutons intégrés (inline,   |
//| le message est modifié sur place : plus de spam). TOUS les       |
//| paramètres du robot sont modifiables, par catégorie.             |
//+------------------------------------------------------------------+
#define AW_NONE    0
#define AW_PARAM   1
#define AW_GOAL    2
#define AW_PRESET  3
#define AW_PIN     4

//--- Constructeurs de claviers
string Btn(string text, string data)
{
   return "{\"text\":\"" + JsonEsc(text) + "\",\"callback_data\":\"" + data + "\"}";
}

string KRow(string a, string b = "", string c = "", string d = "")
{
   string r = "[" + a;
   if(b != "") r += "," + b;
   if(c != "") r += "," + c;
   if(d != "") r += "," + d;
   return r + "]";
}

void AddRow(string &rows, string row)
{
   if(rows != "") rows += ",";
   rows += row;
}

string KB(string rows) { return "{\"inline_keyboard\":[" + rows + "]}"; }

string BackRow(string data = "m") { return KRow(Btn("⬅️ Retour", data), Btn("🏠 Menu", "m")); }

//--- Textes communs
string TfLabel() { return g_tfName[TfToIndex(g_sigTF)]; }

string StateLabel()
{
   if(!g_goalActive) return "⚪ Aucun objectif — le robot ne peut pas démarrer";
   if(!g_enabled)    return (g_pauseUntil > 0 ? "⏸ EN PAUSE jusqu'à " + TimeToString(g_pauseUntil, TIME_MINUTES) : "⏸ EN PAUSE") + (g_vacation ? " · 🏖 vacances" : "");
   if(g_vacation)    return "🏖 MODE VACANCES — aucune nouvelle série";
   if(st_posCount == 0 && g_blockReason != "") return "🟡 ACTIF — en attente : " + g_blockReason;
   return "🟢 ACTIF";
}

string MainText()
{
   RefreshState();
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   string s = "🏠 SCALPX " + SX_VERSION + " · " + _Symbol + "\n" + StateLabel() +
              "\n💼 Balance " + F2(bal) + " " + Cur();
   if(g_goalActive) s += " · 🎯 " + F0(GoalProgressPct()) + " %";
   return s;
}

string TgMainMenuMarkup(bool armed = false)
{
   string rows = "";
   if(!g_goalActive) AddRow(rows, KRow(Btn("🎯 Définir un objectif", "gl")));
   else AddRow(rows, KRow(g_enabled ? Btn("⏸ Pause", "pa") : Btn("▶️ Démarrer", "go"),
                          Btn(armed ? "⚠️ CONFIRMER ?" : "🛑 Tout fermer", armed ? "cy" : "ca")));
   AddRow(rows, KRow(Btn("📈 Statut", "st"), Btn("📋 Positions", "po")));
   AddRow(rows, KRow(Btn("🎯 Objectif", "gl"), Btn("🛡️ Filtres", "fl")));
   AddRow(rows, KRow(Btn("📅 Rapport du jour", "rp"), Btn("🧰 Outils", "tl")));
   AddRow(rows, KRow(Btn("⚙️ Réglages", "se"), Btn("📊 Panneau", "pb")));
   return KB(rows);
}

void TgShowMain(long edit = 0) { TgQueue(MainText(), TgMainMenuMarkup(), edit); }

//--- Statut détaillé
string StatusText()
{
   RefreshState();
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   double eq  = AccountInfoDouble(ACCOUNT_EQUITY);
   string s = "📈 STATUT DÉTAILLÉ\n━━━━━━━━━━━━━━━━━━\n" + StateLabel();
   s += "\n\n💼 COMPTE\n   Balance : " + F2(bal) + " | Equity : " + F2(eq) + " " + Cur() +
        "\n   Marge libre : " + F2(AccountInfoDouble(ACCOUNT_MARGIN_FREE)) + " " + Cur();
   if(g_goalActive)
      s += "\n\n🎯 OBJECTIF\n   " + F2(g_goalInit) + " → " + F2(g_goalTarget) + " " + Cur() + " (" + Sgn(GoalAmount()) + ")" +
           "\n   Progression : " + F1(GoalProgressPct()) + " % | reste " + F2(g_goalTarget - bal) +
           "\n   Drawdown max de l'objectif : " + F1(g_goalMaxDD) + " %";
   s += "\n\n📊 SÉRIE EN COURS";
   if(st_posCount > 0)
   {
      s += "\n   " + string(st_posType == POSITION_TYPE_BUY ? "📈 ACHAT" : "📉 VENTE") + " × " + IntegerToString(st_posCount) +
           " | " + F2(st_posLots) + " lot" +
           "\n   Prix moyen : " + PX(st_avg) +
           "\n   TP : " + (g_tpPrice > 0 ? PX(g_tpPrice) + " (" + F0(g_tpDistPts) + " pts)" : "en calcul") +
           "\n   Flottant : " + Sgn(st_floating) + " " + Cur() +
           "\n   Grille : " + IntegerToString(st_pendCount) + "/" + IntegerToString(PInt(P_MAXPEND)) + " ordres";
   }
   else s += "\n   Aucune position" + string(st_pendCount > 0 ? " (" + IntegerToString(st_pendCount) + " ordre(s) en attente)" : "");
   s += "\n\n📉 MARCHÉ (" + TfLabel() + ", bougie " + (BarShift() == 0 ? "en cours" : "clôturée") + ")" +
        "\n   EMA " + IntegerToString(PInt(P_FAST)) + " : " + PX(mk_fast) + " | EMA " + IntegerToString(PInt(P_SLOW)) + " : " + PX(mk_slow) +
        "\n   Signal : " + string(mk_fast > mk_slow ? "📈 ACHAT" : (mk_fast < mk_slow ? "📉 VENTE" : "—")) +
        "\n   ATR : " + F0(mk_atrPts) + " pts | Spread : " + F0(mk_spread) + "/" + IntegerToString(PInt(P_MAXSPREAD)) + " pts" +
        "\n   Marché : " + string(f_marketOpen ? "🟢 ouvert" : "🔴 fermé");
   s += "\n\n🛡️ FILTRES";
   s += "\n   Volatilité : " + string(!PBool(P_F_ATR) ? "⚪ off" : (f_ok[0] ? "✅ OK" : "⛔ " + f_why[0]));
   s += "\n   Prix/EMA : " + string(!PBool(P_F_EMA) ? "⚪ off" : (f_ok[1] ? "✅ OK" : "⛔ " + f_why[1]));
   s += "\n   Volume : " + string(!PBool(P_F_VOL) ? "⚪ off" : (f_ok[2] ? "✅ OK" : "⛔ " + f_why[2]));
   s += "\n   Horaire : " + string(!PBool(P_F_TIME) ? "⚪ off" : (f_timeBlocked ? "⛔ bloqué" : "✅ OK"));
   s += "\n   EMA " + TfNameP(P_LOTF) + " : " + string(!PBool(P_F_LO) ? "⚪ off" : (f_ok[3] ? "✅ OK" : "⛔ " + f_why[3]));
   s += "\n   EMA " + TfNameP(P_HITF) + " : " + string(!PBool(P_F_HI) ? "⚪ off" : (f_ok[4] ? "✅ OK" : "⛔ " + f_why[4]));
   s += "\n   Bougie " + TfNameP(P_HITF) + " : " + string(!PBool(P_F_CAN) ? "⚪ off" : (f_ok[5] ? "✅ OK" : "⛔ " + f_why[5]));
   int tot = g_sesWins + g_sesLosses;
   s += "\n\n📅 SESSION\n   Trades : " + IntegerToString(tot) + " (✅ " + IntegerToString(g_sesWins) + " / ❌ " + IntegerToString(g_sesLosses) + ")" +
        " | Résultat : " + Sgn(g_sesProfit) + " " + Cur();
   // [ARCHI] début
   s += "\n\n🏛️ ARCHITECTURE\n   État : " + StateName(g_state) + " · cycle " + IntegerToString(g_cycle) + (g_maint ? " · ⚙️ maintenance" : "") + (g_phase500 ? " · 🔒 clôture 500" : "") +
        "\n   Prochain créneau : " + NextSlotText();
   // [ARCHI] fin
   return s;
}

void TgShowStatus(long edit = 0)
{
   TgQueue(StatusText(), KB(KRow(Btn("🔄 Actualiser", "st"), Btn("🏠 Menu", "m"))), edit);
}

//--- Positions
void TgShowPositions(long edit = 0)
{
   RefreshState();
   string s = "📋 POSITIONS & ORDRES\n━━━━━━━━━━━━━━━━━━";
   int shown = 0;
   for(int i = 0; i < PositionsTotal() && shown < 12; i++)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      bool buy = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
      double pl = PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
      s += "\n" + string(pl >= 0 ? "🟢 " : "🔴 ") + (buy ? "ACHAT " : "VENTE ") + F2(PositionGetDouble(POSITION_VOLUME)) +
           " @ " + PX(PositionGetDouble(POSITION_PRICE_OPEN)) + " → " + Sgn(pl);
      shown++;
   }
   if(st_posCount == 0) s += "\nAucune position ouverte";
   else if(st_posCount > shown) s += "\n… et " + IntegerToString(st_posCount - shown) + " autre(s)";
   s += "\n\n🧱 Ordres en attente : " + IntegerToString(st_pendCount) + "/" + IntegerToString(PInt(P_MAXPEND));
   if(st_pendCount > 0) s += "\n   De " + PX(st_lowPend) + " à " + PX(st_highPend);
   if(st_posCount > 0)
      s += "\n\n📊 Total : " + F2(st_posLots) + " lot | Flottant " + Sgn(st_floating) + " " + Cur() +
           "\n💲 Prix moyen : " + PX(st_avg) + " | 🎯 TP : " + (g_tpPrice > 0 ? PX(g_tpPrice) : "—");
   TgQueue(s, KB(KRow(Btn("🔄 Actualiser", "po"), Btn("🏠 Menu", "m"))), edit);
}

//--- Objectif
void TgShowGoal(long edit = 0)
{
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   string s = "🎯 OBJECTIF\n━━━━━━━━━━━━━━━━━━\n💼 Balance : " + F2(bal) + " " + Cur();
   if(g_goalActive)
      s += "\n\n✅ Objectif actif\n   " + F2(g_goalInit) + " → " + F2(g_goalTarget) + " " + Cur() + " (" + Sgn(GoalAmount()) + ")" +
           "\n   Progression : " + F1(GoalProgressPct()) + " % | reste " + F2(g_goalTarget - bal) +
           "\n\n💡 Choisis un montant pour modifier la cible.";
   else
      s += "\n\n⚠️ Aucun objectif actif.\n💡 Choisis un montant à gagner ou un pourcentage :";
   string rows = "";
   AddRow(rows, KRow(Btn("+5", "gs:5"), Btn("+10", "gs:10"), Btn("+20", "gs:20"), Btn("+30", "gs:30")));
   AddRow(rows, KRow(Btn("+1 %", "gs:+1%"), Btn("+2 %", "gs:+2%"), Btn("+3 %", "gs:+3%"), Btn("+5 %", "gs:+5%")));
   AddRow(rows, KRow(Btn("+10 %", "gs:+10%"), Btn("+20 %", "gs:+20%"), Btn("+30 %", "gs:+30%")));
   //--- balances cibles rondes, proches de la balance actuelle
   double rt[3];
   RoundTargets(bal, rt);
   string rb[3]; int nrb = 0;
   for(int q = 0; q < 3; q++)
      if(rt[q] > 0) { rb[nrb] = Btn("→ " + F0(rt[q]) + " " + Cur(), "gs:=" + F0(rt[q])); nrb++; }
   if(nrb == 3)      AddRow(rows, KRow(rb[0], rb[1], rb[2]));
   else if(nrb == 2) AddRow(rows, KRow(rb[0], rb[1]));
   else if(nrb == 1) AddRow(rows, KRow(rb[0]));
   if(g_goalActive) AddRow(rows, KRow(Btn("✏️ Autre valeur", "gc"), Btn("❌ Annuler l'objectif", "gx")));
   else             AddRow(rows, KRow(Btn("✏️ Autre valeur", "gc")));
   AddRow(rows, KRow(Btn("🏠 Menu", "m")));
   TgQueue(s, KB(rows), edit);
}

//--- Paramètres : liste des catégories
void TgShowSettings(long edit = 0)
{
   string s = "⚙️ RÉGLAGES\n" + string(g_configOK ? "✅ Paramètres validés" : "⚠️ Paramètres non validés (requis avant de démarrer)");
   string rows = "";
   AddRow(rows, KRow(Btn("⚡ Rapide", "qs"), Btn("💾 Presets", "ps")));
   for(int g = 0; g < G_COUNT; g += 2)
   {
      string a = Btn(g_groupName[g], "g:" + IntegerToString(g));
      string b = (g + 1 < G_COUNT) ? Btn(g_groupName[g + 1], "g:" + IntegerToString(g + 1)) : "";
      AddRow(rows, KRow(a, b));
   }
   if(!g_configOK) AddRow(rows, KRow(Btn("✅ Valider les paramètres", "cf")));
   AddRow(rows, KRow(Btn("🏠 Menu", "m")));
   TgQueue(s, KB(rows), edit);
}

void TgShowGroup(int g, long edit = 0, string note = "")
{
   if(g < 0 || g >= G_COUNT) g = 0;
   string s = g_groupName[g];
   if(note != "") s += "\n\n" + note;
   string rows = "";
   for(int i = 0; i < P_COUNT; i++)
   {
      if(g_pGroup[i] != g) continue;
      if(g_pType[i] == PT_BOOL)
         AddRow(rows, KRow(Btn(string(PBool(i) ? "✅ " : "❌ ") + g_pName[i], "t:" + IntegerToString(i) + ":g")));
      else
         AddRow(rows, KRow(Btn(g_pName[i] + " : " + PValShort(i), "e:" + IntegerToString(i))));
   }
   AddRow(rows, KRow(Btn("⬅️ Réglages", "se"), Btn("🏠 Menu", "m")));
   TgQueue(s, KB(rows), edit);
}

string FmtStep(int id, int mult)
{
   double v = g_pStep[id] * MathAbs((double)mult);
   return (mult < 0 ? "−" : "+") + DoubleToString(v, Dec(g_pStep[id]));
}

void TgShowParam(int id, long edit = 0, string note = "")
{
   int dec = Dec(g_pStep[id]);
   string s = "⚙️ " + g_pName[id] + "\nValeur actuelle : " + PValStr(id);
   if(g_pType[id] == PT_INT || g_pType[id] == PT_DBL)
      s += "\nDéfaut : " + DoubleToString(g_pDef[id], dec) + g_pUnit[id] +
           " | Limites : " + DoubleToString(g_pMin[id], dec) + " – " + DoubleToString(g_pMax[id], dec);
   s += "\n\nℹ️ " + g_pHelp[id];
   if(note != "") s += "\n\n" + note;

   string sid = IntegerToString(id);
   string rows = "";
   if(g_pType[id] == PT_BOOL)
   {
      AddRow(rows, KRow(Btn(PBool(id) ? "❌ Désactiver" : "✅ Activer", "t:" + sid + ":e")));
   }
   else if(g_pType[id] == PT_ENUM)
   {
      string parts[];
      int n = StringSplit(g_pOpt[id], '|', parts);
      string row = "";
      for(int k = 0; k < n; k++)
      {
         string lbl = (k == PInt(id) ? "✅ " : "") + parts[k];
         row += (row != "" ? "," : "") + Btn(lbl, "o:" + sid + ":" + IntegerToString(k));
         if((k + 1) % 3 == 0 || k == n - 1) { AddRow(rows, "[" + row + "]"); row = ""; }
      }
   }
   else
   {
      AddRow(rows, KRow(Btn(FmtStep(id, -10), "i:" + sid + ":-10"), Btn(FmtStep(id, -1), "i:" + sid + ":-1"),
                        Btn(FmtStep(id, 1), "i:" + sid + ":1"), Btn(FmtStep(id, 10), "i:" + sid + ":10")));
      AddRow(rows, KRow(Btn("✏️ Saisir", "w:" + sid), Btn("↩️ Défaut", "d:" + sid)));
   }
   if(g_pType[id] == PT_BOOL || g_pType[id] == PT_ENUM) AddRow(rows, KRow(Btn("↩️ Défaut", "d:" + sid)));
   AddRow(rows, KRow(Btn("⬅️ Retour", "g:" + IntegerToString(g_pGroup[id])), Btn("🏠 Menu", "m")));
   TgQueue(s, KB(rows), edit);
}

//--- Filtres : écran avec bascules (voir SX_FilterBoard.mqh)
void TgShowFilters(long edit = 0)
{
   TgQueue(FilterBoardText(), FilterBoardMarkup(), edit);
}

//--- Presets
void TgShowPresets(long edit = 0, string note = "")
{
   PresetList();
   string s = "💾 PRESETS\nSauvegarde tous les réglages sous un nom, puis recharge-les en un clic.";
   if(g_psCount == 0) s += "\n\nAucun preset enregistré.";
   if(note != "") s += "\n\n" + note;
   string rows = "";
   for(int i = 0; i < g_psCount; i++)
      AddRow(rows, KRow(Btn("📥 " + g_psNames[i], "pl:" + IntegerToString(i)), Btn("🗑", "pd:" + IntegerToString(i))));
   AddRow(rows, KRow(Btn("💾 Sauvegarder", "pn"), Btn("♻️ Valeurs par défaut", "rs")));
   AddRow(rows, KRow(Btn("⬅️ Réglages", "se"), Btn("🏠 Menu", "m")));
   TgQueue(s, KB(rows), edit);
}

void TgShowHelp(long edit = 0)
{
   string s = "❓ AIDE — SCALPX " + SX_VERSION +
              "\n\n📌 POUR DÉMARRER" +
              "\n1️⃣ 🎯 Objectif : choisis un montant ou un %" +
              "\n2️⃣ ⚙️ Réglages : vérifie puis ✅ Valider" +
              "\n3️⃣ ▶️ Démarrer" +
              "\n\n📅 RAPPORT DU JOUR" +
              "\nBilan de la journée en cours uniquement : gain, séries, drawdown, filtres, courbe d'equity. Repart de zéro chaque jour." +
              "\n\n🕐 HEURES" +
              "\nToutes les heures sont dans le fuseau choisi (France par défaut), avec heure d'été automatique : Réglages → Horaire → Fuseau horaire." +
              "\n\n📊 PANNEAU" +
              "\nPetit résumé toujours en bas de la discussion : balance, equity, objectif, drawdown, filtres. Actualisé toutes les 30 s, remis en dernier toutes les 5 min (sans notification). Bouton 📊 Panneau pour l'afficher tout de suite." +
              "\n\n🔔 ALERTES" +
              "\n🔴 critique = avec son · 🟡 important = silencieux la nuit · ⚪ info = toujours silencieux" +
              "\nRéglages : ⚙️ Réglages → 🧠 Alertes & live" +
              "\n\n🔐 Pilotage par le bot central uniquement (chat autorisé côté bot).";
   TgQueue(s, KB(KRow(Btn("🧰 Outils", "tl"), Btn("🏠 Menu", "m"))), edit);
}

//+------------------------------------------------------------------+
//| Actions                                                          |
//+------------------------------------------------------------------+
void TgShowConfirmStart(long edit = 0)
{
   string s = "✅ VALIDATION AVANT DÉMARRAGE\n━━━━━━━━━━━━━━━━━━" +
              "\n📐 Timeframe : " + TfLabel() + " | EMA " + IntegerToString(PInt(P_FAST)) + "/" + IntegerToString(PInt(P_SLOW)) +
              "\n💰 Lot : " + F2(PDbl(P_LOT)) + " (× " + F1(PDbl(P_LOTMULT)) + " tous les " + IntegerToString(PInt(P_TBM)) + ")" +
              "\n📏 Grille : " + (PBool(P_DYNGRID) ? "ATR × " + F1(PDbl(P_GRIDATR)) + " (" + IntegerToString(PInt(P_GRIDMIN)) + "–" + IntegerToString(PInt(P_GRIDMAX)) + " pts)" : IntegerToString(PInt(P_GRID)) + " pts") + " (× " + F2(PDbl(P_GRIDMULT)) + "), " + IntegerToString(PInt(P_MAXPEND)) + " ordres max" +
              "\n🎯 TP : " + (PBool(P_DYNTP) ? "ATR × " + F1(PDbl(P_ATRMULT)) + " (" + IntegerToString(PInt(P_TPMIN)) + "–" + IntegerToString(PInt(P_TPMAX)) + " pts)"
                                              : F0(PDbl(P_TPFIXED)) + " pts") +
              "\n🛡️ Filtres : ATR " + string(PBool(P_F_ATR) ? "✅" : "❌") + " Prix/EMA " + string(PBool(P_F_EMA) ? "✅" : "❌") +
              " Volume " + string(PBool(P_F_VOL) ? "✅" : "❌") + " Horaire " + string(PBool(P_F_TIME) ? "✅" : "❌") +
              "\n🔭 Multi-TF : EMA " + TfNameP(P_LOTF) + " " + string(PBool(P_F_LO) ? "✅" : "❌") + " EMA " + TfNameP(P_HITF) + " " + string(PBool(P_F_HI) ? "✅" : "❌") +
              " Bougie " + TfNameP(P_HITF) + " " + string(PBool(P_F_CAN) ? "✅" : "❌") +
              "\n⚠️ Drawdown : " + F0(PDbl(P_DDTHR)) + " % | SL urgence : " + (PBool(P_EMSL) ? F0(PDbl(P_EMSLPCT)) + " %" : "off") +
              "\n🎯 Objectif : " + (g_goalActive ? Sgn(GoalAmount()) + " " + Cur() : "aucun") +
              "\n\n👉 Confirme pour démarrer le robot avec ces réglages.";
   string rows = "";
   AddRow(rows, KRow(Btn("✅ Valider et démarrer", "gv")));
   AddRow(rows, KRow(Btn("⚙️ Modifier", "se"), Btn("🏠 Menu", "m")));
   TgQueue(s, KB(rows), edit);
}

void ActStart(long edit = 0)
{
   RefreshState();
   if(!g_goalActive)
   {
      TgQueue("❌ Impossible de démarrer\n⚠️ Définis d'abord un objectif.", KB(KRow(Btn("🎯 Définir un objectif", "gl"), Btn("🏠 Menu", "m"))), edit);
      return;
   }
   if(!g_configOK) { TgShowConfirmStart(edit); return; }
   string w;
   if(!TradeEnvOK(w))
   {
      TgQueue("❌ Impossible de démarrer\n⛔ " + w, KB(KRow(Btn("🔄 Réessayer", "go"), Btn("🏠 Menu", "m"))), edit);
      return;
   }
   g_enabled = true;
   g_stoppedManually = false;
   GlobalVariableDel(GVN("stopped"));
   g_cooldownUntilMs = 0;
   g_failCount = 0;
   g_nextTradeMs = 0;
   string s = "▶️ ROBOT DÉMARRÉ\n━━━━━━━━━━━━━━━━━━\n🎯 Objectif : " + Sgn(GoalAmount()) + " " + Cur() +
              " (cible " + F2(g_goalTarget) + ")\n📐 " + TfLabel() + " | EMA " + IntegerToString(PInt(P_FAST)) + "/" + IntegerToString(PInt(P_SLOW)) +
              "\n💰 Lot " + F2(PDbl(P_LOT));
   if(!f_marketOpen) s += "\n\n🔴 Le marché est actuellement fermé : le robot attendra l'ouverture.";
   TgQueue(s, TgMainMenuMarkup(), edit);
}

void ActPause(long edit = 0)
{
   RefreshState();
   g_enabled = false;
   g_stoppedManually = true;
   GlobalVariableSet(GVN("stopped"), 1);
   TgQueue("⏸ ROBOT EN PAUSE\n━━━━━━━━━━━━━━━━━━\n📊 Positions : " + IntegerToString(st_posCount) + " | 🧱 Ordres : " + IntegerToString(st_pendCount) +
           "\n\nℹ️ Plus aucune nouvelle position ni ordre de grille. Les positions ouvertes restent gérées (TP, objectif, protections)." +
           "\nPour tout liquider : 🛑 Tout fermer.", TgMainMenuMarkup(), edit);
}

void TgShowCloseConfirm(long edit = 0)
{
   RefreshState();
   string s = "🛑 TOUT FERMER ?\n━━━━━━━━━━━━━━━━━━\n📊 " + IntegerToString(st_posCount) + " position(s) | " + F2(st_posLots) + " lot" +
              "\n🧱 " + IntegerToString(st_pendCount) + " ordre(s) en attente" +
              "\n💰 Flottant : " + Sgn(st_floating) + " " + Cur() +
              "\n\n⚠️ Les ordres en attente seront supprimés puis toutes les positions fermées au marché.\nLe robot sera mis en pause.";
   string rows = "";
   AddRow(rows, KRow(Btn("✅ Oui, tout fermer", "cy"), Btn("❌ Annuler", "m")));
   g_confirmCloseUntil = GetTickCount64() + 60000;
   TgQueue(s, KB(rows), edit);
}

void ActCloseAll(long edit = 0)
{
   if(GetTickCount64() > g_confirmCloseUntil) { TgShowCloseConfirm(edit); return; }
   g_enabled = false;
   g_confirmCloseUntil = 0;
   int n = CloseAllFast("fermeture manuelle (Telegram)");
   RefreshState();
   string s = "🛑 FERMETURE EFFECTUÉE\n━━━━━━━━━━━━━━━━━━\n🔒 " + IntegerToString(n) + " élément(s) fermé(s)";
   if(st_posCount + st_pendCount > 0) s += "\n⚠️ Il reste " + IntegerToString(st_posCount + st_pendCount) + " élément(s) : vérifie dans MT5.";
   s += "\n⏸ Robot en pause.\n💼 Balance : " + F2(AccountInfoDouble(ACCOUNT_BALANCE)) + " " + Cur();
   TgQueue(s, TgMainMenuMarkup(), edit);
}

//+------------------------------------------------------------------+
//| Valeurs saisies au clavier                                       |
//+------------------------------------------------------------------+
bool ParseValue(int id, string txt, double &v)
{
   StringTrimLeft(txt); StringTrimRight(txt);
   StringToLower(txt);
   StringReplace(txt, ",", ".");
   if(g_pType[id] == PT_BOOL)
   {
      if(txt == "on" || txt == "oui" || txt == "1" || txt == "true" || txt == "actif") { v = 1; return true; }
      if(txt == "off" || txt == "non" || txt == "0" || txt == "false" || txt == "inactif") { v = 0; return true; }
      return false;
   }
   if(StringLen(txt) == 0) return false;
   ushort c = StringGetCharacter(txt, 0);
   if(!((c >= '0' && c <= '9') || c == '-' || c == '.')) return false;
   v = StringToDouble(txt);
   return true;
}

void TgPromptParam(int id, string note = "")
{
   int dec = Dec(g_pStep[id]);
   string s = "✏️ " + g_pName[id] + "\nValeur actuelle : " + PValStr(id) +
              "\nLimites : " + DoubleToString(g_pMin[id], dec) + " – " + DoubleToString(g_pMax[id], dec) + g_pUnit[id] +
              (note != "" ? "\n\n" + note : "") + "\n\n📝 Envoie la nouvelle valeur :";
   TgQueue(s, KB(KRow(Btn("❌ Annuler", "e:" + IntegerToString(id)))));
}

void TgApplyAwait(string text)
{
   if(g_awMode == AW_PARAM && g_awParam >= 0)
   {
      int id = g_awParam;
      double v;
      if(!ParseValue(id, text, v)) { TgPromptParam(id, "❌ Valeur non reconnue, envoie un nombre."); return; }
      string err;
      if(!SetParam(id, v, err)) { TgPromptParam(id, "❌ " + err); return; }
      g_awMode = AW_NONE; g_awParam = -1;
      TgShowParam(id, 0, "✅ Appliqué : " + PValStr(id));
   }
   else if(g_awMode == AW_GOAL)
   {
      string msg;
      bool ok = GoalSetFromArg(text, msg);
      if(!ok) { TgQueue("❌ " + msg + "\n\n📝 Envoie une autre valeur (ex : 10, +5% ou =1250) :", KB(KRow(Btn("❌ Annuler", "gl")))); return; }
      g_awMode = AW_NONE;
      TgQueue("✅ " + msg, KB(KRow(g_configOK ? Btn("▶️ Démarrer", "go") : Btn("✅ Valider et démarrer", "go"), Btn("🏠 Menu", "m"))));
   }
   else if(g_awMode == AW_PRESET)
   {
      g_awMode = AW_NONE;
      if(PresetSave(text)) TgShowPresets(0, "✅ Preset « " + SanitizeName(text) + " » enregistré.");
      else TgShowPresets(0, "❌ Nom invalide (lettres, chiffres, - et _ uniquement).");
   }
}

//+------------------------------------------------------------------+
//| Messages texte                                                   |
//+------------------------------------------------------------------+
void TgShowParamList()
{
   string s = "📋 LISTE DES PARAMÈTRES\n(modifie-les depuis le bot : ⚙️ Paramètres)\n";
   int lastG = -1;
   for(int i = 0; i < P_COUNT; i++)
   {
      if(g_pGroup[i] != lastG) { lastG = g_pGroup[i]; s += "\n" + g_groupName[lastG] + "\n"; }
      s += "• " + g_pKey[i] + " = " + PValShort(i) + "\n";
   }
   TgQueue(s, KB(KRow(Btn("⚙️ Paramètres", "se"), Btn("🏠 Menu", "m"))));
}

void TgOnText(string text)
{
   StringTrimLeft(text); StringTrimRight(text);
   if(text == "") return;
   string low = text;
   StringToLower(low);

   // le clavier persistant envoie « 🏠 Menu »
   if(StringFind(low, "menu") >= 0 && StringGetCharacter(low, 0) != '/') { g_awMode = AW_NONE; TgShowMain(); return; }

   if(StringGetCharacter(text, 0) != '/')
   {
      if(g_awMode != AW_NONE) { TgApplyAwait(text); return; }
      TgQueue("🤖 Utilise les boutons du menu.", KB(KRow(Btn("🏠 Menu", "m"))));
      return;
   }

   g_awMode = AW_NONE;                       // une commande annule la saisie en cours
   string cmd = low, arg = "";
   int sp = StringFind(text, " ");
   if(sp > 0) { cmd = StringSubstr(low, 0, sp); arg = StringSubstr(text, sp + 1); StringTrimLeft(arg); StringTrimRight(arg); }
   int at = StringFind(cmd, "@");
   if(at > 0) cmd = StringSubstr(cmd, 0, at);
   if(TgExtraCommand(cmd, arg, text)) return;       // commandes courtes, outils, PIN

   if(cmd == "/start" || cmd == "/menu" || cmd == "/m")             { TgShowMain(); return; }
   if(cmd == "/demarrer" || cmd == "/go" || cmd == "/run")          { ActStart(); return; }
   if(cmd == "/pause" || cmd == "/stop" || cmd == "/arreter")       { ActPause(); return; }
   if(cmd == "/fermer_tout" || cmd == "/closeall" || cmd == "/stopall" || cmd == "/tout_fermer") { TgShowCloseConfirm(); return; }
   if(cmd == "/statut" || cmd == "/status" || cmd == "/s")          { TgShowStatus(); return; }
   if(cmd == "/positions" || cmd == "/pos" || cmd == "/ordres")     { TgShowPositions(); return; }
   if(cmd == "/params" || cmd == "/parametres")                     { TgShowParamList(); return; }
   if(cmd == "/filtres")                                            { TgShowFilters(); return; }
   if(cmd == "/risque")                                             { TgShowGroup(G_RISK); return; }
   if(cmd == "/presets")                                            { TgShowPresets(); return; }
   if(cmd == "/rapport")                                            { TgQueue(ReportText("📅 RAPPORT DE SESSION"), KB(KRow(Btn("🏠 Menu", "m")))); return; }
   if(cmd == "/aide" || cmd == "/help")                             { TgShowHelp(); return; }
   if(cmd == "/annuler_objectif")                                   { TgQueue("❌ Annuler l'objectif ?", KB(KRow(Btn("✅ Oui", "gx1"), Btn("↩️ Non", "gl")))); return; }

   if(cmd == "/objectif" || cmd == "/goal" || cmd == "/obj")
   {
      if(arg == "") { TgShowGoal(); return; }
      string msg;
      bool ok = GoalSetFromArg(arg, msg);
      TgQueue((ok ? "✅ " : "❌ ") + msg, KB(KRow(Btn(ok ? "▶️ Démarrer" : "🎯 Objectif", ok ? "go" : "gl"), Btn("🏠 Menu", "m"))));
      return;
   }

   if(cmd == "/set")
   {
      int sp2 = StringFind(arg, " ");
      if(sp2 <= 0) { TgQueue("Format : /set clé valeur\nExemple : /set lot 0.02\nListe des clés : /params"); return; }
      string key = StringSubstr(arg, 0, sp2);
      string val = StringSubstr(arg, sp2 + 1);
      int id = FindParamByKey(key);
      if(id < 0) { TgQueue("❌ Clé inconnue : " + key + "\nListe des clés : /params"); return; }
      double v;
      if(!ParseValue(id, val, v)) { TgQueue("❌ Valeur non reconnue pour " + g_pName[id]); return; }
      string err;
      if(!SetParam(id, v, err)) { TgQueue("❌ " + err); return; }
      TgShowParam(id, 0, "✅ Appliqué : " + PValStr(id));
      return;
   }

   TgQueue("🤖 Commande inconnue : " + cmd + "\nTape /aide pour la liste.", KB(KRow(Btn("🏠 Menu", "m"))));
}

//+------------------------------------------------------------------+
//| Boutons                                                          |
//+------------------------------------------------------------------+
void TgOnCallback(long msgId, string cbId, string data)
{
   string p[];
   int n = StringSplit(data, ':', p);
   if(n < 1) return;
   string c = p[0];

   if(c != "w" && c != "gc" && c != "pn") g_awMode = AW_NONE;     // quitter une saisie en cours
   if(TgExtraCallback(msgId, c, p, n)) return;                     // outils, réglages rapides, vacances, pause programmée...

   if(c == "m")  { TgShowMain(msgId); return; }
   if(c == "mn") { TgShowMain(); return; }                        // depuis le panneau : menu dans un NOUVEAU message
   if(c == "st") { TgShowStatus(msgId); return; }
   if(c == "po") { TgShowPositions(msgId); return; }
   if(c == "hp") { TgShowHelp(msgId); return; }
   if(c == "fl") { UpdateFilters(); TgShowFilters(msgId); return; }
   if(c == "se") { TgShowSettings(msgId); return; }
   if(c == "ps") { TgShowPresets(msgId); return; }
   if(c == "rp") { TgQueue(ReportText("📅 RAPPORT DE SESSION"), KB(KRow(Btn("⬅️ Outils", "tl"), Btn("🏠 Menu", "m"))), msgId); return; }

   // --- actions principales
   if(c == "go") { ActStart(msgId); return; }
   if(c == "gv") { g_configOK = true; GlobalVariableSet(GVN("cfgok"), 1); ActStart(msgId); return; }
   if(c == "cf") { g_configOK = true; GlobalVariableSet(GVN("cfgok"), 1); TgShowSettings(msgId); return; }
   if(c == "pa") { ActPause(msgId); return; }
   if(c == "ca") { TgShowCloseConfirm(msgId); return; }
   if(c == "cy") { ActCloseAll(msgId); return; }

   // --- objectif
   if(c == "gl") { TgShowGoal(msgId); return; }
   if(c == "gs" && n >= 2)
   {
      string arg = p[1];
      for(int k = 2; k < n; k++) arg += ":" + p[k];
      string msg;
      bool ok = GoalSetFromArg(arg, msg);
      string rows = "";
      if(ok) AddRow(rows, KRow(Btn(g_enabled ? "📈 Statut" : "▶️ Démarrer", g_enabled ? "st" : "go"), Btn("🏠 Menu", "m")));
      else   AddRow(rows, KRow(Btn("🎯 Objectif", "gl"), Btn("🏠 Menu", "m")));
      TgQueue((ok ? "✅ " : "❌ ") + msg, KB(rows), msgId);
      return;
   }
   if(c == "gc") { g_awMode = AW_GOAL; TgQueue("✏️ OBJECTIF PERSONNALISÉ\n━━━━━━━━━━━━━━━━━━\n• 10 → gagner 10 " + Cur() +
                   "\n• +5% → gagner 5 % de la balance\n• =1250 → balance cible 1250\n\n📝 Envoie ta valeur :", KB(KRow(Btn("❌ Annuler", "gl"))), msgId); return; }
   if(c == "gx") { TgQueue("❌ Annuler l'objectif en cours ?\n\nℹ️ Les positions ouvertes ne sont pas fermées.", KB(KRow(Btn("✅ Oui, annuler", "gx1"), Btn("↩️ Non", "gl"))), msgId); return; }
   if(c == "gx1"){ GoalCancel(); TgQueue("✅ Objectif annulé. Robot en pause.", TgMainMenuMarkup(), msgId); return; }

   // --- paramètres
   if(c == "g" && n >= 2) { TgShowGroup((int)StringToInteger(p[1]), msgId); return; }
   if(n >= 2)
   {
      int id = (int)StringToInteger(p[1]);
      if(id < 0 || id >= P_COUNT) return;
      string err = "";
      if(c == "e") { TgShowParam(id, msgId); return; }
      if(c == "w") { g_awMode = AW_PARAM; g_awParam = id; TgPromptParam(id); return; }
      if(c == "d")
      {
         double dv = g_pDef[id];
         if(!SetParam(id, dv, err)) { TgShowParam(id, msgId, "❌ " + err); return; }
         TgShowParam(id, msgId, "✅ Valeur par défaut rétablie");
         return;
      }
      if(c == "t")
      {
         if(!SetParam(id, PBool(id) ? 0.0 : 1.0, err)) { TgShowParam(id, msgId, "❌ " + err); return; }
         string view = (n >= 3) ? p[2] : "e";
         if(view == "g") TgShowGroup(g_pGroup[id], msgId);
         else if(view == "f") { UpdateFilters(); TgShowFilters(msgId); }
         else if(view == "n") TgShowNews(msgId);
         else TgShowParam(id, msgId, "✅ Appliqué : " + PValStr(id));
         return;
      }
      if(c == "i" && n >= 3)
      {
         double nv = g_pVal[id] + (double)StringToInteger(p[2]) * g_pStep[id];
         nv = NormalizeDouble(nv, 8);
         if(nv < g_pMin[id]) nv = g_pMin[id];
         if(nv > g_pMax[id]) nv = g_pMax[id];
         if(!SetParam(id, nv, err)) { TgShowParam(id, msgId, "❌ " + err); return; }
         TgShowParam(id, msgId, "✅ Appliqué : " + PValStr(id));
         return;
      }
      if(c == "o" && n >= 3)
      {
         if(!SetParam(id, (double)StringToInteger(p[2]), err)) { TgShowParam(id, msgId, "❌ " + err); return; }
         if(n >= 4 && p[3] == "n") { TgShowNews(msgId); return; }
         TgShowParam(id, msgId, "✅ Appliqué : " + PValStr(id));
         return;
      }
   }

   // --- presets
   if(c == "pn") { g_awMode = AW_PRESET; TgQueue("💾 NOUVEAU PRESET\n\n📝 Envoie un nom (lettres, chiffres, - et _) :", KB(KRow(Btn("❌ Annuler", "ps"))), msgId); return; }
   if(c == "pl" && n >= 2)
   {
      PresetList();
      int i = (int)StringToInteger(p[1]);
      if(i < 0 || i >= g_psCount) { TgShowPresets(msgId, "❌ Preset introuvable"); return; }
      string nm = g_psNames[i];
      if(PresetLoad(nm)) { g_configOK = false; GlobalVariableDel(GVN("cfgok")); TgShowPresets(msgId, "✅ Preset « " + nm + " » chargé.\n⚠️ Valide les paramètres avant de démarrer."); }
      else TgShowPresets(msgId, "❌ Chargement impossible");
      return;
   }
   if(c == "pd" && n >= 2)
   {
      PresetList();
      int i = (int)StringToInteger(p[1]);
      if(i >= 0 && i < g_psCount) { string nm = g_psNames[i]; PresetDelete(nm); TgShowPresets(msgId, "🗑 Preset « " + nm + " » supprimé."); }
      else TgShowPresets(msgId, "❌ Preset introuvable");
      return;
   }
   if(c == "rs") { TgQueue("♻️ Remettre TOUS les paramètres aux valeurs par défaut (celles des inputs du robot) ?", KB(KRow(Btn("✅ Oui", "rs1"), Btn("↩️ Non", "ps"))), msgId); return; }
   if(c == "rs1")
   {
      ResetParamsToDefaults();
      RebuildIndicators();
      g_tpSig = -1;
      ChartRefreshAll(true);
      g_configOK = false;
      GlobalVariableDel(GVN("cfgok"));
      TgShowPresets(msgId, "✅ Valeurs par défaut rétablies.\n⚠️ Valide les paramètres avant de démarrer.");
      return;
   }
}

//+------------------------------------------------------------------+
