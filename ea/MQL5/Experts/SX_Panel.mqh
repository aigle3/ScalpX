//+------------------------------------------------------------------+
//| SX_Panel.mqh - ScalpX v5.0                                       |
//| Panneau dynamique : état, objectif, compte, série en cours,      |
//| marché, filtres, session. Le bandeau affiche en priorité la      |
//| raison pour laquelle le robot n'ouvre pas de position.           |
//| Boutons : Pause/Démarrer, Tout fermer (double confirmation),     |
//| réduire/agrandir.                                                |
//+------------------------------------------------------------------+
#define PN_X        10
#define PN_Y        24
#define PN_W        340

#define PC_BG       C'14,20,34'
#define PC_HEAD     C'22,32,54'
#define PC_BORDER   C'40,54,82'
#define PC_TEXT     C'226,232,240'
#define PC_DIM      C'125,140,165'
#define PC_GREEN    C'38,194,129'
#define PC_RED      C'239,83,80'
#define PC_ORANGE   C'255,167,38'
#define PC_BLUE     C'66,165,245'
#define PC_GREY     C'84,98,124'

bool g_panelCompact = false;
bool g_panelLastCompact = false;

string PNM(string id) { return SX_PREFIX + "P_" + id; }

void PSetI(string n, ENUM_OBJECT_PROPERTY_INTEGER prop, long v)
{
   if(ObjectGetInteger(0, n, prop) != v) ObjectSetInteger(0, n, prop, v);
}

void PRect(string id, int x, int y, int w, int h, color bg, color br)
{
   string n = PNM(id);
   if(ObjectFind(0, n) < 0)
   {
      if(!ObjectCreate(0, n, OBJ_RECTANGLE_LABEL, 0, 0, 0)) return;
      ObjectSetInteger(0, n, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, n, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, n, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, n, OBJPROP_BORDER_TYPE, BORDER_FLAT);
      ObjectSetInteger(0, n, OBJPROP_BACK, false);
   }
   PSetI(n, OBJPROP_XDISTANCE, x);
   PSetI(n, OBJPROP_YDISTANCE, y);
   PSetI(n, OBJPROP_XSIZE, w);
   PSetI(n, OBJPROP_YSIZE, h);
   PSetI(n, OBJPROP_BGCOLOR, bg);
   PSetI(n, OBJPROP_COLOR, br);
}

// al : 0 = aligné à gauche, 1 = aligné à droite, 2 = centré
void PText(string id, int x, int y, string txt, color col, int sz = 9, int al = 0, string font = "Segoe UI")
{
   string n = PNM(id);
   if(ObjectFind(0, n) < 0)
   {
      if(!ObjectCreate(0, n, OBJ_LABEL, 0, 0, 0)) return;
      ObjectSetInteger(0, n, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, n, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, n, OBJPROP_HIDDEN, true);
      ObjectSetString(0, n, OBJPROP_FONT, font);
   }
   if(txt == "") txt = " ";                       // [ARCHI] évite le texte par défaut "Label"
   long anchor = (al == 1) ? ANCHOR_RIGHT_UPPER : ((al == 2) ? ANCHOR_UPPER : ANCHOR_LEFT_UPPER);
   PSetI(n, OBJPROP_ANCHOR, anchor);
   PSetI(n, OBJPROP_XDISTANCE, x);
   PSetI(n, OBJPROP_YDISTANCE, y);
   PSetI(n, OBJPROP_COLOR, col);
   PSetI(n, OBJPROP_FONTSIZE, sz);
   if(ObjectGetString(0, n, OBJPROP_TEXT) != txt) ObjectSetString(0, n, OBJPROP_TEXT, txt);
}

void PBar(string id, int x, int y, int w, int h, double frac, color fg)
{
   PRect(id + "b", x, y, w, h, C'30,42,66', C'30,42,66');
   if(frac < 0) frac = 0;
   if(frac > 1) frac = 1;
   int ww = (int)MathRound((w - 2) * frac);
   PRect(id + "f", x + 1, y + 1, MathMax(1, ww), h - 2, (ww > 0 ? fg : C'30,42,66'), (ww > 0 ? fg : C'30,42,66'));
}

