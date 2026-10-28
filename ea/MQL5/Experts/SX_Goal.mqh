//+------------------------------------------------------------------+
//| SX_Goal.mqh - ScalpX v4.5                                        |
//| Objectif (goal), protections de risque, statistiques de session, |
//| dépôts/retraits, rapport journalier.                             |
//+------------------------------------------------------------------+

//--- Persistance de l'objectif (variables globales du terminal)
void GoalSave()
{
   GlobalVariableSet(GVN("g_target"), g_goalTarget);
   GlobalVariableSet(GVN("g_init"),   g_goalInit);
   GlobalVariableSet(GVN("g_peak"),   g_goalPeak);
   GlobalVariableSet(GVN("g_dd"),     g_goalMaxDD);
   GlobalVariableSet(GVN("g_start"),  (double)g_goalStart);
   GlobalVariableSet(GVN("g_active"), g_goalActive ? 1 : 0);
   GlobalVariableSet(GVN("g_n25"),    g_n25 ? 1 : 0);
   GlobalVariableSet(GVN("g_n50"),    g_n50 ? 1 : 0);
   GlobalVariableSet(GVN("g_n75"),    g_n75 ? 1 : 0);
   GlobalVariableSet(GVN("g_done"),   g_goalDone ? 1 : 0);
}

void GoalClear()
{
   string k[] = { "g_target", "g_init", "g_peak", "g_dd", "g_start", "g_active", "g_n25", "g_n50", "g_n75", "g_done" };
   for(int i = 0; i < ArraySize(k); i++) GlobalVariableDel(GVN(k[i]));
}

void GoalLoad()
{
   if(!GlobalVariableCheck(GVN("g_active"))) return;
   g_goalTarget = GlobalVariableGet(GVN("g_target"));
   g_goalInit   = GlobalVariableGet(GVN("g_init"));
   g_goalPeak   = GlobalVariableGet(GVN("g_peak"));
   g_goalMaxDD  = GlobalVariableGet(GVN("g_dd"));
   g_goalStart  = (datetime)(long)GlobalVariableGet(GVN("g_start"));
   g_goalActive = GlobalVariableGet(GVN("g_active")) > 0;
   g_n25        = GlobalVariableGet(GVN("g_n25")) > 0;
   g_n50        = GlobalVariableGet(GVN("g_n50")) > 0;
   g_n75        = GlobalVariableGet(GVN("g_n75")) > 0;
   g_goalDone   = GlobalVariableGet(GVN("g_done")) > 0;
   if(g_goalDone) g_goalActive = false;
}

double GoalAmount() { return g_goalTarget - g_goalInit; }

double GoalProgressPct()
{
   double a = GoalAmount();
   if(!g_goalActive || a <= 0) return 0;
   return (AccountInfoDouble(ACCOUNT_BALANCE) - g_goalInit) / a * 100.0;
}

double CurrentDDPct()
{
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   if(g_peakEq <= 0) return 0;
   double d = (g_peakEq - eq) / g_peakEq * 100.0;
   return (d < 0) ? 0 : d;
}

//--- 3 balances cibles rondes au-dessus de la balance (pas = 1/10, 1/2 puis 1 de l'ordre de grandeur)
void RoundTargets(double bal, double &t[])
{
   t[0] = 0; t[1] = 0; t[2] = 0;
   if(bal <= 0) return;
   double mag = MathPow(10.0, MathFloor(MathLog10(bal)));
   double steps[3];
   steps[0] = mag / 10.0; steps[1] = mag / 2.0; steps[2] = mag;
   double floorV = bal * 1.01;                       // au moins +1 % pour avoir un vrai objectif
   for(int i = 0; i < 3; i++)
   {
      double st = MathMax(steps[i], 1.0);
      double v = MathFloor(floorV / st) * st + st;   // prochain multiple strictement supérieur
      t[i] = v;
      floorV = v;                                    // les suivants sont strictement croissants
   }
}

