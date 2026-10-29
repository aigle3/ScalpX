//+------------------------------------------------------------------+
//| SX_Journal.mqh - ScalpX                                          |
//| Journée en cours uniquement : compteurs du jour, journal des     |
//| filtres du jour, courbe d'equity du jour, rapport du jour.       |
//| Aucun historique des jours précédents.                           |
//+------------------------------------------------------------------+
#define EQ_MAX 1500

//--- Fichiers (MQL5\Files)
string EqFile()  { return "ScalpX4_" + IntegerToString(MagicNumber) + "_eq.csv"; }

void AppendLine(string fname, string line)
{
   int h = FileOpen(fname, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE) return;
   FileSeek(h, 0, SEEK_END);
   FileWriteString(h, line + "\r\n");
   FileClose(h);
}

// Lit les dernières lignes d'un fichier texte (keep <= 0 : toutes)
int ReadLines(string fname, string &out[], int keep)
{
   ArrayResize(out, 0);
   int h = FileOpen(fname, FILE_READ | FILE_TXT | FILE_ANSI | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE) return 0;
   int n = 0;
   while(!FileIsEnding(h))
   {
      string l = FileReadString(h);
      if(StringLen(l) < 3) continue;
      ArrayResize(out, n + 1, 256);
      out[n] = l;
      n++;
   }
   FileClose(h);
   if(keep > 0 && n > keep)
   {
      int d = n - keep;
      for(int i = 0; i < keep; i++) out[i] = out[i + d];
      ArrayResize(out, keep);
      n = keep;
   }
   return n;
}

//+------------------------------------------------------------------+
//| Mise en forme                                                    |
//+------------------------------------------------------------------+
string Bar(double pct, int len = 10)
{
   if(pct < 0) pct = 0;
   if(pct > 100) pct = 100;
   int f = (int)MathRound(pct / 100.0 * len);
   string s = "";
   for(int i = 0; i < len; i++) s += (i < f) ? "▓" : "░";
   return s;
}

string Dot3(double ratio)      // ratio d'usage : < 50 % vert, < 80 % orange, sinon rouge
{
   if(ratio < 0.5) return "🟢";
   if(ratio < 0.8) return "🟡";
   return "🔴";
}

string FmtDur(double sec)
{
   int s = (int)MathRound(sec);
   if(s < 60) return IntegerToString(s) + " s";
   if(s < 3600) return IntegerToString(s / 60) + " min";
   int m = (s % 3600) / 60;
   return IntegerToString(s / 3600) + " h " + (m < 10 ? "0" : "") + IntegerToString(m);
}

string Hh(datetime t) { return TimeToString(t, TIME_MINUTES); }

//+------------------------------------------------------------------+
//| Jour courant (fuseau choisi) : compteurs de la journée                |
//+------------------------------------------------------------------+
int      g_dayKey = 0;
double   g_dayNet = 0;
int      g_daySeries = 0, g_dayWins = 0;
double   g_dayMaxDD = 0;

int DayKeyNow()
{
   MqlDateTime d;
   TimeToStruct(LocalNow(), d);
   return d.year * 10000 + d.mon * 100 + d.day;
}

void DaySave()
{
   GlobalVariableSet(GVN("db"), g_dayStartBal);
   GlobalVariableSet(GVN("dk"), g_dayKey);
   GlobalVariableSet(GVN("dn"), g_dayNet);
   GlobalVariableSet(GVN("ds"), g_daySeries);
   GlobalVariableSet(GVN("dw"), g_dayWins);
   GlobalVariableSet(GVN("dm"), g_dayMaxDD);
}

void DayLoad()
{
   g_dayKey = DayKeyNow();
   g_dayPeakEq = AccountInfoDouble(ACCOUNT_EQUITY);
   g_dayStartBal = AccountInfoDouble(ACCOUNT_BALANCE);
   if(GlobalVariableCheck(GVN("dk")) && (int)GlobalVariableGet(GVN("dk")) == g_dayKey)
   {
      g_dayNet = GlobalVariableGet(GVN("dn"));
      g_daySeries = (int)GlobalVariableGet(GVN("ds"));
      g_dayWins = (int)GlobalVariableGet(GVN("dw"));
      g_dayMaxDD = GlobalVariableGet(GVN("dm"));
      g_dayStartBal = GlobalVariableCheck(GVN("db")) ? GlobalVariableGet(GVN("db")) : g_dayStartBal - g_dayNet;   // sauvegarde d'une ancienne version
   }
}