void PBtn(string id, int x, int y, int w, int h, string txt, color bg, color fg)
{
   string n = PNM(id);
   if(ObjectFind(0, n) < 0)
   {
      if(!ObjectCreate(0, n, OBJ_BUTTON, 0, 0, 0)) return;
      ObjectSetInteger(0, n, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, n, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, n, OBJPROP_HIDDEN, true);
      ObjectSetString(0, n, OBJPROP_FONT, "Segoe UI");
      ObjectSetInteger(0, n, OBJPROP_FONTSIZE, 9);
   }
   PSetI(n, OBJPROP_XDISTANCE, x);
   PSetI(n, OBJPROP_YDISTANCE, y);
   PSetI(n, OBJPROP_XSIZE, w);
   PSetI(n, OBJPROP_YSIZE, h);
   PSetI(n, OBJPROP_BGCOLOR, bg);
   PSetI(n, OBJPROP_BORDER_COLOR, bg);
   PSetI(n, OBJPROP_COLOR, fg);
   if(ObjectGetString(0, n, OBJPROP_TEXT) != txt) ObjectSetString(0, n, OBJPROP_TEXT, txt);
}

void PanelClear() { ObjectsDeleteAll(0, PNM("")); }

string Trunc(string s, int mx)
{
   if(StringLen(s) <= mx) return s;
   return StringSubstr(s, 0, mx - 1) + "…";
}

// [ARCHI] début : aides d'affichage du planning
string DayFr(int dow) { string d[7] = {"dim", "lun", "mar", "mer", "jeu", "ven", "sam"}; return d[dow % 7]; }

string Countdown(datetime t)
{
   long s = (long)(t - LocalNow());
   if(s < 0) s = 0;
   long d = s / 86400, h = (s % 86400) / 3600, m = (s % 3600) / 60;
   if(d > 0) return IntegerToString(d) + "j " + IntegerToString(h) + "h " + StringFormat("%02d", (int)m) + "min";
   if(h > 0) return IntegerToString(h) + "h " + StringFormat("%02d", (int)m) + "min";
   return IntegerToString(m) + " min";
}

string SlotLabel(datetime t)
{
   if(t == 0) return "—";
   MqlDateTime a, b;
   TimeToStruct(t, a);
   TimeToStruct(LocalNow(), b);
   string hm = StringFormat("%02d:%02d", a.hour, a.min);
   if(a.year == b.year && a.day_of_year == b.day_of_year) return "aujourd'hui " + hm;
   return DayFr(a.day_of_week) + " " + StringFormat("%02d/%02d", a.day, a.mon) + " " + hm;
}

// retour à la ligne par mots ; renvoie le nombre de lignes affichées
int PWrap(string id, int x, int y, string txt, color col, int sz, int maxChars, int maxLines)
{
   string words[];
   int n = StringSplit(txt, ' ', words);
   string line = "";
   int ln = 0;
   for(int i = 0; i < n; i++)
   {
      string cand = (line == "") ? words[i] : line + " " + words[i];
      if(StringLen(cand) > maxChars && line != "" && ln < maxLines - 1)
      {
         PText(id + IntegerToString(ln), x, y + ln * 15, line, col, sz);
         ln++;
         line = words[i];
      }
      else line = cand;
   }
   PText(id + IntegerToString(ln), x, y + ln * 15, Trunc(line, maxChars + 4), col, sz);
   ln++;
   for(int k = ln; k < maxLines; k++) PText(id + IntegerToString(k), x, y + k * 15, "", col, sz);
   return ln;
}

