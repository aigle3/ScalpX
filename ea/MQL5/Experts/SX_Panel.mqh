//+------------------------------------------------------------------+
//| SX_Panel.mqh - ScalpX                                            |
//| Panneau dynamique (6.4.0 : dessiné sur UN canevas, sans          |
//| clignotement) : état, planning, objectif, compte, série, marché, |
//| filtres, session. Le bandeau affiche en priorité la raison pour  |
//| laquelle le robot n'ouvre pas de position.                       |
//| Boutons : Pause/Démarrer, Tout fermer (double confirmation),     |
//| réduire/agrandir. Aucune logique de trading ici.                 |
//+------------------------------------------------------------------+

#define PN_X        10
#define PN_Y        24
#define PN_W        360

// palette (couleurs « color » pour les textes du reste du code, ARGB pour le canevas)
#define PC_BG       C'13,18,30'
#define PC_HEAD     C'20,28,46'
#define PC_CARD     C'20,27,43'
#define PC_BORDER   C'38,50,76'
#define PC_TEXT     C'230,236,245'
#define PC_DIM      C'128,142,168'
#define PC_GREEN    C'38,201,134'
#define PC_RED      C'240,86,86'
#define PC_ORANGE   C'255,170,51'
#define PC_BLUE     C'77,163,255'
#define PC_GREY     C'90,104,130'
#define PC_TRACK    C'33,44,68'

bool    g_panelCompact = false;
bool    g_panelLastCompact = false;
// canevas maison (aucune bibliothèque externe) : tableau de pixels ARGB → ressource → OBJ_BITMAP_LABEL
uint    g_px[];
int     g_cvW = 0, g_cvH = 0;
#define PN_RES  "::SX_PANEL"

uint Blend(uint dst, uint src)
{
   uint sa = src >> 24;
   if(sa >= 255) return src;
   if(sa == 0) return dst;
   uint da = dst >> 24;
   uint oa = sa + da * (255 - sa) / 255;
   if(oa == 0) return 0;
   uint r = (((src >> 16) & 0xFF) * sa + ((dst >> 16) & 0xFF) * da * (255 - sa) / 255) / oa;
   uint g = (((src >> 8) & 0xFF) * sa + ((dst >> 8) & 0xFF) * da * (255 - sa) / 255) / oa;
   uint b = ((src & 0xFF) * sa + (dst & 0xFF) * da * (255 - sa) / 255) / oa;
   return (oa << 24) | (r << 16) | (g << 8) | b;
}

void PxFill(int x1, int y1, int x2, int y2, uint c)
{
   if(x1 < 0) x1 = 0; if(y1 < 0) y1 = 0;
   if(x2 >= g_cvW) x2 = g_cvW - 1; if(y2 >= g_cvH) y2 = g_cvH - 1;
   bool opaque = (c >> 24) >= 255;
   for(int y = y1; y <= y2; y++)
   {
      int row = y * g_cvW;
      for(int x = x1; x <= x2; x++) g_px[row + x] = opaque ? c : Blend(g_px[row + x], c);
   }
}

// disque lissé (bord anticrénelé)
void PxCircle(int cx, int cy, int r, uint c)
{
   uint ca = c >> 24;
   for(int y = cy - r - 1; y <= cy + r + 1; y++)
   {
      if(y < 0 || y >= g_cvH) continue;
      for(int x = cx - r - 1; x <= cx + r + 1; x++)
      {
         if(x < 0 || x >= g_cvW) continue;
         double d = MathSqrt((double)((x - cx) * (x - cx) + (y - cy) * (y - cy)));
         double cov = (double)r + 0.5 - d;
         if(cov <= 0) continue;
         uint a = (cov >= 1) ? ca : (uint)(ca * cov);
         g_px[y * g_cvW + x] = Blend(g_px[y * g_cvW + x], (a << 24) | (c & 0xFFFFFF));
      }
   }
}
bool    g_cvOk   = false;
bool    g_meas   = false;          // passe de mesure (aucun dessin) : donne la hauteur du panneau
string  g_cvSig  = "";             // contenu dessiné : le bitmap n'est renvoyé au graphique que s'il a changé
string  g_cvCur  = "";
int     g_hit[3][4];               // zones cliquables : 0 réduire, 1 pause/démarrer, 2 tout fermer (x1,y1,x2,y2)

string PNM(string id) { return SX_PREFIX + "P_" + id; }

uint A(color c, uchar a = 255) { return ColorToARGB(c, a); }

//--- primitives (toutes ignorées pendant la passe de mesure)
void CvRect(int x1, int y1, int x2, int y2, uint c)
{
   g_cvCur += "R" + IntegerToString(x1) + "," + IntegerToString(y1) + "," + IntegerToString(x2) + "," + IntegerToString(y2) + ":" + IntegerToString(c) + ";";
   if(g_meas) return;
   PxFill(x1, y1, x2, y2, c);
}

void CvRound(int x, int y, int w, int h, int r, uint c)
{
   if(w <= 0 || h <= 0) return;
   r = MathMin(r, MathMin(w, h) / 2);
   g_cvCur += "O" + IntegerToString(x) + "," + IntegerToString(y) + "," + IntegerToString(w) + "," + IntegerToString(h) + ":" + IntegerToString(c) + ";";
   if(g_meas) return;
   if(r <= 0) { PxFill(x, y, x + w - 1, y + h - 1, c); return; }
   PxFill(x + r, y, x + w - 1 - r, y + h - 1, c);
   PxFill(x, y + r, x + r - 1, y + h - 1 - r, c);
   PxFill(x + w - r, y + r, x + w - 1, y + h - 1 - r, c);
   // coins : quarts de disque lissés, sans recouvrir les bandes déjà peintes (pas de double transparence)
   for(int q = 0; q < 4; q++)
   {
      int ccx = (q % 2 == 0) ? x + r : x + w - 1 - r;
      int ccy = (q < 2) ? y + r : y + h - 1 - r;
      int xa = (q % 2 == 0) ? x : x + w - r, xb = (q % 2 == 0) ? x + r - 1 : x + w - 1;
      int ya = (q < 2) ? y : y + h - r, yb = (q < 2) ? y + r - 1 : y + h - 1;
      uint ca = c >> 24;
      for(int yy = MathMax(ya, 0); yy <= MathMin(yb, g_cvH - 1); yy++)
         for(int xx = MathMax(xa, 0); xx <= MathMin(xb, g_cvW - 1); xx++)
         {
            double d = MathSqrt((double)((xx - ccx) * (xx - ccx) + (yy - ccy) * (yy - ccy)));
            double cov = (double)r + 0.5 - d;
            if(cov <= 0) continue;
            uint a = (cov >= 1) ? ca : (uint)(ca * cov);
            g_px[yy * g_cvW + xx] = Blend(g_px[yy * g_cvW + xx], (a << 24) | (c & 0xFFFFFF));
         }
   }
}