//+------------------------------------------------------------------+
//| Courbe d'equity du jour                                          |
//+------------------------------------------------------------------+
datetime g_eqT[EQ_MAX];
double   g_eqV[EQ_MAX];
int      g_eqN = 0;
ulong    g_eqLastMs = 0;

void EquityLoad()
{
   g_eqN = 0;
   string ln[];
   int n = ReadLines(EqFile(), ln, EQ_MAX);
   datetime mid = StringToTime(TimeToString(LocalNow(), TIME_DATE));
   for(int i = 0; i < n && g_eqN < EQ_MAX; i++)
   {
      string f[];
      if(StringSplit(ln[i], ';', f) < 2) continue;
      datetime t = (datetime)StringToInteger(f[0]);
      if(t < mid) continue;
      g_eqT[g_eqN] = t;
      g_eqV[g_eqN] = StringToDouble(f[1]);
      g_eqN++;
   }
}

void EquitySample()
{
   ulong now = GetTickCount64();
   if(g_eqN > 0 && now - g_eqLastMs < 60000) return;
   g_eqLastMs = now;
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   if(eq <= 0) return;
   if(g_eqN >= EQ_MAX)
   {
      for(int i = 1; i < EQ_MAX; i++) { g_eqT[i - 1] = g_eqT[i]; g_eqV[i - 1] = g_eqV[i]; }
      g_eqN = EQ_MAX - 1;
   }
   g_eqT[g_eqN] = LocalNow();
   g_eqV[g_eqN] = eq;
   g_eqN++;
   AppendLine(EqFile(), IntegerToString((long)LocalNow()) + ";" + DoubleToString(eq, 2));
}

string EquitySpark(int len = 24)
{
   string lv[8] = { "▁", "▂", "▃", "▄", "▅", "▆", "▇", "█" };
   if(g_eqN < 2) return "▁▁▁▁▁▁▁▁▁▁▁▁";
   double mn = g_eqV[0], mx = g_eqV[0];
   for(int i = 1; i < g_eqN; i++) { if(g_eqV[i] < mn) mn = g_eqV[i]; if(g_eqV[i] > mx) mx = g_eqV[i]; }
   string s = "";
   for(int k = 0; k < len; k++)
   {
      int idx = (int)MathFloor((double)(k + 1) * g_eqN / len) - 1;
      if(idx < 0) idx = 0;
      if(idx >= g_eqN) idx = g_eqN - 1;
      double r = (mx > mn) ? (g_eqV[idx] - mn) / (mx - mn) : 0.5;
      int l = (int)MathRound(r * 7.0);
      if(l < 0) l = 0;
      if(l > 7) l = 7;
      s += lv[l];
   }
   return s;
}

//--- Image BMP 24 bits dessinée à la main (aucune bibliothèque)
void BmpPut32(uchar &a[], int pos, int v)
{
   a[pos]     = (uchar)(v & 0xFF);
   a[pos + 1] = (uchar)((v >> 8) & 0xFF);
   a[pos + 2] = (uchar)((v >> 16) & 0xFF);
   a[pos + 3] = (uchar)((v >> 24) & 0xFF);
}

void BmpPx(uchar &a[], int rowSize, int W, int H, int x, int y, uint rgb)
{
   if(x < 0 || x >= W || y < 0 || y >= H) return;
   int pos = 54 + (H - 1 - y) * rowSize + x * 3;
   a[pos]     = (uchar)(rgb & 0xFF);
   a[pos + 1] = (uchar)((rgb >> 8) & 0xFF);
   a[pos + 2] = (uchar)((rgb >> 16) & 0xFF);
}