// quand le trading reprend / pourquoi il ne trade pas
string ResumeMsg(color &col)
{
   col = PC_TEXT;
   datetime ns = NextSlot();
   string slot = !PBool(P_SL_ON) ? "créneaux désactivés" :
                 (ns > 0 ? SlotLabel(ns) + " (dans " + Countdown(ns) + ")" : "aucun créneau configuré");
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   if(g_maint)        { col = PC_ORANGE; return "Maintenance : le trading reprend sur ordre du bot central."; }
   if(g_phase500)     { col = PC_ORANGE; return "Clôture 500 en cours : fermeture des positions puis vérification du solde."; }
   if(g_state == ST_ARRET)
   { col = PC_RED; return "Seuil " + F0(PDbl(P_SEUIL_ARRET)) + " atteint : reprise automatique après retrait (solde entre " + F0(PDbl(P_SEUIL_REPRISE)) + " et " + F0(PDbl(P_SEUIL_ARRET)) + ")."; }
   if(g_state == ST_PAUSE) { col = PC_BLUE; return "Pause : le trading reprend avec la commande REPRISE (bot ou bouton Démarrer)."; }
   if(!g_goalActive)
   {
      if(bal < PDbl(P_SEUIL_REPRISE)) { col = PC_RED; return "Solde sous " + F0(PDbl(P_SEUIL_REPRISE)) + " " + Cur() + " : aucune nouvelle série ne sera lancée."; }
      col = PC_ORANGE;
      return "Aucun objectif : le trading reprend au prochain créneau, " + slot + ".";
   }
   if(!g_configOK) { col = PC_ORANGE; return "Paramètres à valider (commande VALIDER) avant de reprendre."; }
   if(!g_enabled)  { col = PC_BLUE; return "Robot arrêté : touche Démarrer pour reprendre l'objectif en cours."; }
   if(st_posCount > 0) { col = PC_GREEN; return "Série en cours. Prochain créneau : " + slot + " (sauté si l'objectif n'est pas terminé)."; }
   if(g_blockReason != "") { col = PC_ORANGE; return "Nouvelle série bloquée (" + g_blockReason + ") : reprise automatique dès que la condition se libère."; }
   col = PC_GREEN;
   return "Prêt : ouverture d'une série au prochain signal. Prochain créneau : " + slot + ".";
}
// [ARCHI] fin

//+------------------------------------------------------------------+
//| Bandeau : la chose la plus importante à savoir maintenant        |
//+------------------------------------------------------------------+
string BannerMsg(color &col)
{
   col = PC_TEXT;
   // [ARCHI] début
   if(g_maint)        { col = PC_ORANGE; return "Maintenance — trading suspendu"; }
   if(g_phase500)     { col = PC_ORANGE; return "Clôture 500 en cours"; }
   if(g_state == ST_ARRET) { col = PC_RED; return "Arrêt 500 — en attente de retrait"; }
   if(g_state == ST_PAUSE) { col = PC_BLUE; return "Robot en pause (commande bot / panneau)"; }
   if(!g_goalActive)  { col = PC_ORANGE; return "En veille — aucun objectif actif"; }
   // [ARCHI] fin
   if(!g_configOK)    { col = PC_ORANGE; return "Paramètres à valider (commande VALIDER du bot)"; }
   if(!g_enabled)     { col = PC_BLUE;   return "Robot en pause — touche Démarrer pour reprendre"; }
   if(st_posCount > 0)
   {
      col = (st_posType == POSITION_TYPE_BUY) ? PC_GREEN : PC_RED;
      return string(st_posType == POSITION_TYPE_BUY ? "Série ACHAT" : "Série VENTE") + " en cours — objectif : TP commun";
   }
   if(g_blockReason != "") { col = PC_ORANGE; return "Nouvelle série bloquée : " + g_blockReason; }
   col = PC_GREEN;
   return "En attente de signal (" + string(mk_fast > mk_slow ? "achat" : "vente") + ")";
}

void PanelToggleRun()
{
   if(g_enabled)
   {
      g_enabled = false; g_pauseUntil = 0;
      g_stoppedManually = true;
      GlobalVariableSet(GVN("stopped"), 1);
      Log("Robot mis en pause depuis le panneau");
      return;
   }
   if(!g_goalActive) { Alert("ScalpX : aucun objectif en cours (prochain créneau " + NextSlotText() + ")."); return; }
   g_configOK = true;
   GlobalVariableSet(GVN("cfgok"), 1);
   g_enabled = true;
   g_stoppedManually = false;
   GlobalVariableDel(GVN("stopped"));
   g_cooldownUntilMs = 0;
   g_failCount = 0;
   g_nextTradeMs = 0;
   Log("Robot démarré depuis le panneau");
}

bool PanelOnClick(string name)
{
   if(StringFind(name, PNM("")) != 0) return false;
   ObjectSetInteger(0, name, OBJPROP_STATE, false);
   if(name == PNM("col"))      { g_panelCompact = !g_panelCompact; g_panelMs = 0; PanelUpdate(); return true; }
   if(name == PNM("b_run"))    { PanelToggleRun(); g_panelMs = 0; PanelUpdate(); return true; }
   if(name == PNM("b_close"))
   {
      if(!g_btnCloseArmed) { g_btnCloseArmed = true; g_btnCloseMs = GetTickCount64(); }
      else
      {
         g_btnCloseArmed = false;
         g_enabled = false; g_pauseUntil = 0;
         CloseAllFast("fermeture manuelle (panneau)");
      }
      g_panelMs = 0;
      PanelUpdate();
      return true;
   }
   return true;
}