// carte : bord d'un pixel + fond
void CvCard(int x, int y, int w, int h, color fill, color border, int r = 8)
{
   CvRound(x, y, w, h, r, A(border));
   CvRound(x + 1, y + 1, w - 2, h - 2, r - 1, A(fill));
}

// al : 0 gauche, 1 droite, 2 centré ; sz en points
void CvText(int x, int y, string txt, color col, int sz = 9, int al = 0, bool bold = false)
{
   if(txt == "") return;
   g_cvCur += "T" + IntegerToString(x) + "," + IntegerToString(y) + "," + txt + ":" + IntegerToString(col) + IntegerToString(sz) + ";";
   if(g_meas) return;
   TextSetFont("Segoe UI", -sz * 10, bold ? FW_SEMIBOLD : FW_NORMAL);
   uint flags = TA_TOP | (al == 1 ? TA_RIGHT : (al == 2 ? TA_CENTER : TA_LEFT));
   TextOut(txt, x, y, flags, g_px, g_cvW, g_cvH, A(col), COLOR_FORMAT_ARGB_NORMALIZE);
}

int CvTextW(string txt, int sz = 9, bool bold = false);

// texte raccourci (…) pour tenir dans maxW pixels
string Fit(string txt, int sz, int maxW, bool bold = false)
{
   if(CvTextW(txt, sz, bold) <= maxW) return txt;
   int n = StringLen(txt);
   while(n > 1 && CvTextW(StringSubstr(txt, 0, n) + "…", sz, bold) > maxW) n--;
   return StringSubstr(txt, 0, n) + "…";
}

int CvTextW(string txt, int sz = 9, bool bold = false)
{
   TextSetFont("Segoe UI", -sz * 10, bold ? FW_SEMIBOLD : FW_NORMAL);
   uint tw = 0, th = 0;
   TextGetSize(txt, tw, th);
   return (int)tw;
}

string Trunc(string s, int mx)
{
   if(StringLen(s) <= mx) return s;
   return StringSubstr(s, 0, mx - 1) + "…";
}

// texte coupé à la largeur maxW (pixels) ; renvoie la hauteur utilisée
int CvWrap(int x, int y, string txt, color col, int sz, int maxW, int maxLines)
{
   string words[];
   int n = StringSplit(txt, ' ', words);
   string line = "";
   int ln = 0, lh = sz + 7;
   for(int i = 0; i < n; i++)
   {
      string cand = (line == "") ? words[i] : line + " " + words[i];
      if(line != "" && CvTextW(cand, sz) > maxW && ln < maxLines - 1)
      {
         CvText(x, y + ln * lh, line, col, sz);
         ln++;
         line = words[i];
      }
      else line = cand;
   }
   if(line != "") { CvText(x, y + ln * lh, line, col, sz); ln++; }
   return ln * lh;
}

void CvBar(int x, int y, int w, int h, double frac, color fg)
{
   if(frac != frac) frac = 0;
   frac = MathMax(0.0, MathMin(1.0, frac));
   CvRound(x, y, w, h, h / 2, A(PC_TRACK));
   int ww = (int)MathRound(w * frac);
   if(ww >= h) CvRound(x, y, ww, h, h / 2, A(fg));
   else if(ww > 0) CvRound(x, y, h, h, h / 2, A(fg));
}

// pastille d'état : fond teinté + texte coloré
void CvPill(int x, int y, int w, int h, string txt, color c, bool solid = false)
{
   CvRound(x, y, w, h, h / 2, solid ? A(c) : A(c, 46));
   CvText(x + w / 2, y + (h - 13) / 2, txt, solid ? C'10,14,24' : c, 8, 2, true);
}

void CvDot(int x, int y, int r, color c) { if(!g_meas) PxCircle(x, y, r, A(c)); g_cvCur += "D" + IntegerToString(x) + IntegerToString(c) + ";"; }

// ligne « libellé ............ valeur »
void CvRow(int xl, int xr, int y, string label, string value, color vcol, bool bold = false)
{
   CvText(xl, y, label, PC_DIM, 9);
   CvText(xr, y, value, vcol, 9, 1, bold);
}

void CvSection(int xl, int y, string title)
{
   CvText(xl, y, title, PC_DIM, 8, 0, true);
}

void CvButton(int k, int x, int y, int w, int h, string txt, color bg, color fg)
{
   CvRound(x, y, w, h, 7, A(bg));
   CvText(x + w / 2, y + (h - 15) / 2, txt, fg, 9, 2, true);
   g_hit[k][0] = x; g_hit[k][1] = y; g_hit[k][2] = x + w; g_hit[k][3] = y + h;
}

void PanelClear()
{
   if(g_cvOk) { ObjectDelete(0, PNM("cv")); ResourceFree(PN_RES); g_cvOk = false; }
   g_cvSig = "";
   ObjectsDeleteAll(0, PNM(""));                         // anciens objets du panneau (versions < 6.4.0)
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
      if(PBool(P_SL_CONT)) return ContArmed() ? "Mode continu : filtres OK, objectif lancé à la clôture de la bougie M5 s'ils le sont encore."   // [ARCHI] 7.8.6
                                              : "Mode continu : objectif lancé dès que tous les filtres sont OK (puis clôture de la bougie M5).";
      return "Aucun objectif : le trading reprend au prochain créneau, " + slot + ".";
   }
   if(!g_configOK) { col = PC_ORANGE; return "Paramètres à valider (commande VALIDER) avant de reprendre."; }
   if(!g_enabled)  { col = PC_BLUE; return "Robot arrêté : touche Démarrer pour reprendre l'objectif en cours."; }
   if(st_posCount > 0) { col = PC_GREEN; return PBool(P_SL_CONT) ? "Série en cours. Mode continu : objectif suivant après celui-ci."   // [ARCHI] 7.8.6
                                                                 : "Série en cours. Prochain créneau : " + slot + " (sauté si l'objectif n'est pas terminé)."; }
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