bool EquityBmp(string fname, int W, int H)
{
   if(g_eqN < 2) return false;
   int rowSize = ((W * 3 + 3) / 4) * 4;
   int dataSize = rowSize * H;
   uchar img[];
   ArrayResize(img, 54 + dataSize);
   ArrayInitialize(img, 0);
   img[0] = 'B'; img[1] = 'M';
   BmpPut32(img, 2, 54 + dataSize);
   BmpPut32(img, 10, 54);
   BmpPut32(img, 14, 40);
   BmpPut32(img, 18, W);
   BmpPut32(img, 22, H);
   img[26] = 1; img[28] = 24;
   BmpPut32(img, 34, dataSize);

   uint bg = 0x11151C, gridC = 0x232A35, baseC = 0x6B7686;
   for(int yy = 0; yy < H; yy++)
      for(int xx = 0; xx < W; xx++) BmpPx(img, rowSize, W, H, xx, yy, bg);

   int mL = 12, mR = 12, mT = 14, mB = 14;
   double mn = g_eqV[0], mx = g_eqV[0];
   for(int i = 1; i < g_eqN; i++) { if(g_eqV[i] < mn) mn = g_eqV[i]; if(g_eqV[i] > mx) mx = g_eqV[i]; }
   if(mx - mn < 0.01) { mx += 0.5; mn -= 0.5; }
   double pad = (mx - mn) * 0.08;
   mx += pad; mn -= pad;
   int pw = W - mL - mR, ph = H - mT - mB;

   for(int gi = 0; gi <= 4; gi++)
   {
      int gy = mT + ph * gi / 4;
      for(int gx = mL; gx < W - mR; gx++) BmpPx(img, rowSize, W, H, gx, gy, gridC);
   }
   int y0 = mT + (int)MathRound((1.0 - (g_eqV[0] - mn) / (mx - mn)) * ph);
   for(int bx = mL; bx < W - mR; bx += 8)
      for(int bk = 0; bk < 4; bk++) BmpPx(img, rowSize, W, H, bx + bk, y0, baseC);

   bool up = (g_eqV[g_eqN - 1] >= g_eqV[0]);
   uint lineC = up ? 0x2ECC71 : 0xE74C3C;
   uint fillC = up ? 0x16382A : 0x3A1A1A;
   int prevY = -1;
   for(int x = 0; x < pw; x++)
   {
      double fi = (double)x / (double)(pw - 1) * (g_eqN - 1);
      int i0 = (int)MathFloor(fi);
      int i1 = i0 + 1;
      if(i1 > g_eqN - 1) i1 = g_eqN - 1;
      double fr = fi - i0;
      double v = g_eqV[i0] + (g_eqV[i1] - g_eqV[i0]) * fr;
      int y = mT + (int)MathRound((1.0 - (v - mn) / (mx - mn)) * ph);
      for(int fy = y + 2; fy < H - mB; fy++) BmpPx(img, rowSize, W, H, mL + x, fy, fillC);
      int ya = y, yb = y;
      if(prevY >= 0) { ya = MathMin(prevY, y); yb = MathMax(prevY, y); }
      for(int ly = ya - 1; ly <= yb + 1; ly++) BmpPx(img, rowSize, W, H, mL + x, ly, lineC);
      prevY = y;
   }
   int fh = FileOpen(fname, FILE_WRITE | FILE_BIN);
   if(fh == INVALID_HANDLE) return false;
   FileWriteArray(fh, img);
   FileClose(fh);
   return true;
}

string EquityCaption()
{
   if(g_eqN < 2) return "📈 Pas encore assez de points pour la courbe du jour.";
   double mn = g_eqV[0], mx = g_eqV[0];
   for(int i = 1; i < g_eqN; i++) { if(g_eqV[i] < mn) mn = g_eqV[i]; if(g_eqV[i] > mx) mx = g_eqV[i]; }
   double d = g_eqV[g_eqN - 1] - g_eqV[0];
   return "📈 Equity du jour · " + _Symbol + "\n" + F2(g_eqV[0]) + " → " + F2(g_eqV[g_eqN - 1]) + " " + Cur() + " (" + Sgn(d) + ")\n" +
          "▼ min " + F2(mn) + "  ▲ max " + F2(mx) + "\nDepuis " + Hh(g_eqT[0]) + " · " + IntegerToString(g_eqN) + " points";
}

//+------------------------------------------------------------------+
//| Séries du jour (suivi en direct, aucune écriture sur disque)     |
//+------------------------------------------------------------------+
bool     g_srActive = false, g_srEnding = false;
ulong    g_srEndMs = 0;
double   g_srNet = 0;

void SeriesFinalize()
{
   g_daySeries++;
   if(g_srNet > 0) g_dayWins++;
   DaySave();
   g_srEnding = false;
   g_srNet = 0;
}

// Appelé à chaque deal de sortie (net = profit + swap + commissions)
void JournalOnDeal(double net)
{
   g_srNet += net;
   g_dayNet += net;
}

void JournalTick()
{
   ulong now = GetTickCount64();
   if(st_posCount > 0)
   {
      if(g_srEnding) SeriesFinalize();                 // une nouvelle série démarre : l'ancienne est clôturée
      g_srActive = true;
   }
   else if(g_srActive)
   {
      g_srActive = false;
      g_srEnding = true;                               // on attend 2 s que les deals de sortie soient comptés
      g_srEndMs = now;
   }
   if(g_srEnding && now - g_srEndMs >= 2000) SeriesFinalize();
}

