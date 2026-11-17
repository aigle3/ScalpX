//+------------------------------------------------------------------+
//| SX_Commands.mqh - ScalpX v5                                      |
//| Textes et actions des commandes du bot central (fichiers cmd_*). |
//| Remplace l'ancienne interface Telegram de l'EA (menus, boutons,  |
//| saisies, PIN, commandes courtes) : c'est le bot qui pilote.      |
//+------------------------------------------------------------------+

string TfLabel() { return g_tfName[TfToIndex(g_sigTF)]; }

string StateLabel()
{
   if(!g_goalActive) return "⚪ Aucun objectif — le robot ne peut pas démarrer";
   if(!g_enabled)    return (g_pauseUntil > 0 ? "⏸ EN PAUSE jusqu'à " + TimeToString(g_pauseUntil, TIME_MINUTES) : "⏸ EN PAUSE") + (g_vacation ? " · 🏖 vacances" : "");
   if(g_vacation)    return "🏖 MODE VACANCES — aucune nouvelle série";
   if(st_posCount == 0 && g_blockReason != "") return "🟡 ACTIF — en attente : " + g_blockReason;
   return "🟢 ACTIF";
}

//--- Statut détaillé (commande STATUT)
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
   for(int k = 0; k < FB_N; k++)
   {
      int i = FbOrder(k);
      s += "\n   " + FbName(i) + " : " + string(!FbOn(i) ? "⚪ off" : (FbOK(i) ? "✅ OK" : "⛔ " + FbWhy(i)));
   }
   int tot = g_sesWins + g_sesLosses;
   s += "\n\n📅 SESSION\n   Trades : " + IntegerToString(tot) + " (✅ " + IntegerToString(g_sesWins) + " / ❌ " + IntegerToString(g_sesLosses) + ")" +
        " | Résultat : " + Sgn(g_sesProfit) + " " + Cur();
   // [ARCHI] début
   s += "\n\n🏛️ ARCHITECTURE\n   État : " + StateName(g_state) + " · cycle " + IntegerToString(g_cycle) + (g_maint ? " · ⚙️ maintenance" : "") + (g_phase500 ? " · 🔒 clôture 500" : "") +
        "\n   Prochain créneau : " + NextSlotText();
   // [ARCHI] fin
   return s;
}

//--- Positions et ordres (commande POSITIONS)
string PositionsText()
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
   return s;
}

//--- Objectif (commande OBJECTIF sans argument)
string GoalText()
{
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   string s = "🎯 OBJECTIF\n━━━━━━━━━━━━━━━━━━\n💼 Balance : " + F2(bal) + " " + Cur();
   if(g_goalActive)
      s += "\n\n✅ Objectif actif\n   " + F2(g_goalInit) + " → " + F2(g_goalTarget) + " " + Cur() + " (" + Sgn(GoalAmount()) + ")" +
           "\n   Progression : " + F1(GoalProgressPct()) + " % | reste " + F2(g_goalTarget - bal);
   else
      s += "\n\nAucun objectif actif. Prochain créneau : " + NextSlotText();
   return s;
}

//--- Paramètres (commandes PARAMS et RISQUE) : « • clé = valeur », lu par le bot
string ParamListText()
{
   string s = "📋 LISTE DES PARAMÈTRES\n(modifie-les depuis le bot : ⚙️ Paramètres)\n";
   int lastG = -1;
   for(int i = 0; i < P_COUNT; i++)
   {
      if(g_pGroup[i] != lastG) { lastG = g_pGroup[i]; s += "\n" + g_groupName[lastG] + "\n"; }
      s += "• " + g_pKey[i] + " = " + PValShort(i) + "\n";
   }
   return s;
}

string GroupText(int g)
{
   if(g < 0 || g >= G_COUNT) g = 0;
   string s = g_groupName[g] + "\n━━━━━━━━━━━━━━━━━━";
   for(int i = 0; i < P_COUNT; i++)
      if(g_pGroup[i] == g) s += "\n• " + g_pName[i] + " : " + PValStr(i);
   return s;
}

//--- Presets (commande PRESETS) : les noms « • nom » sont lus par le bot
string PresetsText()
{
   PresetList();
   string s = "💾 PRESETS\nSauvegarde tous les réglages sous un nom, puis recharge-les en un clic.";
   if(g_psCount == 0) s += "\n\nAucun preset enregistré.";
   for(int k = 0; k < g_psCount; k++) s += "\n• " + g_psNames[k];
   return s;
}

//--- Valeur reçue avec SET
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

//+------------------------------------------------------------------+
//| Actions (démarrer, pause, tout fermer) : renvoient le texte      |
//+------------------------------------------------------------------+
bool ActStart(string &msg)
{
   RefreshState();
   if(!g_goalActive) { msg = "❌ Impossible de démarrer : aucun objectif actif (🎯 Objectif)."; return false; }
   if(!g_configOK)   { msg = "❌ Impossible de démarrer : paramètres non validés (✅ Valider les réglages)."; return false; }
   string w;
   if(!TradeEnvOK(w)) { msg = "❌ Impossible de démarrer : " + w; return false; }
   g_enabled = true;
   g_stoppedManually = false;
   GlobalVariableDel(GVN("stopped"));
   g_cooldownUntilMs = 0;
   g_failCount = 0;
   g_nextTradeMs = 0;
   msg = "▶️ ROBOT DÉMARRÉ\n━━━━━━━━━━━━━━━━━━\n🎯 Objectif : " + Sgn(GoalAmount()) + " " + Cur() +
         " (cible " + F2(g_goalTarget) + ")\n📐 " + TfLabel() + " | EMA " + IntegerToString(PInt(P_FAST)) + "/" + IntegerToString(PInt(P_SLOW)) +
         "\n💰 Lot " + F2(PDbl(P_LOT));
   if(!f_marketOpen) msg += "\n\n🔴 Le marché est actuellement fermé : le robot attendra l'ouverture.";
   return true;
}

string ActPause()
{
   RefreshState();
   g_enabled = false;
   g_stoppedManually = true;
   GlobalVariableSet(GVN("stopped"), 1);
   return "⏸ ROBOT EN PAUSE\n━━━━━━━━━━━━━━━━━━\n📊 Positions : " + IntegerToString(st_posCount) + " | 🧱 Ordres : " + IntegerToString(st_pendCount) +
          "\n\nℹ️ Plus aucune nouvelle position ni ordre de grille. Les positions ouvertes restent gérées (TP, objectif, protections).";
}

// La confirmation est faite par le bot.
string ActCloseAll()
{
   g_enabled = false;
   int n = CloseAllFast("fermeture manuelle (bot)");
   RefreshState();
   string s = "🛑 FERMETURE EFFECTUÉE\n━━━━━━━━━━━━━━━━━━\n🔒 " + IntegerToString(n) + " élément(s) fermé(s)";
   if(st_posCount + st_pendCount > 0)
   {
      s += "\n⚠️ Il reste " + IntegerToString(st_posCount + st_pendCount) + " élément(s) : vérifie dans MT5.";
      // [ARCHI] début
      if(g_closeBlocked) s += "\n⏳ Fermeture bloquée (" + g_closeBlockWhy + ") : nouvelles tentatives automatiques pendant 30 min.";
      // [ARCHI] fin
   }
   s += "\n⏸ Robot en pause.\n💼 Balance : " + F2(AccountInfoDouble(ACCOUNT_BALANCE)) + " " + Cur();
   return s;
}
//+------------------------------------------------------------------+