//+------------------------------------------------------------------+
//| Clics : le canevas est un seul objet, on regarde où l'on clique  |
//+------------------------------------------------------------------+
bool PanelHit(int k, int x, int y)
{
   int px = x - PN_X, py = y - PN_Y;
   return px >= g_hit[k][0] && px <= g_hit[k][2] && py >= g_hit[k][1] && py <= g_hit[k][3];
}

bool PanelOnClickXY(int x, int y)
{
   if(!g_cvOk) return false;
   if(PanelHit(0, x, y)) { g_panelCompact = !g_panelCompact; g_panelMs = 0; PanelUpdate(); return true; }
   if(PanelHit(1, x, y)) { PanelToggleRun(); g_panelMs = 0; PanelUpdate(); return true; }
   if(PanelHit(2, x, y))
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
   return false;
}

// compatibilité : clic sur un objet nommé (anciens boutons) — plus utilisé par le panneau sur canevas
bool PanelOnClick(string name) { return StringFind(name, PNM("")) == 0; }

//+------------------------------------------------------------------+
//| Sections (chacune : une carte, de x à x+w, à partir de y)        |
//| Renvoient le y sous la carte. La hauteur de la carte vient de la |
//| passe de mesure (tableau g_cardH, une case par section).         |
//+------------------------------------------------------------------+
int g_cardH[8] = { 120, 90, 150, 80, 150, 300, 40, 150 };   // [ARCHI] 7.10.0 : + statistiques

int CardBegin(int k, int x, int y, int w) { CvCard(x, y, w, g_cardH[k], PC_CARD, PC_BORDER); return y + 10; }
int CardEnd(int k, int y0, int y)        { y += 4; g_cardH[k] = y - y0; return y; }

int DrawPlanning(int x, int w, int y0)
{
   // [ARCHI] début
   int cx = x + 12, cr = x + w - 12, cw = w - 24;
   datetime ns = NextSlot();
   bool cont = PBool(P_SL_CONT);                      // [ARCHI] 7.8.6 : mode continu, pas de créneaux affichés
   bool slotsOn = (!cont && PBool(P_SL_ON) && g_slotsN > 0);
   int y = CardBegin(0, x, y0, w);
   CvSection(cx, y, "PLANNING");
   CvText(cr, y, "Heure EA " + TimeToString(LocalNow(), TIME_MINUTES | TIME_SECONDS), PC_DIM, 8, 1);
   y += 18;
   CvText(cx, y, "Prochain objectif +" + F1(PDbl(P_SL_PCT)) + " %", PC_TEXT, 10, 0, true);
   if(cont) CvText(cr, y, "mode continu", PC_GREEN, 10, 1, true);   // [ARCHI] 7.8.6
   else     CvText(cr, y, slotsOn ? SlotLabel(ns) : "créneaux désactivés", slotsOn ? PC_ORANGE : PC_DIM, 10, 1, true);
   y += 20;
   if(cont) CvRow(cx, cr, y, "Déclenchement", g_goalActive ? "objectif en cours" : (ContArmed() ? "clôture M5 dans " + IntegerToString(MathMax(0, (int)(g_contArm + 300 - TimeCurrent()))) + " s" : "attente des filtres"), PC_TEXT, true);
   else     CvRow(cx, cr, y, "Compte à rebours", slotsOn ? Countdown(ns) : "—", PC_TEXT, true);
   y += 20;
   color rc;
   string rm = ResumeMsg(rc);
   y += CvWrap(cx, y, rm, rc, 9, cw, 3) + 4;
   if(slotsOn)
   {
      int maxChips = 12;
      int nchip = MathMin(g_slotsN, maxChips);
      int perRow = MathMin(nchip, 6);
      int gw = (cw - (perRow - 1) * 5) / perRow;
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
         if(start >= g_slotsN) start = 0;
      }
      bool nextMarked = false;
      for(int i = 0; i < nchip; i++)
      {
         int si = (start + i) % g_slotsN;
         int px = cx + (i % perRow) * (gw + 5), py = y + (i / perRow) * 26;
         bool passed = !many && weekday && (nowMin >= g_slots[si] + win);
         bool isNext = (!passed && !nextMarked && (weekday || i == 0));
         if(isNext) nextMarked = true;
         if(isNext) CvRound(px, py, gw, 21, 6, A(PC_ORANGE));
         else       { CvRound(px, py, gw, 21, 6, A(PC_BORDER)); CvRound(px + 1, py + 1, gw - 2, 19, 5, A(passed ? PC_CARD : PC_HEAD)); }
         CvText(px + gw / 2, py + 3, SlotText(g_slots[si]), isNext ? C'10,14,24' : (passed ? PC_GREY : PC_TEXT), 8, 2, isNext);
      }
      y += ((nchip - 1) / perRow + 1) * 26;
      CvText(cx, y, many ? IntegerToString(g_slotsN) + " créneaux · les " + IntegerToString(maxChips) + " prochains · fenêtre " + IntegerToString(win) + " min"
                         : "Lun–ven · fenêtre " + IntegerToString(win) + " min · sauté si un objectif est en cours", PC_DIM, 8);
      y += 16;
      // [ARCHI] début : 7.0.0
      if(g_cutPending)
      {
         CvText(cx, y, "Coupure " + g_cutName + " : la série en cours finit au TP, puis l'objectif est annulé", PC_ORANGE, 8, 0, true);
         y += 16;
      }
      else if(PBool(P_CT_ON) && CutsText() != "")
      {
         CvText(cx, y, "Coupure de l'objectif : " + CutsText(), PC_DIM, 8);
         y += 16;
      }
      // [ARCHI] fin
   }
   return CardEnd(0, y0, y);
   // [ARCHI] fin
}