//--- Définition d'un objectif : "10" (montant), "+5%" (pourcentage), "=1250" (balance cible)
bool GoalSetFromArg(string arg, string &msg)
{
   StringTrimLeft(arg); StringTrimRight(arg);
   StringReplace(arg, ",", ".");
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   double target = 0;
   string desc = "";
   if(StringLen(arg) == 0) { msg = "Format : 10 (montant), +5% (pourcentage) ou =1250 (balance cible)"; return false; }

   ushort c0 = StringGetCharacter(arg, 0);
   if(c0 == '+' || StringFind(arg, "%") >= 0)
   {
      string num = arg;
      StringReplace(num, "+", ""); StringReplace(num, "%", "");
      double pct = StringToDouble(num);
      if(pct <= 0 || pct > 500) { msg = "Pourcentage invalide (0 à 500)"; return false; }
      target = (g_goalActive ? g_goalInit : bal) * (1.0 + pct / 100.0);
      desc = "+" + F2(pct) + " %";
   }
   else if(c0 == '=')
   {
      target = StringToDouble(StringSubstr(arg, 1));
      if(target <= bal) { msg = "La balance cible doit être supérieure à la balance actuelle (" + F2(bal) + ")"; return false; }
      desc = "balance cible " + F2(target) + " " + Cur();
   }
   else
   {
      double v = StringToDouble(arg);
      if(v <= 0) { msg = "Montant invalide"; return false; }
      if(v > PDbl(P_GOALMAX))
      {
         msg = "Le montant dépasse le plafond d'objectif (" + F2(PDbl(P_GOALMAX)) + " " + Cur() + ").\nAugmente le plafond dans Paramètres → Objectifs, ou utilise +N % ou =N.";
         return false;
      }
      target = (g_goalActive ? g_goalInit : bal) + v;
      desc = "+" + F2(v) + " " + Cur();
   }

   if(!g_goalActive)
   {
      g_goalInit  = bal;
      g_goalPeak  = AccountInfoDouble(ACCOUNT_EQUITY);
      g_goalMaxDD = 0;
      g_goalStart = TimeCurrent();
      g_n25 = g_n50 = g_n75 = false;
   }
   else if(target <= bal)
   {
      msg = "La nouvelle cible est déjà atteinte par la balance actuelle.";
      return false;
   }
   g_goalTarget = target;
   g_goalActive = true;
   g_goalDone   = false;
   GoalSave();
   msg = "🎯 Objectif défini : " + desc + "\n💼 Balance de départ : " + F2(g_goalInit) + " " + Cur() +
         "\n🏁 Cible : " + F2(g_goalTarget) + " " + Cur() + " (" + Sgn(GoalAmount()) + " " + Cur() + ")";
   return true;
}

void GoalCancel()
{
   g_goalActive = false;
   g_goalDone = false;
   g_enabled = false; g_pauseUntil = 0;
   GoalClear();
}

// Solde >= cible (tolérance d'un demi-centime : le solde est arrondi)
bool GoalBalanceReached(double bal)
{
   int dg = (int)AccountInfoInteger(ACCOUNT_CURRENCY_DIGITS);
   return bal + 0.5 * MathPow(10.0, -dg) >= g_goalTarget;
}