//+------------------------------------------------------------------+
//| Mise à jour (max 4 fois par seconde, seulement si changement)    |
//+------------------------------------------------------------------+
void PanelUpdate()
{
   if(!PBool(P_CH_PANEL)) return;
   ulong now = GetTickCount64();
   if(now - g_panelMs < 250) return;
   g_panelMs = now;
   if(g_btnCloseArmed && now - g_btnCloseMs > 4000) g_btnCloseArmed = false;

   if(g_panelCompact != g_panelLastCompact)
   {
      ObjectsDeleteAll(0, PNM("a_")); ObjectsDeleteAll(0, PNM("s_"));
      ObjectsDeleteAll(0, PNM("m_")); ObjectsDeleteAll(0, PNM("e_"));
      g_panelLastCompact = g_panelCompact;
   }

   int x0 = PN_X, y0 = PN_Y, W = PN_W, pad = 12;
   int xl = x0 + pad, xr = x0 + W - pad, iw = W - 2 * pad;
   int y = y0;
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   double eq  = AccountInfoDouble(ACCOUNT_EQUITY);

   PRect("bg", x0, y0, W, 400, PC_BG, PC_BORDER);       // hauteur ajustée en fin de mise à jour

   //--- En-tête + badge d'état
   // [ARCHI] début
   color badgeCol;
   string badge;
   if(g_maint)                                            { badge = "MAINTENANCE";  badgeCol = PC_ORANGE; }
   else if(g_phase500)                                    { badge = "CLÔTURE 500";  badgeCol = PC_ORANGE; }
   else if(g_state == ST_ARRET)                           { badge = "ARRÊT 500";    badgeCol = PC_RED; }
   else if(g_state == ST_PAUSE || (!g_enabled && g_goalActive)) { badge = "PAUSE";    badgeCol = PC_BLUE; }
   else if(!g_goalActive)                                 { badge = "VEILLE";       badgeCol = PC_GREY; }
   else if(st_posCount == 0 && g_blockReason != "")       { badge = "EN ATTENTE";   badgeCol = PC_ORANGE; }
   else                                                   { badge = "ACTIF";        badgeCol = PC_GREEN; }
   // [ARCHI] fin
   PRect("hd", x0, y0, W, 30, PC_HEAD, PC_BORDER);
   PText("t1", xl, y0 + 7, "SCALPX " + SX_VERSION, PC_TEXT, 10, 0, "Segoe UI Semibold");
   PText("t2", xl + 88, y0 + 10, _Symbol + " · " + TfLabel(), PC_DIM, 8);
   PRect("bd", xr - 92, y0 + 6, 92, 18, badgeCol, badgeCol);
   PText("bdt", xr - 46, y0 + 8, badge, C'10,14,24', 8, 2, "Segoe UI Semibold");
   PBtn("col", xr - 92 - 26, y0 + 6, 22, 18, g_panelCompact ? "+" : "–", PC_BORDER, PC_TEXT);
   y += 30;

   //--- Bandeau : ce que fait le robot maintenant
   color bc;
   string bm = BannerMsg(bc);
   PRect("bn", x0, y, W, 24, C'17,26,44', PC_BORDER);
   PRect("bnd", xl, y + 8, 8, 8, bc, bc);
   PText("bnt", xl + 14, y + 5, Trunc(bm, 50), bc, 9);
   y += 32;

   //--- PLANNING : prochain créneau, compte à rebours, reprise du trading
   // [ARCHI] début
   datetime ns = NextSlot();
   bool slotsOn = (PBool(P_SL_ON) && g_slotsN > 0);
   PText("p_t", xl, y, "PLANNING", PC_DIM, 8);
   PText("p_tr", xr, y, "Heure EA " + TimeToString(LocalNow(), TIME_MINUTES | TIME_SECONDS), PC_DIM, 8, 1);
   y += 15;
   PText("p_1", xl, y, "Prochain objectif +" + F1(PDbl(P_SL_PCT)) + " %", PC_TEXT, 9);
   PText("p_1r", xr, y, slotsOn ? SlotLabel(ns) : "créneaux désactivés", slotsOn ? PC_ORANGE : PC_DIM, 9, 1, "Segoe UI Semibold");
   y += 16;
   PText("p_2", xl, y, "Compte à rebours", PC_TEXT, 9);
   PText("p_2r", xr, y, slotsOn ? Countdown(ns) : "—", PC_TEXT, 9, 1);
   y += 18;
   color rc;
   string rm = ResumeMsg(rc);
   int nl = PWrap("p_m", xl, y, rm, rc, 9, 52, 3);
   y += nl * 15 + 4;
   static int s_chips = 0;                              // nombre de pastilles dessinées au rafraîchissement précédent
   int maxChips = 12;                                   // au-delà : les 12 prochains créneaux (le panneau reste lisible)
   int wantChips = slotsOn ? MathMin(g_slotsN, maxChips) : 0;
   for(int i = wantChips; i < s_chips; i++)             // créneau retiré (ou désactivé) : on efface les pastilles en trop
   {
      ObjectDelete(0, PNM("p_c" + IntegerToString(i)));
      ObjectDelete(0, PNM("p_ct" + IntegerToString(i)));
   }
   s_chips = wantChips;
   if(slotsOn)
   {
      int perRow = MathMin(wantChips, 6);
      int cw = (iw - (perRow - 1) * 4) / perRow;
      MqlDateTime lt;
      TimeToStruct(LocalNow(), lt);
      bool weekday = (lt.day_of_week >= 1 && lt.day_of_week <= 5);
      int nowMin = lt.hour * 60 + lt.min, win = SlotWindow();
      bool many = (g_slotsN > maxChips);
      int start = 0;                                    // beaucoup de créneaux : on commence au prochain
      if(many && weekday)
      {
         start = g_slotsN;
         for(int k = 0; k < g_slotsN; k++) if(nowMin < g_slots[k] + win) { start = k; break; }
         if(start >= g_slotsN) start = 0;               // tous passés aujourd'hui : ceux de demain
      }
      bool nextMarked = false;
      for(int i = 0; i < wantChips; i++)
      {
         int si = (start + i) % g_slotsN;
         int cx = i % perRow, row = i / perRow;
         int px = xl + cx * (cw + 4), py = y + row * 22;
         bool passed = !many && weekday && (nowMin >= g_slots[si] + win);   // fenêtre terminée
         bool isNext = (!passed && !nextMarked && (weekday || i == 0));
         if(isNext) nextMarked = true;
         color fill = isNext ? PC_ORANGE : C'24,34,58';
         color tcol = isNext ? C'10,14,24' : (passed ? PC_GREY : PC_TEXT);
         PRect("p_c" + IntegerToString(i), px, py, cw, 18, fill, isNext ? PC_ORANGE : PC_BORDER);
         PText("p_ct" + IntegerToString(i), px + cw / 2, py + 3, SlotText(g_slots[si]), tcol, 8, 2, isNext ? "Segoe UI Semibold" : "Segoe UI");
      }
      y += ((wantChips - 1) / perRow + 1) * 22;
      PText("p_n", xl, y, many ? IntegerToString(g_slotsN) + " créneaux · les " + IntegerToString(maxChips) + " prochains · fenêtre " + IntegerToString(win) + " min"
                               : "Lun–ven · fenêtre " + IntegerToString(win) + " min · sauté si objectif en cours", PC_DIM, 8);
      y += 14;
   }
   y += 8;
   // [ARCHI] fin

   //--- Objectif
   PText("g_t", xl, y, "OBJECTIF EN COURS", PC_DIM, 8);
   if(g_goalActive)
   {
      double prog = GoalProgressPct();
      string pt = (prog > 999) ? ">999" : F0(prog);
      PText("g_p", xr, y, pt + " %   " + Sgn(bal - g_goalInit) + " / " + F2(GoalAmount()) + " " + Cur(), PC_TEXT, 9, 1);
      y += 15;
      PBar("g_b", xl, y, iw, 8, prog / 100.0, (prog >= 0 ? PC_GREEN : PC_RED));
      y += 14;
      PText("g_d", xl, y, "Départ " + F2(g_goalInit) + " · cible " + F2(g_goalTarget) + " · reste " + F2(MathMax(0.0, g_goalTarget - bal)), PC_DIM, 8);
      y += 14;
      PText("g_e", xl, y, bal >= g_goalTarget ? "Cible atteinte — vérification du solde / clôture" : "DD max atteint " + F1(g_goalMaxDD) + " %", PC_DIM, 8);
   }
   else
   {
      PText("g_p", xr, y, "—", PC_DIM, 9, 1);
      y += 15;
      PBar("g_b", xl, y, iw, 8, 0, PC_GREEN);
      y += 14;
      PText("g_d", xl, y, "Aucun objectif actif", PC_DIM, 8);
      y += 14;
      PText("g_e", xl, y, slotsOn ? "Le suivant démarre à " + SlotLabel(ns) : "", PC_DIM, 8);
   }
   y += 24;

   if(!g_panelCompact)
   {
      //--- Compte
      double dd = CurrentDDPct();
      color ddc = (dd >= PDbl(P_DDTHR)) ? PC_RED : ((dd >= PDbl(P_DDTHR) * 0.7) ? PC_ORANGE : PC_TEXT);
      double dayPl = (g_dayStartBal > 0) ? (bal - g_dayStartBal) : 0.0;   // réalisé du jour : le flottant est sur la ligne au-dessus
      double ml = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);
      PText("a_t", xl, y, "COMPTE", PC_DIM, 8);
      PText("a_tr", xr, y, g_aid, PC_DIM, 8, 1);
      y += 14;
      PText("a_1", xl, y, "Solde " + F2(bal) + " " + Cur(), PC_TEXT, 9);
      PText("a_1r", xr, y, "Equity " + F2(eq), PC_TEXT, 9, 1);
      y += 16;
      PText("a_2", xl, y, "Flottant " + Sgn(st_floating), st_floating >= 0 ? PC_GREEN : PC_RED, 9);
      PText("a_2r", xr, y, "Drawdown " + F1(dd) + " % / " + F0(PDbl(P_DDTHR)) + " %", ddc, 9, 1);
      y += 16;
      PText("a_3", xl, y, "Résultat jour " + Sgn(dayPl), dayPl >= 0 ? PC_GREEN : PC_RED, 9);
      PText("a_3r", xr, y, "Marge " + (ml > 0 ? F0(ml) + " %" : "—"), PC_TEXT, 9, 1);
      y += 20;
      // [ARCHI] début : progression vers le seuil d'arrêt (500) et seuil minimal (125)
      double lo = PDbl(P_SEUIL_REPRISE), hi = PDbl(P_SEUIL_ARRET);
      double frac = (hi > lo) ? (bal - lo) / (hi - lo) : 0.0;
      color pc = (bal < lo) ? PC_RED : (bal >= hi ? PC_ORANGE : PC_BLUE);
      PText("a_p", xl, y, "Vers " + F0(hi) + " " + Cur() + " · cycle " + IntegerToString(g_cycle), PC_TEXT, 9);
      PText("a_pr", xr, y, bal >= hi ? "seuil atteint" : "reste " + F2(hi - bal), PC_DIM, 9, 1);
      y += 16;
      PBar("a_pb", xl, y, iw, 8, frac, pc);
      y += 12;
      PText("a_pl", xl, y, F0(lo) + " (mini)", PC_DIM, 8);
      PText("a_pm", xr, y, bal < lo ? "solde < " + F0(lo) + " : pas de nouvelle série" : F0(MathMax(0.0, frac * 100.0)) + " %", bal < lo ? PC_RED : PC_DIM, 8, 1);
      y += 22;
      // [ARCHI] fin

      //--- Série en cours
      PText("s_t", xl, y, "SÉRIE EN COURS", PC_DIM, 8);
      y += 14;
      if(st_posCount > 0)
      {
         bool buy = (st_posType == POSITION_TYPE_BUY);
         double px = buy ? SymbolInfoDouble(_Symbol, SYMBOL_BID) : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         double toTp = (g_tpPrice > 0) ? MathAbs(g_tpPrice - px) / _Point : 0;
         double nxt  = buy ? st_highPend : st_lowPend;
         PText("s_1", xl, y, string(buy ? "ACHAT" : "VENTE") + " × " + IntegerToString(st_posCount) + " · " + F2(st_posLots) + " lot",
               buy ? PC_GREEN : PC_RED, 9, 0, "Segoe UI Semibold");
         PText("s_1r", xr, y, "P/L " + Sgn(st_floating), st_floating >= 0 ? PC_GREEN : PC_RED, 9, 1);
         y += 16;
         PText("s_2", xl, y, "Prix moyen " + PX(st_avg), PC_TEXT, 9);
         PText("s_2r", xr, y, g_tpPrice > 0 ? "TP " + PX(g_tpPrice) + " (" + F0(toTp) + " pts)" : "TP en calcul", PC_GREEN, 9, 1);
         y += 16;
         PText("s_3", xl, y, "Grille " + IntegerToString(st_pendCount) + "/" + IntegerToString(PInt(P_MAXPEND)) + " ordres", PC_TEXT, 9);
         PText("s_3r", xr, y, nxt > 0 ? "Prochain " + PX(nxt) : "", PC_DIM, 9, 1);
      }
      else
      {
         PText("s_1", xl, y, "Aucune position", PC_DIM, 9, 0, "Segoe UI");
         PText("s_1r", xr, y, "", PC_DIM, 9, 1);
         y += 16;
         PText("s_2", xl, y, "Signal : " + string(mk_fast > mk_slow ? "ACHAT" : (mk_fast < mk_slow ? "VENTE" : "—")) +
               " (EMA" + IntegerToString(PInt(P_FAST)) + (mk_fast > mk_slow ? " > " : " < ") + "EMA" + IntegerToString(PInt(P_SLOW)) + ")",
               mk_fast > mk_slow ? PC_GREEN : PC_RED, 9);
         PText("s_2r", xr, y, "", PC_DIM, 9, 1);
         y += 16;
         PText("s_3", xl, y, st_pendCount > 0 ? IntegerToString(st_pendCount) + " ordre(s) en attente" : "", PC_DIM, 9);
         PText("s_3r", xr, y, "", PC_DIM, 9, 1);
      }
      y += 22;

      //--- Marché
      PText("m_t", xl, y, "MARCHÉ · " + TfLabel() + (BarShift() == 0 ? " · bougie en cours" : " · bougie clôturée"), PC_DIM, 8);
      y += 14;
      PText("m_1", xl, y, "EMA" + IntegerToString(PInt(P_FAST)) + " " + PX(mk_fast), C_EMA_F, 9);
      PText("m_1r", xr, y, "EMA" + IntegerToString(PInt(P_SLOW)) + " " + PX(mk_slow), C_EMA_S, 9, 1);
      y += 16;
      color spc = (mk_spread > PInt(P_MAXSPREAD)) ? PC_RED : PC_TEXT;
      PText("m_2", xl, y, "ATR " + F0(mk_atrPts) + " pts" + (PBool(P_F_ATR) ? "  [" + F0(PDbl(P_ATRMIN)) + "–" + F0(PDbl(P_ATRMAX)) + "]" : ""), PC_TEXT, 9);
      PText("m_2r", xr, y, "Spread " + F0(mk_spread) + "/" + IntegerToString(PInt(P_MAXSPREAD)), spc, 9, 1);
      y += 16;
      string loN = TfNameP(P_LOTF), hiN = TfNameP(P_HITF);
      color  cLo = !PBool(P_F_LO) ? PC_DIM : (f_ok[3] ? PC_GREEN : PC_RED);
      color  cHi = !PBool(P_F_HI) ? PC_DIM : (f_ok[4] ? PC_GREEN : PC_RED);
      color  cCa = !PBool(P_F_CAN) ? PC_DIM : (f_ok[5] ? PC_GREEN : PC_RED);
      string aLo = !PBool(P_F_LO) ? "off" : (mk_loF > mk_loS ? "▲" : (mk_loF < mk_loS ? "▼" : "–"));
      string aHi = !PBool(P_F_HI) ? "off" : (mk_hiF > mk_hiS ? "▲" : (mk_hiF < mk_hiS ? "▼" : "–"));
      string aCa = !PBool(P_F_CAN) ? "off" : (mk_candleDir > 0 ? "▲" : (mk_candleDir < 0 ? "▼" : "–"));
      PText("m_3", xl, y, "EMA " + loN + " " + aLo, cLo, 9);
      PText("m_3c", (xl + xr) / 2, y, "EMA " + hiN + " " + aHi, cHi, 9, 2);
      PText("m_3r", xr, y, "Bougie " + hiN + " " + aCa, cCa, 9, 1);
      y += 18;
      string pn[FB_N];
      bool   on[FB_N], ok[FB_N];
      pn[0] = "ATR";   on[0] = PBool(P_F_ATR);  ok[0] = f_ok[0];
      pn[1] = "EMA";   on[1] = PBool(P_F_EMA);  ok[1] = f_ok[1];
      pn[2] = "VOL";   on[2] = PBool(P_F_VOL);  ok[2] = f_ok[2];
      pn[3] = "HEURE"; on[3] = PBool(P_F_TIME); ok[3] = !f_timeBlocked;
      pn[4] = loN;           on[4] = PBool(P_F_LO);  ok[4] = f_ok[3];
      pn[5] = hiN;           on[5] = PBool(P_F_HI);  ok[5] = f_ok[4];
      pn[6] = "BG " + hiN;   on[6] = PBool(P_F_CAN); ok[6] = f_ok[5];
      pn[7] = "NEWS";        on[7] = PBool(P_NEWS_BLOCK); ok[7] = !g_newsBlock;
      pn[8] = "SPREAD";      on[8] = true;                ok[8] = FbOK(8);     // [ARCHI] 6.3.0
      int slot = iw / 4;
      for(int i = 0; i < FB_N; i++)
      {
         int cx = i % 4, row = i / 4;
         color c = !on[i] ? PC_GREY : (ok[i] ? PC_GREEN : PC_RED);
         PRect("m_d" + IntegerToString(i), xl + cx * slot, y + row * 16 + 4, 8, 8, c, c);
         PText("m_f" + IntegerToString(i), xl + cx * slot + 13, y + row * 16, pn[i], !on[i] ? PC_DIM : PC_TEXT, 8);
      }
      y += ((FB_N - 1) / 4 + 1) * 16 + 8;                 // une ligne de pastilles de plus avec le spread

      //--- Session
      int tot = g_sesWins + g_sesLosses;
      PText("e_t", xl, y, "SESSION", PC_DIM, 8);
      y += 14;
      PText("e_1", xl, y, "Trades " + IntegerToString(tot) + " (" + IntegerToString(g_sesWins) + " gagnants / " + IntegerToString(g_sesLosses) + " perdants)" +
            (tot > 0 ? " · " + F0((double)g_sesWins / tot * 100.0) + " %" : ""), PC_TEXT, 9);
      y += 16;
      PText("e_2", xl, y, "Résultat session " + Sgn(g_sesProfit) + " " + Cur(), g_sesProfit >= 0 ? PC_GREEN : PC_RED, 9);
      y += 24;
   }

   //--- Boutons
   int half = (iw - 8) / 2;
   PBtn("b_run", xl, y, half, 24, g_enabled ? "Pause" : "Démarrer", g_enabled ? C'80,92,120' : C'30,140,100', C'240,244,250');
   PBtn("b_close", xl + half + 8, y, half, 24, g_btnCloseArmed ? "Confirmer ?" : "Tout fermer", g_btnCloseArmed ? PC_RED : C'120,50,56', C'240,244,250');
   y += 32;

   //--- Pied : architecture
   // [ARCHI] début
   bool conn  = (TerminalInfoInteger(TERMINAL_CONNECTED) != 0);
   bool algoT = (TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) != 0);
   color tgc = (g_state == ST_ACTIF && !g_maint) ? PC_GREEN : PC_ORANGE;
   PRect("f_d", xl, y + 4, 8, 8, tgc, tgc);
   PText("f_1", xl + 13, y, "Bot central · " + StateName(g_state) + (g_maint ? " · maintenance" : "") + " · cycle " + IntegerToString(g_cycle), PC_DIM, 8);
   y += 15;
   PText("f_3", xl + 13, y, string(conn ? "Connecté" : "HORS LIGNE") + " · Algo terminal " + string(algoT ? "ON" : "OFF") + " · v" + SX_VERSION,
         (conn && algoT) ? PC_DIM : PC_RED, 8);
   // [ARCHI] fin
   if(ChartPeriod(0) != g_sigTF)
      PText("f_2", xr, y - 15, "Graphique " + EnumToString(ChartPeriod(0)) + " ≠ robot " + TfLabel(), PC_ORANGE, 8, 1);
   else
      PText("f_2", xr, y - 15, "", PC_DIM, 8, 1);
   y += 20;

   PRect("bg", x0, y0, W, y - y0, PC_BG, PC_BORDER);
   ChartRedraw(0);
}
//+------------------------------------------------------------------+