int DrawGoal(int x, int w, int y0)
{
   int cx = x + 12, cr = x + w - 12, cw = w - 24;
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   int y = CardBegin(1, x, y0, w);
   if(g_goalActive)
   {
      double prog = GoalProgressPct();
      string pt = (prog > 999) ? ">999" : F0(prog);
      CvSection(cx, y, "OBJECTIF EN COURS");
      CvText(cr, y - 2, pt + " %", prog >= 0 ? PC_GREEN : PC_RED, 11, 1, true);
      y += 20;
      CvBar(cx, y, cw, 8, prog / 100.0, (prog >= 0 ? PC_GREEN : PC_RED));
      y += 16;
      CvRow(cx, cr, y, "Gain", Sgn(bal - g_goalInit) + " / " + F2(GoalAmount()) + " " + Cur(), PC_TEXT, true);
      y += 18;
      CvText(cx, y, "Départ " + F2(g_goalInit) + " · cible " + F2(g_goalTarget) + " · reste " + F2(MathMax(0.0, g_goalTarget - bal)), PC_DIM, 8);
      y += 16;
      CvText(cx, y, bal >= g_goalTarget ? "Cible atteinte : vérification du solde / clôture" : "Drawdown max de l'objectif " + F1(g_goalMaxDD) + " %", PC_DIM, 8);
      y += 16;
   }
   else
   {
      datetime ns = NextSlot();
      bool slotsOn = (!PBool(P_SL_CONT) && PBool(P_SL_ON) && g_slotsN > 0);   // [ARCHI] 7.8.6
      CvSection(cx, y, "OBJECTIF");
      y += 20;
      CvText(cx, y, "Aucun objectif actif", PC_TEXT, 9, 0, true);
      y += 18;
      CvText(cx, y, PBool(P_SL_CONT) ? "Mode continu : le suivant démarre quand tous les filtres sont OK"   // [ARCHI] 7.8.6
                    : (slotsOn ? "Le suivant démarre " + SlotLabel(ns) : "Lance un objectif depuis le bot (bouton Objectif)"), PC_DIM, 8);
      y += 16;
   }
   return CardEnd(1, y0, y);
}

int DrawAccount(int x, int w, int y0)
{
   int cx = x + 12, cr = x + w - 12, cw = w - 24;
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   double eq  = AccountInfoDouble(ACCOUNT_EQUITY);
   int y = CardBegin(2, x, y0, w);
   double dd = CurrentDDPct();
   color ddc = (dd >= PDbl(P_DDTHR)) ? PC_RED : ((dd >= PDbl(P_DDTHR) * 0.7) ? PC_ORANGE : PC_TEXT);
   double dayPl = (g_dayStartBal > 0) ? (bal - g_dayStartBal) : 0.0;   // réalisé du jour : le flottant a sa ligne
   double ml = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);
   CvSection(cx, y, "COMPTE");
   CvText(cr, y, g_aid, PC_DIM, 8, 1);
   y += 18;
   int half = cw / 2;
   CvText(cx, y, "Solde", PC_DIM, 8);
   CvText(cx + half, y, "Equity", PC_DIM, 8);
   y += 14;
   CvText(cx, y, F2(bal) + " " + Cur(), PC_TEXT, 12, 0, true);
   CvText(cx + half, y, F2(eq) + " " + Cur(), PC_TEXT, 12, 0, true);
   y += 24;
   CvRow(cx, cr, y, "Flottant", Sgn(st_floating), st_floating >= 0 ? PC_GREEN : PC_RED, true);
   y += 18;
   CvRow(cx, cr, y, "Résultat du jour (réalisé)", Sgn(dayPl), dayPl >= 0 ? PC_GREEN : PC_RED, true);
   y += 18;
   CvRow(cx, cr, y, "Drawdown", F1(dd) + " % / " + F0(PDbl(P_DDTHR)) + " %", ddc);
   y += 18;
   CvRow(cx, cr, y, "Niveau de marge", ml > 0 ? F0(ml) + " %" : "—", PC_TEXT);
   y += 22;
   // [ARCHI] début : progression vers le seuil d'arrêt (500) depuis le seuil de reprise (125)
   double lo = PDbl(P_SEUIL_REPRISE), hi = PDbl(P_SEUIL_ARRET);
   double frac = (hi > lo) ? (bal - lo) / (hi - lo) : 0.0;
   color pc = (bal < lo) ? PC_RED : (bal >= hi ? PC_ORANGE : PC_BLUE);
   CvRow(cx, cr, y, "Vers " + F0(hi) + " " + Cur() + " · cycle " + IntegerToString(g_cycle), bal >= hi ? "seuil atteint" : "reste " + F2(hi - bal), PC_TEXT);
   y += 18;
   CvBar(cx, y, cw, 8, frac, pc);
   y += 14;
   CvText(cx, y, F0(lo) + " (mini)", PC_DIM, 8);
   CvText(cr, y, bal < lo ? "solde < " + F0(lo) + " : pas de nouvelle série" : F0(MathMax(0.0, frac * 100.0)) + " %", bal < lo ? PC_RED : PC_DIM, 8, 1);
   y += 16;
   // [ARCHI] fin
   return CardEnd(2, y0, y);
}