//+------------------------------------------------------------------+
//| Fin d'objectif : on FERME d'abord, on vérifie le solde, puis on  |
//| notifie. Si le solde final n'atteint pas la cible (clôture à un  |
//| autre prix), l'objectif n'est PAS terminé : la série continue.   |
//+------------------------------------------------------------------+
void FinishGoal(bool early)
{
   double eqBefore = AccountInfoDouble(ACCOUNT_EQUITY);
   double balBefore = AccountInfoDouble(ACCOUNT_BALANCE);
   bool wasEnabled = g_enabled;
   datetime wasPause = g_pauseUntil;
   g_enabled = false; g_pauseUntil = 0;
   g_goalActive = false;
   g_goalDone = true;
   g_cooldownUntilMs = GetTickCount64() + 10000;

   int closed = CloseAllFast(early ? "objectif atteint par anticipation" : "objectif atteint");
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);

   //--- Vérification finale : le solde doit réellement atteindre la cible (il peut mettre un instant à se mettre à jour)
   ulong tw = GetTickCount64();
   while(!GoalBalanceReached(bal) && GetTickCount64() - tw < 2500)
   {
      Sleep(100);
      bal = AccountInfoDouble(ACCOUNT_BALANCE);
   }
   if(!GoalBalanceReached(bal))
   {
      g_goalActive = true;
      g_goalDone = false;
      g_enabled = wasEnabled;
      g_pauseUntil = wasPause;
      g_cooldownUntilMs = GetTickCount64() + 2000;
      g_finishFails++;
      g_finishRetryMs = GetTickCount64() + 15000;
      RefreshState();
      Log("Objectif non confirmé après clôture : solde " + F2(bal) + " < cible " + F2(g_goalTarget) + " → la série continue");
      if(g_finishFails <= 3)
         TgQueue("⚠️ OBJECTIF PAS ENCORE ATTEINT\n━━━━━━━━━━━━━━━━━━" +
                 "\n🔒 Clôture faite, mais à un prix différent de celui attendu." +
                 "\n💰 Solde : " + F2(bal) + " | 🏁 Cible : " + F2(g_goalTarget) + " " + Cur() +
                 "\n📊 Il manque : " + F2(g_goalTarget - bal) + " " + Cur() +
                 (st_posCount + st_pendCount > 0 ? "\n⚠️ Il reste " + IntegerToString(st_posCount + st_pendCount) + " élément(s) non fermé(s)." : "") +
                 "\n\n▶️ La série n'est pas terminée : l'objectif reste actif.");
      return;
   }
   g_finishFails = 0;
   g_finishRetryMs = 0;

   double profit = bal - g_goalInit;
   double pct = (g_goalInit > 0) ? profit / g_goalInit * 100.0 : 0;
   int secs = (g_goalStart > 0) ? (int)(TimeCurrent() - g_goalStart) : 0;
   string dur = (secs >= 3600) ? IntegerToString(secs / 3600) + " h " + IntegerToString((secs % 3600) / 60) + " min"
                               : IntegerToString(secs / 60) + " min";
   g_sesGoals++;

   string m = early ? "🎯 OBJECTIF ATTEINT PAR ANTICIPATION" : "🎉 OBJECTIF ATTEINT";
   m += "\n━━━━━━━━━━━━━━━━━━";
   if(early) m += "\n⚡ Clôture avant le TP : profit réalisé + flottant = " + Sgn(eqBefore - g_goalInit) + " " + Cur();
   m += "\n💰 Solde départ : " + F2(g_goalInit) + " " + Cur() +
        "\n💰 Solde final : " + F2(bal) + " " + Cur() +
        "\n✅ Profit : " + Sgn(profit) + " " + Cur() + " (" + Sgn(pct) + " %)" +
        "\n⏱️ Durée : " + dur +
        "\n📉 Drawdown max : " + F2(g_goalMaxDD) + " %" +
        "\n🔒 Éléments fermés : " + IntegerToString(closed);
   if(st_posCount + st_pendCount > 0) m += "\n⚠️ Il reste " + IntegerToString(st_posCount + st_pendCount) + " élément(s) — vérifie manuellement";
   m += "\n\n▶️ Définis un nouvel objectif pour continuer.";
   g_goalMaxDD = 0;
   GoalClear();
   // [ARCHI] début
   EvTyped("OBJECTIF_ATTEINT", 1, m, "{\"solde\":" + NumS(bal) + ",\"profit\":" + NumS(profit) + ",\"anticipe\":" + BoolS(early) + "}");
   // [ARCHI] fin
}

//+------------------------------------------------------------------+
//| Suivi de l'objectif (à chaque tick, très léger)                  |
//+------------------------------------------------------------------+
void GoalTick()
{
   if(!g_goalActive || g_closing) return;
   double eq  = AccountInfoDouble(ACCOUNT_EQUITY);
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   if(eq > g_goalPeak) g_goalPeak = eq;
   if(g_goalPeak > 0)
   {
      double dd = (g_goalPeak - eq) / g_goalPeak * 100.0;
      if(dd > g_goalMaxDD) g_goalMaxDD = dd;
   }
   double amount = GoalAmount();
   if(amount <= 0) return;

   bool canFinish = (GetTickCount64() >= g_finishRetryMs);
   // Clôture anticipée : réalisé + flottant >= objectif (equity - balance initiale)
   if(canFinish && PBool(P_EARLY) && st_posCount > 0 && (eq - bal) > 0 && (eq - g_goalInit) >= amount)
   {
      FinishGoal(true);
      return;
   }
   // Objectif atteint sur la balance
   if(canFinish && GoalBalanceReached(bal))
   {
      FinishGoal(false);
      return;
   }
   // Progression 25 / 50 / 75 %
   double prog = (bal - g_goalInit) / amount * 100.0;
   int level = 0;
   if(prog >= 75.0 && !g_n75)      { g_n75 = true; g_n50 = true; g_n25 = true; level = 75; }
   else if(prog >= 50.0 && !g_n50) { g_n50 = true; g_n25 = true; level = 50; }
   else if(prog >= 25.0 && !g_n25) { g_n25 = true; level = 25; }
   if(level > 0)
   {
      GoalSave();
      if(PBool(P_N_PROGRESS))
         TgInfo("🎯 Objectif : " + IntegerToString(level) + " %\n━━━━━━━━━━━━━━━━━━" +
                 "\n💰 Solde : " + F2(bal) + " " + Cur() +
                 "\n🏁 Cible : " + F2(g_goalTarget) + " " + Cur() +
                 "\n📊 Reste : " + F2(g_goalTarget - bal) + " " + Cur() +
                 "\n📉 Drawdown : " + F2(CurrentDDPct()) + " % (max objectif " + F2(g_goalMaxDD) + " %)");
   }
}