//+------------------------------------------------------------------+
//| Journal des filtres : temps de blocage par filtre                |
//+------------------------------------------------------------------+
double   g_fjSec[FB_N];
int      g_fjCnt[FB_N];
double   g_fjAny = 0;
bool     g_fjPrev[FB_N];
datetime g_fjStart[FB_N];
ulong    g_fjLastMs = 0;
string   g_fjEv[12];
int      g_fjEvN = 0;

void FjEvent(int i, datetime a, datetime b)
{
   if(g_fjEvN >= 12)
   {
      for(int k = 1; k < 12; k++) g_fjEv[k - 1] = g_fjEv[k];
      g_fjEvN = 11;
   }
   g_fjEv[g_fjEvN] = "🔴 " + FbName(i) + " · " + Hh(a) + " → " + Hh(b) + " (" + FmtDur((double)(b - a)) + ")";
   g_fjEvN++;
}

void FjSave()
{
   for(int i = 0; i < FB_N; i++)
   {
      GlobalVariableSet(GVN("fjs" + IntegerToString(i)), g_fjSec[i]);
      GlobalVariableSet(GVN("fjc" + IntegerToString(i)), g_fjCnt[i]);
   }
   GlobalVariableSet(GVN("fja"), g_fjAny);
}

void FjLoad()
{
   if(!GlobalVariableCheck(GVN("dk")) || (int)GlobalVariableGet(GVN("dk")) != DayKeyNow()) return;
   for(int i = 0; i < FB_N; i++)
   {
      if(GlobalVariableCheck(GVN("fjs" + IntegerToString(i)))) g_fjSec[i] = GlobalVariableGet(GVN("fjs" + IntegerToString(i)));
      if(GlobalVariableCheck(GVN("fjc" + IntegerToString(i)))) g_fjCnt[i] = (int)GlobalVariableGet(GVN("fjc" + IntegerToString(i)));
   }
   if(GlobalVariableCheck(GVN("fja"))) g_fjAny = GlobalVariableGet(GVN("fja"));
}

void FilterJournalTick()
{
   ulong now = GetTickCount64();
   double dt = (g_fjLastMs > 0) ? (double)(now - g_fjLastMs) / 1000.0 : 0.0;
   g_fjLastMs = now;
   if(dt > 5.0) dt = 1.0;                                   // coupure / lenteur : on ne compte pas le trou
   bool relevant = (g_enabled && f_marketOpen);             // le blocage ne « coûte » que si le robot est actif et le marché ouvert
   bool any = false;
   for(int i = 0; i < FB_N; i++)
   {
      bool blk = FbOn(i) && !FbOK(i);
      if(blk) { any = true; if(relevant) g_fjSec[i] += dt; }
      if(blk && !g_fjPrev[i]) { g_fjCnt[i]++; g_fjStart[i] = LocalNow(); }
      if(!blk && g_fjPrev[i]) FjEvent(i, g_fjStart[i], LocalNow());
      g_fjPrev[i] = blk;
   }
   if(any && relevant) g_fjAny += dt;
}

string FilterJournalText()
{
   string s = "🧾 JOURNAL DES FILTRES\n━━━━━━━━━━━━━━━━━━\n📅 AUJOURD'HUI (robot actif, marché ouvert)";
   bool none = true;
   for(int k = 0; k < FB_N; k++)
   {
      int i = FbOrder(k);
      if(g_fjSec[i] < 1 && g_fjCnt[i] == 0) continue;
      none = false;
      s += "\n🔴 " + FbName(i) + " · " + FmtDur(g_fjSec[i]) + " · " + IntegerToString(g_fjCnt[i]) + "×";
   }
   if(none) s += "\n✅ Aucun blocage aujourd'hui";
   else     s += "\n⛔ Temps total sans nouvelle série possible : " + FmtDur(g_fjAny);
   if(g_fjEvN > 0)
   {
      s += "\n\n🕘 DERNIERS BLOCAGES";
      int from = (g_fjEvN > 8) ? g_fjEvN - 8 : 0;
      for(int e = g_fjEvN - 1; e >= from; e--) s += "\n" + g_fjEv[e];
   }
   return s;
}