int DrawSeries(int x, int w, int y0)
{
   int cx = x + 12, cr = x + w - 12;
   int y = CardBegin(3, x, y0, w);
   CvSection(cx, y, "SÉRIE EN COURS");
   y += 18;
   if(st_posCount > 0)
   {
      bool buy = (st_posType == POSITION_TYPE_BUY);
      double px = buy ? SymbolInfoDouble(_Symbol, SYMBOL_BID) : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double toTp = (g_tpPrice > 0) ? MathAbs(g_tpPrice - px) / _Point : 0;
      double nxt  = buy ? st_highPend : st_lowPend;
      string side = buy ? "ACHAT" : "VENTE";
      int sw = CvTextW(side, 8, true) + 18;
      CvPill(cx, y - 1, sw, 19, side, buy ? PC_GREEN : PC_RED);
      CvText(cx + sw + 8, y, IntegerToString(st_posCount) + " position(s) · " + F2(st_posLots) + " lot", PC_TEXT, 9, 0, true);
      CvText(cr, y, Sgn(st_floating), st_floating >= 0 ? PC_GREEN : PC_RED, 10, 1, true);
      y += 24;
      CvRow(cx, cr, y, "Prix moyen", PX(st_avg), PC_TEXT);
      y += 18;
      CvRow(cx, cr, y, "Take profit", g_tpPrice > 0 ? PX(g_tpPrice) + "  (" + F0(toTp) + " pts)" : "en calcul", PC_GREEN);
      y += 18;
      CvRow(cx, cr, y, "Grille " + IntegerToString(st_pendCount) + "/" + IntegerToString(PInt(P_MAXPEND)) + " ordres", nxt > 0 ? "prochain " + PX(nxt) : "", PC_DIM);
      y += 18;
   }
   else
   {
      bool up = mk_fast > mk_slow;
      CvText(cx, y, "Aucune position", PC_TEXT, 9, 0, true);
      CvText(cr, y, "Signal " + string(up ? "ACHAT" : (mk_fast < mk_slow ? "VENTE" : "—")), up ? PC_GREEN : PC_RED, 9, 1, true);
      y += 18;
      CvText(cx, y, "EMA" + IntegerToString(PInt(P_FAST)) + (up ? " > " : " < ") + "EMA" + IntegerToString(PInt(P_SLOW)) +
             (st_pendCount > 0 ? " · " + IntegerToString(st_pendCount) + " ordre(s) en attente" : ""), PC_DIM, 8);
      y += 16;
   }
   return CardEnd(3, y0, y);
}

int DrawMarket(int x, int w, int y0, bool chips = true)
{
   int cx = x + 12, cr = x + w - 12, cw = w - 24;
   int y = CardBegin(4, x, y0, w);
   CvSection(cx, y, "MARCHÉ · " + TfLabel() + (BarShift() == 0 ? " · bougie en cours" : " · bougie clôturée"));
   y += 18;
   CvText(cx, y, "EMA" + IntegerToString(PInt(P_FAST)) + "  " + PX(mk_fast), C_EMA_F, 9, 0, true);
   CvText(cr, y, "EMA" + IntegerToString(PInt(P_SLOW)) + "  " + PX(mk_slow), C_EMA_S, 9, 1, true);
   y += 18;
   CvRow(cx, cr, y, "ATR " + F0(mk_atrPts) + " pts" + (PBool(P_F_ATR) ? "  [" + F0(PDbl(P_ATRMIN)) + "–" + F0(PDbl(P_ATRMAX)) + "]" : ""),
         "Spread " + F0(mk_spread) + " / " + IntegerToString(PInt(P_MAXSPREAD)), (mk_spread > PInt(P_MAXSPREAD)) ? PC_RED : PC_TEXT);
   y += 22;
   if(!chips) { y -= 4; return CardEnd(4, y0, y); }
   CvText(cx, y, "FILTRES", PC_DIM, 8, 0, true);
   CvText(cr, y, FbBlockedCount() == 0 ? "tous au vert" : IntegerToString(FbBlockedCount()) + " bloquant(s)", FbBlockedCount() == 0 ? PC_GREEN : PC_RED, 8, 1, true);
   y += 18;
   // pastilles : vert = OK, rouge = bloque, gris = désactivé (même ordre que le bot)
   int fw = (cw - 2 * 6) / 3;
   for(int k = 0; k < FB_N; k++)
   {
      int i = FbOrder(k);
      bool on = FbOn(i), ok = FbOK(i);
      color c = !on ? PC_GREY : (ok ? PC_GREEN : PC_RED);
      int px = cx + (k % 3) * (fw + 6), py = y + (k / 3) * 26;
      CvRound(px, py, fw, 21, 10, A(c, (uchar)(on ? 40 : 22)));
      CvDot(px + 11, py + 10, 3, c);
      string nm = FbName(i);
      StringReplace(nm, "Volatilité (ATR)", "ATR");
      StringReplace(nm, " précédente", "");
      CvText(px + 20, py + 3, Trunc(nm, 15), on ? PC_TEXT : PC_DIM, 8, 0, !ok && on);
   }
   y += ((FB_N - 1) / 3 + 1) * 26 - 4;
   return CardEnd(4, y0, y);
}


//--- détail d'un filtre : valeur actuelle, règle, état (même lecture que l'ATR « 787 pts [250–800] »)
void FilterDetail(int i, string &value, string &rule)
{
   string sig = (mk_fast > mk_slow) ? "achat" : ((mk_fast < mk_slow) ? "vente" : "aucun");
   switch(i)
   {
      case 0: value = F0(mk_atrPts) + " pts"; rule = "plage " + F0(PDbl(P_ATRMIN)) + "–" + F0(PDbl(P_ATRMAX)) + " pts"; break;
      case 1: value = "prix " + PX(mk_bid) + " · EMA" + IntegerToString(PInt(P_FAST)) + " " + PX(mk_fast);
              rule = "prix du côté du signal (" + sig + ") de l'EMA rapide"; break;
      case 2: value = F0(mk_volRatio * 100) + " % de la moyenne"; rule = "plage " + F0(PDbl(P_VOLMIN) * 100) + "–" + F0(PDbl(P_VOLMAX) * 100) + " %"; break;
      case 3: value = FbTrend(mk_loF, mk_loS) + " (" + PX(mk_loF) + " / " + PX(mk_loS) + ")"; rule = "dans le sens du signal (" + sig + ")"; break;
      case 4: value = FbTrend(mk_hiF, mk_hiS) + " (" + PX(mk_hiF) + " / " + PX(mk_hiS) + ")"; rule = "dans le sens du signal (" + sig + ")"; break;
      case 5: value = (mk_candleDir > 0 ? "▲ haussière" : (mk_candleDir < 0 ? "▼ baissière" : "– neutre")) + ", corps " + F0(MathAbs(mk_candleBody)) + " pts";
              rule = "corps ≥ " + F0(mk_candleMin) + " pts, dans le sens du signal"; break;
      case 6: value = "il est " + TimeToString(LocalNow(), TIME_MINUTES); rule = "pas d'entrée de " + IntegerToString(PInt(P_T_START)) + "h à " + IntegerToString(PInt(P_T_END)) + "h"; break;
      case 7:
      {
         string nx = NewsNextText();
         value = g_newsBlock ? g_newsWhy : (nx != "" ? "prochaine : " + nx : "aucune dans les 24 h");
         rule = "pas d'entrée " + IntegerToString(PInt(P_NEWS_BEFORE)) + " min avant à " + IntegerToString(PInt(P_NEWS_AFTER)) + " min après";
         break;
      }
      // [ARCHI] début : 6.6.0
      case 9: value = F1(MathAbs(mk_extAtr)) + " x ATR de l'EMA lente" + ExtTfText(); rule = "maximum " + F1(PDbl(P_EXTATR)) + " x ATR dans le sens du signal (" + sig + ")"; break;
      case 10: value = FreshText(); rule = "croisement des EMA il y a " + IntegerToString(PInt(P_FRESHBARS)) + " bougies au plus"; break;
      // [ARCHI] fin
      default: value = F0(mk_spread) + " pts"; rule = "maximum " + IntegerToString(PInt(P_MAXSPREAD)) + " pts"; break;
   }
}