//+------------------------------------------------------------------+
//| Protections de risque (à chaque tick)                            |
//+------------------------------------------------------------------+
void RiskTick()
{
   if(g_closing) return;
   double eq  = AccountInfoDouble(ACCOUNT_EQUITY);
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   if(eq > g_peakEq) { g_peakEq = eq; g_ddNotified = false; }

   //--- Drawdown (equity)
   double thr = PDbl(P_DDTHR);
   if(g_peakEq > 0 && thr > 0)
   {
      double dd = (g_peakEq - eq) / g_peakEq * 100.0;
      if(dd >= thr && !g_ddNotified)
      {
         g_ddNotified = true;
         string head = "📉 Drawdown : " + F2(dd) + " % (seuil " + F0(thr) + " %)\n" +
                       "📊 Equity max : " + F2(g_peakEq) + " | actuelle : " + F2(eq) + " " + Cur();
         if(PBool(P_DDSTOP) && g_enabled)
         {
            g_enabled = false; g_pauseUntil = 0;
            int c = CancelPendingFast();
            TgCrit("🛑 DRAWDOWN — ROBOT MIS EN PAUSE\n━━━━━━━━━━━━━━━━━━\n" + head +
                    "\n🧹 " + IntegerToString(c) + " ordre(s) en attente annulé(s)" +
                    "\n\n💡 Les positions ouvertes restent gérées (TP). Relance avec ▶️ Démarrer.");
         }
         else
            TgCrit("⚠️ DRAWDOWN DÉTECTÉ\n━━━━━━━━━━━━━━━━━━\n" + head + "\n\n💡 Le robot continue (arrêt auto désactivé).");
      }
      if(dd < MathMax(0.0, thr - 1.0)) g_ddNotified = false;     // hystérésis
   }

   //--- Stop loss d'urgence (EQUITY : inclut le flottant de la grille)
   if(PBool(P_EMSL) && g_goalActive && g_goalInit > 0)
   {
      double slLevel = g_goalInit * (1.0 - PDbl(P_EMSLPCT) / 100.0);
      if(eq <= slLevel)
      {
         g_enabled = false; g_pauseUntil = 0;
         g_goalActive = false;
         double lossBefore = g_goalInit - eq;
         int closed = CloseAllFast("stop loss d'urgence");
         double balAfter = AccountInfoDouble(ACCOUNT_BALANCE);
         GoalClear();
         TgCrit("🚨 STOP LOSS D'URGENCE DÉCLENCHÉ\n━━━━━━━━━━━━━━━━━━" +
                 "\n❌ Perte à l'instant : " + F2(lossBefore) + " " + Cur() + " (" + F1(lossBefore / g_goalInit * 100.0) + " %)" +
                 "\n💼 Balance initiale : " + F2(g_goalInit) + " | finale : " + F2(balAfter) + " " + Cur() +
                 "\n🔒 " + IntegerToString(closed) + " élément(s) fermé(s)" +
                 "\n\n⏸ Objectif terminé, robot en pause.");
         return;
      }
   }

   //--- Limite de perte de session
   if(PBool(P_SESSLIM) && g_sesStartBal > 0 && g_enabled)
   {
      double loss = (g_sesStartBal - eq) / g_sesStartBal * 100.0;
      if(loss >= PDbl(P_SESSPCT))
      {
         g_enabled = false; g_pauseUntil = 0;
         int c = CancelPendingFast();
         TgCrit("⛔ LIMITE DE PERTE DE SESSION ATTEINTE\n━━━━━━━━━━━━━━━━━━" +
                 "\n📉 Perte : " + F2(g_sesStartBal - eq) + " " + Cur() + " (" + F1(loss) + " %, limite " + F0(PDbl(P_SESSPCT)) + " %)" +
                 "\n🧹 " + IntegerToString(c) + " ordre(s) en attente annulé(s)" +
                 "\n\n⏸ Robot en pause. Les positions ouvertes restent gérées.");
      }
   }
}