//+------------------------------------------------------------------+
//| Passage à un nouveau jour                                        |
//+------------------------------------------------------------------+
// Drawdown du jour = recul de l'equity depuis son plus haut DE LA JOURNÉE (appelé à chaque tick)
void DayDDTrack()
{
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   if(g_dayPeakEq <= 0 || eq > g_dayPeakEq) PeakRaise(PK_DAY, g_dayPeakEq, eq);   // [ARCHI] voir PeakAfterFlow
   double dd = (g_dayPeakEq - eq) / g_dayPeakEq * 100.0;
   if(dd > g_dayMaxDD) g_dayMaxDD = dd;
}

void DayTick()
{
   DayDDTrack();
   int key = DayKeyNow();
   if(key == g_dayKey) return;
   for(int i = 0; i < FB_N; i++) { g_fjSec[i] = 0; g_fjCnt[i] = 0; }
   g_fjAny = 0;
   g_fjEvN = 0;                                           // les blocages d'hier ne sont pas conservés
   g_dayKey = key;
   g_dayNet = 0; g_daySeries = 0; g_dayWins = 0; g_dayMaxDD = 0;
   g_dayPeakEq = AccountInfoDouble(ACCOUNT_EQUITY);
   g_dayStartBal = AccountInfoDouble(ACCOUNT_BALANCE);
   g_eqN = 0;
   int h = FileOpen(EqFile(), FILE_WRITE | FILE_TXT | FILE_ANSI);     // nouveau fichier d'equity pour la nouvelle journée
   if(h != INVALID_HANDLE) FileClose(h);
   DaySave();
   FjSave();
}

//+------------------------------------------------------------------+
//| Rapport du jour (commande RAPPORT)                               |
//+------------------------------------------------------------------+
string TodayReportText(string title)
{
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   int nSer = g_daySeries, nWin = g_dayWins;
   double dayNet = g_dayNet, dayBase = g_dayStartBal;
   int ls = nSer - nWin;
   string s = title + "\n━━━━━━━━━━━━━━━━━━" +
              "\n💰 Gain du jour : " + Sgn(dayNet) + " " + Cur() + (dayBase > 0 ? " (" + Sgn(dayNet / dayBase * 100.0) + " % du solde de début de journée)" : "") +
              "\n📦 Séries : " + IntegerToString(nSer) + " · ✅ " + IntegerToString(nWin) + " · ❌ " + IntegerToString(ls) +
              (nSer > 0 ? " · réussite " + F0((double)nWin / nSer * 100.0) + " %" : "") +
              "\n📉 Pire drawdown du jour : " + F1(g_dayMaxDD) + " % " + Dot3(PDbl(P_DDTHR) > 0 ? g_dayMaxDD / PDbl(P_DDTHR) : 0) +
              "\n💼 Balance " + F2(bal) + " · Equity " + F2(AccountInfoDouble(ACCOUNT_EQUITY)) + " " + Cur() +
              "\n📈 " + EquitySpark(24);
   string fl = "";
   for(int k = 0; k < FB_N; k++)
   {
      int i = FbOrder(k);
      if(g_fjSec[i] >= 60) fl += "\n   🔴 " + FbName(i) + " · " + FmtDur(g_fjSec[i]) + " (" + IntegerToString(g_fjCnt[i]) + "×)";
   }
   s += "\n\n🛡️ FILTRES (temps de blocage)" + (fl != "" ? fl : "\n   ✅ Aucun blocage notable");
   if(g_sesGoals > 0) s += "\n\n🏆 Objectifs atteints : " + IntegerToString(g_sesGoals);
   return s;
}

//+------------------------------------------------------------------+
//| Init / tick 1 Hz                                                 |
//+------------------------------------------------------------------+
void JournalInit()
{
   ArrayInitialize(g_fjSec, 0);
   ArrayInitialize(g_fjCnt, 0);
   ArrayInitialize(g_fjPrev, false);
   DayLoad();
   FjLoad();
   EquityLoad();
   FileDelete("ScalpX\\inst_" + IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN)) + "_" + IntegerToString(MagicNumber) + "_" + _Symbol + ".txt", FILE_COMMON);   // fichier de l'ancien multi-instances
}

void JournalTick1Hz()
{
   static ulong lastSave = 0;
   ulong now = GetTickCount64();
   DayTick();
   EquitySample();
   FilterJournalTick();
   JournalTick();
   if(now - lastSave >= 60000) { lastSave = now; DaySave(); FjSave(); }
}