int DrawFilters(int x, int w, int y0, bool detailed = true)
{
   int cx = x + 12, cr = x + w - 12, cw = w - 24;
   int y = CardBegin(5, x, y0, w);
   int nb = FbBlockedCount();
   CvSection(cx, y, "FILTRES");
   CvText(cr, y, nb == 0 ? "tous au vert : entrée possible" : IntegerToString(nb) + " bloquant(s)", nb == 0 ? PC_GREEN : PC_RED, 8, 1, true);
   y += 20;
   for(int k = 0; k < FB_N; k++)
   {
      int i = FbOrder(k);
      bool on = FbOn(i), ok = FbOK(i);
      color c = !on ? PC_GREY : (ok ? PC_GREEN : PC_RED);
      string value, rule;
      FilterDetail(i, value, rule);
      string why = (on && !ok) ? FbWhy(i) : "";
      int rh = 42 + (detailed ? 16 : 0) + (why != "" ? 16 : 0);
      CvRound(cx, y, cw, rh - 6, 8, A(c, (uchar)(on ? (ok ? 26 : 40) : 14)));
      CvRound(cx, y, 4, rh - 6, 2, A(c));
      CvDot(cx + 16, y + 11, 4, c);
      CvText(cx + 28, y + 3, FbName(i), on ? PC_TEXT : PC_DIM, 9, 0, true);
      string st = !on ? "désactivé" : (ok ? "OK" : "BLOQUE");
      int sw = CvTextW(st, 8, true) + 16;
      CvPill(cr - 6 - sw, y + 3, sw, 17, st, c, on && !ok);
      int tw = cw - 40;
      int ly = y + 20;
      if(on) { CvText(cx + 28, ly, Fit(value, 8, tw), PC_TEXT, 8); ly += 16; }
      if(detailed || !on) { CvText(cx + 28, ly, Fit("règle : " + rule, 8, tw), PC_DIM, 8); ly += 16; }
      if(why != "") CvText(cx + 28, ly, Fit(why, 8, tw, true), PC_RED, 8, 0, true);
      if(!on && !detailed) rh = 42;
      y += rh;
   }
   return CardEnd(5, y0, y - 4);
}

//+------------------------------------------------------------------+
//| Dessin complet (une passe de mesure, puis une passe de dessin).  |
//| Grand écran : deux colonnes ; réduit (–) : une colonne.          |
//+------------------------------------------------------------------+
int PanelWidth()
{
   if(g_panelCompact) return 380;
   int cw = (int)ChartGetInteger(0, CHART_WIDTH_IN_PIXELS) - PN_X - (CHART_PRICE_HIDDEN ? 12 : 70);   // [ARCHI] 7.9.0 : plus d'échelle des prix
   return MathMax(740, MathMin(2400, cw));                                  // toute la largeur utile
}

// [ARCHI] début 7.10.0 : statistiques des trades de l'EA (historique du broker), par période
#define ST_NP 5
int    g_stTrades[ST_NP], g_stLong[ST_NP], g_stShort[ST_NP], g_stWon[ST_NP], g_stLost[ST_NP];
double g_stNet[ST_NP];
datetime g_stCalcAt = 0;
int    g_stDeals = -1;

string StatsLabel(int k) { static string l[ST_NP] = { "24 h", "48 h", "7 jours", "30 jours", "3 mois" }; return l[k]; }
int    StatsSecs(int k)  { static int d[ST_NP] = { 86400, 172800, 604800, 2592000, 7776000 }; return d[k]; }

// Recalcul toutes les 60 s, ou dès que le nombre de positions ouvertes change : un trade = une position de l'EA fermée sur ce symbole.
void StatsRefresh()
{
   datetime now = TimeCurrent();
   if(now <= 0) now = TimeTradeServer();
   if(st_posCount == g_stDeals && now - g_stCalcAt < 60) return;   // (g_stDeals : nombre de positions au dernier calcul)
   if(!HistorySelect(now - StatsSecs(ST_NP - 1) - 3600, now + 3600)) return;
   int nd = HistoryDealsTotal();
   g_stDeals = st_posCount;
   g_stCalcAt = now;
   for(int k = 0; k < ST_NP; k++) { g_stTrades[k] = g_stLong[k] = g_stShort[k] = g_stWon[k] = g_stLost[k] = 0; g_stNet[k] = 0; }
   for(int i = 0; i < nd; i++)
   {
      ulong t = HistoryDealGetTicket(i);
      if(t == 0) continue;
      if(HistoryDealGetInteger(t, DEAL_MAGIC) != MagicNumber || HistoryDealGetString(t, DEAL_SYMBOL) != _Symbol) continue;
      long typ = HistoryDealGetInteger(t, DEAL_TYPE);
      if(typ != DEAL_TYPE_BUY && typ != DEAL_TYPE_SELL) continue;
      long ent = HistoryDealGetInteger(t, DEAL_ENTRY);
      double net = HistoryDealGetDouble(t, DEAL_PROFIT) + HistoryDealGetDouble(t, DEAL_SWAP) + HistoryDealGetDouble(t, DEAL_COMMISSION);
      long age = (long)(now - (datetime)HistoryDealGetInteger(t, DEAL_TIME));
      bool out = (ent == DEAL_ENTRY_OUT || ent == DEAL_ENTRY_INOUT || ent == DEAL_ENTRY_OUT_BY);
      for(int k = 0; k < ST_NP; k++)
      {
         if(age > StatsSecs(k)) continue;
         g_stNet[k] += net;                                      // commissions d'entrée comprises
         if(!out) continue;
         g_stTrades[k]++;
         if(typ == DEAL_TYPE_SELL) g_stLong[k]++; else g_stShort[k]++;   // une vente ferme un achat (long)
         if(net >= 0) g_stWon[k]++; else g_stLost[k]++;
      }
   }
}