//+------------------------------------------------------------------+
//| Dépôts / retraits : l'objectif est décalé, pas "gagné"           |
//+------------------------------------------------------------------+
void AdjustForCashFlow(double amt)
{
   g_peakEq += amt;
   g_dayPeakEq += amt;
   g_dayStartBal += amt;                 // un dépôt / retrait ne compte pas comme gain du jour
   g_sesStartBal += amt;
   if(g_goalActive)
   {
      g_goalInit += amt; g_goalTarget += amt; g_goalPeak += amt;
      GoalSave();
   }
   // [ARCHI] début : un seul événement, avec son (prio 1)
   EvTyped("CASHFLOW", 1, (amt > 0 ? "💰 DÉPÔT " : "💸 RETRAIT ") + Sgn(amt) + " " + Cur() +
           "\n💼 Balance : " + F2(AccountInfoDouble(ACCOUNT_BALANCE)) + " " + Cur() +
           (g_goalActive ? "\n🎯 Objectif ajusté (départ " + F2(g_goalInit) + ", cible " + F2(g_goalTarget) + ") : un dépôt n'est pas un gain" : ""),
           "{\"montant\":" + NumS(amt) + ",\"solde\":" + NumS(AccountInfoDouble(ACCOUNT_BALANCE)) + "}");
   // [ARCHI] fin
}

// Un deal est « à nous » si son magic est le nôtre, OU si c'est la sortie (manuelle : appli mobile, terminal,
// stop-out) d'une position ouverte par notre robot : ces sorties ont un autre magic mais comptent dans la série.
bool DealIsOurs(ulong deal)
{
   if(HistoryDealGetInteger(deal, DEAL_MAGIC) == MagicNumber) return true;
   ulong pid = (ulong)HistoryDealGetInteger(deal, DEAL_POSITION_ID);
   if(pid == 0) return false;
   if(!HistorySelectByPosition(pid)) return false;
   int n = HistoryDealsTotal();
   for(int i = 0; i < n; i++)
   {
      ulong tk = HistoryDealGetTicket(i);
      if(tk != 0 && HistoryDealGetInteger(tk, DEAL_ENTRY) == DEAL_ENTRY_IN)
         return HistoryDealGetInteger(tk, DEAL_MAGIC) == MagicNumber;
   }
   return false;
}

// Appelé par OnTradeTransaction pour chaque deal ajouté
void OnDealAdded(ulong deal)
{
   if(!HistoryDealSelect(deal)) return;
   long type = HistoryDealGetInteger(deal, DEAL_TYPE);
   if(type == DEAL_TYPE_BALANCE)
   {
      double amt = HistoryDealGetDouble(deal, DEAL_PROFIT);
      if(amt != 0) AdjustForCashFlow(amt);
      return;
   }
   if(HistoryDealGetString(deal, DEAL_SYMBOL) != _Symbol) return;
   long entry = HistoryDealGetInteger(deal, DEAL_ENTRY);
   if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_OUT_BY && entry != DEAL_ENTRY_INOUT) return;
   if(!DealIsOurs(deal)) return;

   double net = HistoryDealGetDouble(deal, DEAL_PROFIT) + HistoryDealGetDouble(deal, DEAL_SWAP) +
                HistoryDealGetDouble(deal, DEAL_COMMISSION) + HistoryDealGetDouble(deal, DEAL_FEE);
   g_sesProfit += net;
   JournalOnDeal(net);
   if(net > 0) g_sesWins++; else g_sesLosses++;

   // Alerte de profit cumulé (le cumul est conservé d'un deal à l'autre)
   double amt = PDbl(P_PROFNOTIF);
   if(amt > 0)
   {
      g_profitAccum += net;
      if(g_profitAccum >= amt)
      {
         TgInfo("💰 Profit cumulé +" + F2(g_profitAccum) + " " + Cur() + " | Balance : " + F2(AccountInfoDouble(ACCOUNT_BALANCE)));
         g_profitAccum = 0;
      }
      else if(g_profitAccum < -amt) g_profitAccum = 0;
   }
}

//+------------------------------------------------------------------+
//| Session                                                          |
//+------------------------------------------------------------------+
void SessionInit()
{
   g_sesStartBal = AccountInfoDouble(ACCOUNT_BALANCE);
   g_sesStart    = TimeCurrent();
   g_sesWins = g_sesLosses = g_sesGoals = 0;
   g_sesProfit = 0;
   g_peakEq = AccountInfoDouble(ACCOUNT_EQUITY);
}