int DrawStats(int x, int w, int y0)
{
   StatsRefresh();
   int cx = x + 12, cw = w - 24;
   int y = CardBegin(7, x, y0, w);
   CvSection(cx, y, "STATISTIQUES DES TRADES");
   CvText(x + w - 12, y, "positions de l'EA fermées · " + _Symbol, PC_DIM, 8, 1);
   y += 20;
   string head[8] = { "Période", "Trades", "Longs", "Courts", "Gagnés", "Perdus", "Réussite", "Résultat" };
   int col[8];
   int first = cw * 16 / 100, rest = (cw - first) / 7;
   col[0] = cx;
   for(int c = 1; c < 8; c++) col[c] = cx + first + rest * c;     // colonnes alignées à droite
   for(int c = 0; c < 8; c++) CvText(col[c], y, head[c], PC_DIM, 8, c == 0 ? 0 : 1, true);
   y += 16;
   CvRect(cx, y, cx + cw, y, A(PC_BORDER));
   y += 6;
   for(int k = 0; k < ST_NP; k++)
   {
      int n = g_stTrades[k];
      CvText(col[0], y, StatsLabel(k), PC_TEXT, 9, 0, true);
      CvText(col[1], y, IntegerToString(n), PC_TEXT, 9, 1, true);
      CvText(col[2], y, IntegerToString(g_stLong[k]), PC_TEXT, 9, 1);
      CvText(col[3], y, IntegerToString(g_stShort[k]), PC_TEXT, 9, 1);
      CvText(col[4], y, IntegerToString(g_stWon[k]), PC_GREEN, 9, 1);
      CvText(col[5], y, IntegerToString(g_stLost[k]), g_stLost[k] > 0 ? PC_RED : PC_DIM, 9, 1);
      CvText(col[6], y, n > 0 ? F0((double)g_stWon[k] / n * 100.0) + " %" : "—", PC_TEXT, 9, 1);
      CvText(col[7], y, Sgn(g_stNet[k]) + " " + Cur(), g_stNet[k] >= 0 ? PC_GREEN : PC_RED, 9, 1, true);
      y += 18;
   }
   return CardEnd(7, y0, y);
}
// [ARCHI] fin

int PanelDraw()
{
   int W = PanelWidth(), pad = 14;
   int xl = pad, xr = W - pad, iw = W - 2 * pad;
   int H = g_meas ? 2000 : g_cvH;

   // fond (opaque : le graphique ne transparaît pas) et bord
   CvRound(0, 0, W, H, 12, A(PC_BORDER));
   CvRound(1, 1, W - 2, H - 2, 11, A(PC_BG));

   //--- En-tête : nom, version, symbole, serveur, état
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
   CvRound(1, 1, W - 2, 46, 11, A(PC_HEAD));
   CvRect(1, 36, W - 2, 46, A(PC_HEAD));
   CvText(xl, 9, "ScalpX", PC_TEXT, 12, 0, true);
   CvText(xl + CvTextW("ScalpX", 12, true) + 6, 13, "v" + SX_VERSION, PC_DIM, 8);
   string srv = AccountInfoString(ACCOUNT_SERVER);
   CvText(xl, 28, _Symbol + "  ·  " + TfLabel() + (srv != "" ? "  ·  " + srv : ""), PC_DIM, 8);
   int pw = CvTextW(badge, 8, true) + 22;
   CvPill(xr - pw, 12, pw, 22, badge, badgeCol, true);
   CvButton(0, xr - pw - 30, 12, 24, 22, g_panelCompact ? "+" : "–", PC_BORDER, PC_TEXT);
   CvRect(1, 47, W - 2, 47, A(PC_BORDER));
   int y = 58;

   //--- Bandeau : ce que fait le robot maintenant (toute la largeur)
   color bc;
   string bm = BannerMsg(bc);
   int bh = 30;
   CvCard(xl, y, iw, bh, PC_CARD, PC_BORDER, 8);
   CvRound(xl + 1, y + 1, 4, bh - 2, 2, A(bc));
   CvDot(xl + 18, y + bh / 2, 4, bc);
   CvText(xl + 30, y + 8, Trunc(bm, g_panelCompact ? 56 : 110), bc, 9, 0, true);
   y += bh + 10;

   if(g_panelCompact)
   {
      y = DrawPlanning(xl, iw, y) + 10;
      y = DrawGoal(xl, iw, y) + 10;
   }
   else if(W >= 1040)
   {
      // trois colonnes : planning / objectif / série · compte / marché · filtres en détail
      int gap = 12, cw = (iw - 2 * gap) / 3;
      int xa = xl, xb = xl + cw + gap, xc = xl + 2 * (cw + gap);
      int ya = DrawPlanning(xa, cw, y) + 10;
      ya = DrawGoal(xa, cw, ya) + 10;
      ya = DrawSeries(xa, cw, ya) + 10;
      int yb = DrawAccount(xb, cw, y) + 10;
      yb = DrawMarket(xb, cw, yb, false) + 10;
      int yc = DrawFilters(xc, cw, y) + 10;
      y = MathMax(ya, MathMax(yb, yc));
   }
   else
   {
      // deux colonnes (graphique étroit) : planning / objectif / série · compte / marché / filtres en détail
      int gap = 12, cw = (iw - gap) / 2;
      int xa = xl, xb = xl + cw + gap;
      int ya = DrawPlanning(xa, cw, y) + 10;
      ya = DrawGoal(xa, cw, ya) + 10;
      ya = DrawSeries(xa, cw, ya) + 10;
      int yb = DrawAccount(xb, cw, y) + 10;
      yb = DrawMarket(xb, cw, yb, false) + 10;
      yb = DrawFilters(xb, cw, yb, false) + 10;
      y = MathMax(ya, yb);
   }
   if(!g_panelCompact) y = DrawStats(xl, iw, y) + 10;   // [ARCHI] 7.10.0 : statistiques (toute la largeur)
   if(!g_panelCompact)
   {
      //--- Session (toute la largeur)
      int tot = g_sesWins + g_sesLosses;
      CvText(xl + 2, y, "SESSION", PC_DIM, 8, 0, true);
      CvText(xr - 2, y, "Trades " + IntegerToString(tot) + " · " + IntegerToString(g_sesWins) + " gagnants / " + IntegerToString(g_sesLosses) + " perdants" +
             (tot > 0 ? " · " + F0((double)g_sesWins / tot * 100.0) + " %" : "") + "   ·   résultat " + Sgn(g_sesProfit) + " " + Cur(),
             g_sesProfit >= 0 ? PC_GREEN : PC_RED, 8, 1);
      y += 22;
   }

   //--- Boutons
   int bw = (iw - 10) / 2;
   CvButton(1, xl, y, bw, 30, g_enabled ? "Pause" : "Démarrer", g_enabled ? C'58,70,98' : C'24,150,104', C'240,244,250');
   CvButton(2, xl + bw + 10, y, bw, 30, g_btnCloseArmed ? "Confirmer ?" : "Tout fermer", g_btnCloseArmed ? PC_RED : C'120,46,54', C'240,244,250');
   y += 40;

   //--- Pied : liaison avec le bot central
   // [ARCHI] début
   bool conn  = (TerminalInfoInteger(TERMINAL_CONNECTED) != 0);
   bool algoT = (TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) != 0);
   color tgc = (g_state == ST_ACTIF && !g_maint) ? PC_GREEN : PC_ORANGE;
   CvDot(xl + 4, y + 7, 4, tgc);
   CvText(xl + 14, y, "Bot central · " + StateName(g_state) + (g_maint ? " · maintenance" : "") + " · cycle " + IntegerToString(g_cycle), PC_DIM, 8);
   if(!g_panelCompact)
   {
      CvDot(xl + iw / 2 + 4, y + 7, 4, (conn && algoT) ? PC_GREEN : PC_RED);
      CvText(xl + iw / 2 + 14, y, string(conn ? "Connecté" : "HORS LIGNE") + " · Algo Trading " + string(algoT ? "activé" : "DÉSACTIVÉ"), (conn && algoT) ? PC_DIM : PC_RED, 8);
   }
   else
   {
      y += 16;
      CvDot(xl + 4, y + 7, 4, (conn && algoT) ? PC_GREEN : PC_RED);
      CvText(xl + 14, y, string(conn ? "Connecté" : "HORS LIGNE") + " · Algo Trading " + string(algoT ? "activé" : "DÉSACTIVÉ"), (conn && algoT) ? PC_DIM : PC_RED, 8);
   }
   // [ARCHI] fin
   if(ChartPeriod(0) != g_sigTF)
   {
      y += 16;
      CvText(xl + 14, y, "Graphique " + EnumToString(ChartPeriod(0)) + " ≠ robot " + TfLabel(), PC_ORANGE, 8);
   }
   y += 24;
   return y;
}

//+------------------------------------------------------------------+
//| Mise à jour (au plus 2 fois par seconde, et seulement si le      |
//| contenu a changé : aucun clignotement, très peu de CPU)          |
//+------------------------------------------------------------------+
void PanelUpdate()
{
   if(!PBool(P_CH_PANEL)) return;
   ulong now = GetTickCount64();
   if(now - g_panelMs < 500) return;
   g_panelMs = now;
   if(g_btnCloseArmed && now - g_btnCloseMs > 4000) g_btnCloseArmed = false;

   if(!g_cvOk)
   {
      ObjectsDeleteAll(0, PNM(""));                       // objets de l'ancien panneau
      g_cvW = PanelWidth(); g_cvH = 600;
      ArrayResize(g_px, g_cvW * g_cvH);
      ArrayInitialize(g_px, 0);
      if(!ResourceCreate(PN_RES, g_px, g_cvW, g_cvH, 0, 0, g_cvW, COLOR_FORMAT_ARGB_NORMALIZE)) return;
      if(!ObjectCreate(0, PNM("cv"), OBJ_BITMAP_LABEL, 0, 0, 0)) return;
      ObjectSetString(0, PNM("cv"), OBJPROP_BMPFILE, 0, PN_RES);
      ObjectSetInteger(0, PNM("cv"), OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, PNM("cv"), OBJPROP_XDISTANCE, PN_X);
      ObjectSetInteger(0, PNM("cv"), OBJPROP_YDISTANCE, PN_Y);
      ObjectSetInteger(0, PNM("cv"), OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, PNM("cv"), OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, PNM("cv"), OBJPROP_BACK, false);
      g_cvOk = true;
      g_cvSig = "";
   }
   if(ObjectFind(0, PNM("cv")) < 0) { g_cvOk = false; ResourceFree(PN_RES); return; }   // supprimé de l'extérieur : recréé au tour suivant

   // 1) mesure : hauteur exacte du panneau (aucun fond trop court ni trop long)
   g_meas = true;
   g_cvCur = "";
   int h = PanelDraw();
   g_meas = false;
   if(h != g_cvH || g_cvW != PanelWidth())
   {
      g_cvH = h;
      g_cvW = PanelWidth();
      ArrayResize(g_px, g_cvW * g_cvH);
      ObjectSetInteger(0, PNM("cv"), OBJPROP_XSIZE, g_cvW);
      ObjectSetInteger(0, PNM("cv"), OBJPROP_YSIZE, g_cvH);
   }
   // 2) dessin, renvoyé au graphique seulement si quelque chose a changé
   g_cvCur = IntegerToString(h) + ";";
   ArrayInitialize(g_px, 0);
   PanelDraw();
   if(g_cvCur == g_cvSig) return;
   g_cvSig = g_cvCur;
   ResourceCreate(PN_RES, g_px, g_cvW, g_cvH, 0, 0, g_cvW, COLOR_FORMAT_ARGB_NORMALIZE);
   ChartRedraw(0);
}
//+------------------------------------------------------------------+
