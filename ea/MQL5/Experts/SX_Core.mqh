//+------------------------------------------------------------------+
//| SX_Core.mqh - ScalpX v4.5                                        |
//| Noyau : variables globales, registre de paramètres (tous les     |
//| réglages sont modifiables à chaud), persistance, presets.        |
//+------------------------------------------------------------------+
#define SX_VERSION   "6.2.0"
#define SX_PREFIX    "SX4_"
#define SX_MAXQ      40

//--- Types / groupes / identifiants de paramètres
enum ENUM_PTYPE  { PT_BOOL = 0, PT_INT = 1, PT_DBL = 2, PT_ENUM = 3 };
enum ENUM_PGROUP { G_SIGNAL = 0, G_GRID, G_TP, G_FILTERS, G_MTF, G_TIME, G_RISK, G_GOAL, G_EXEC, G_NOTIF, G_CHART, G_SMART, G_COUNT };

enum ENUM_PID
{
   // Signal & timeframe
   P_TF = 0, P_BAR, P_FAST, P_SLOW, P_ATRP,
   // Lots & grille
   P_LOT, P_TBM, P_LOTMULT, P_GRID, P_GRIDMULT, P_DYNGRID, P_GRIDATR, P_GRIDMIN, P_GRIDMAX, P_MAXPEND, P_MAXSPREAD, P_WAVG,
   // Take profit
   P_DYNTP, P_ATRMULT, P_TPFIXED, P_TPMIN, P_TPMAX,
   // Filtres marché
   P_F_ATR, P_ATRMIN, P_ATRMAX, P_F_EMA, P_F_VOL, P_VOLMIN, P_VOLMAX, P_VOLBARS,
   // Filtres multi-timeframe
   P_F_LO, P_LOTF, P_LOFAST, P_LOSLOW, P_LOBAR, P_F_HI, P_HITF, P_HIFAST, P_HISLOW, P_HIBAR, P_F_CAN, P_CANBODY,
   // Filtre horaire
   P_TZ, P_F_TIME, P_T_START, P_T_END, P_T_SERVER,
   // Risque
   P_DDTHR, P_DDSTOP, P_EMSL, P_EMSLPCT, P_SESSLIM, P_SESSPCT,
   // Objectifs
   P_EARLY, P_GOALMAX, P_PROFNOTIF,
   // [ARCHI] début : objectifs automatiques aux créneaux (P_SL_M00..P_SL_M45 = 4 masques consécutifs) et seuils 500 / 125
   P_SL_ON, P_SL_PCT, P_SL_WIN, P_SL_M00, P_SL_M15, P_SL_M30, P_SL_M45, P_SEUIL_ON, P_SEUIL_ARRET, P_SEUIL_REPRISE,
   // [ARCHI] fin
   // Exécution
   P_DEVIATION, P_RETRIES,
   // Notifications
   P_N_FILTERS, P_N_PROGRESS, P_N_ERRORS,
   // Graphique
   P_CH_STYLE, P_CH_EMA, P_CH_LEVELS, P_CH_PANEL,
   // Alertes, news, mode vacances
   P_N_WATCHDOG, P_N_MARKET,
   P_N_NEWS, P_NEWS_MIN, P_NEWS_IMP, P_NEWS_BLOCK, P_NEWS_BEFORE, P_NEWS_AFTER, P_VAC_RISK,
   P_COUNT
};

#define PF_INDI   1   // reconstruire les indicateurs
#define PF_CHART  2   // redessiner le graphique
#define PF_TP     4   // recalculer le TP

//--- Registre
string g_pKey[P_COUNT];
string g_pName[P_COUNT];
string g_pUnit[P_COUNT];
string g_pHelp[P_COUNT];
string g_pOpt[P_COUNT];
int    g_pGroup[P_COUNT];
int    g_pType[P_COUNT];
int    g_pFlag[P_COUNT];
double g_pMin[P_COUNT];
double g_pMax[P_COUNT];
double g_pStep[P_COUNT];
double g_pDef[P_COUNT];
double g_pVal[P_COUNT];
string g_groupName[G_COUNT];

ENUM_TIMEFRAMES g_tfList[6] = { PERIOD_M1, PERIOD_M5, PERIOD_M15, PERIOD_M30, PERIOD_H1, PERIOD_H4 };
string          g_tfName[6] = { "M1", "M5", "M15", "M30", "H1", "H4" };

//--- Etat du robot
bool     g_enabled        = false;   // robot démarré (entrées + grille autorisées)
bool     g_stoppedManually= false;   // arrêt manuel (pas de reprise auto)
bool     g_configOK       = false;   // paramètres validés par l'utilisateur
bool     g_closing        = false;   // fermeture groupée en cours
ulong    g_cooldownUntilMs= 0;       // pas d'entrée avant (ms)
ulong    g_nextTradeMs    = 0;       // backoff après échec d'ordre (ms)
int      g_failCount      = 0;       // échecs d'ordre consécutifs

//--- Indicateurs
int             g_hFast = INVALID_HANDLE;
int             g_hSlow = INVALID_HANDLE;
int             g_hATR  = INVALID_HANDLE;
int             g_hFastLo = INVALID_HANDLE, g_hSlowLo = INVALID_HANDLE;   // timeframe inférieur
int             g_hFastHi = INVALID_HANDLE, g_hSlowHi = INVALID_HANDLE;   // timeframe supérieur
int             g_hATRHi  = INVALID_HANDLE;                               // ATR du timeframe supérieur (corps de bougie)
ENUM_TIMEFRAMES g_loTF  = PERIOD_M1, g_hiTF = PERIOD_M15;
ENUM_TIMEFRAMES g_sigTF = PERIOD_M5;
ENUM_ORDER_TYPE_FILLING g_fillMkt  = ORDER_FILLING_IOC;
ENUM_ORDER_TYPE_FILLING g_fillPend = ORDER_FILLING_IOC;

//--- Instantané de l'état de trading (une seule passe par tick)
int      st_posCount   = 0;
double   st_posLots    = 0;
double   st_floating   = 0;
double   st_avg        = 0;      // prix moyen (pondéré ou simple selon P_WAVG)
int      st_posType    = -1;     // POSITION_TYPE_BUY / SELL, -1 = aucune
double   st_lowOpen    = 0;
double   st_highOpen   = 0;
int      st_pendCount  = 0;
double   st_lowPend    = 0;
double   st_highPend   = 0;
int      st_tpWrong    = 0;      // nb de positions dont le TP broker != TP cible
datetime st_firstTime  = 0;

//--- Take profit
double   g_tpPrice     = 0;
double   g_tpDistPts   = 0;
double   g_tpSig       = -1;
ulong    g_nextTpCheckMs = 0;

//--- Marché / filtres
double   mk_fast = 0, mk_slow = 0, mk_atrPts = 0, mk_bid = 0, mk_ask = 0, mk_spread = 0;
double   mk_volRatio = 0;
#define NFILTERS 6
bool     f_ok[NFILTERS]    = { true, true, true, true, true, true };   // 0 = ATR, 1 = Prix/EMA, 2 = Volume, 3 = EMA inférieur, 4 = EMA supérieur, 5 = bougie supérieure
bool     f_raw[NFILTERS]   = { true, true, true, true, true, true };
ulong    f_since[NFILTERS] = { 0, 0, 0, 0, 0, 0 };
ulong    f_lastNote[NFILTERS] = { 0, 0, 0, 0, 0, 0 };
double   mk_loF = 0, mk_loS = 0, mk_hiF = 0, mk_hiS = 0;   // EMA des timeframes inférieur / supérieur
int      mk_candleDir = 0;                                  // bougie sup. précédente : +1 haussière, -1 baissière, 0 neutre
double   mk_candleBody = 0, mk_candleMin = 0;               // corps et seuil (points)
bool     f_timeBlocked = false;
bool     f_marketOpen  = true;
//--- Tableau des filtres (SX_FilterBoard.mqh) : les 6 filtres de marché + Horaire + News
#define FB_N 8
int      g_faState  = -1;          // dernier état global annoncé : 1 = tous OK, 0 = bloqué, -1 = inconnu
ulong    g_faSince  = 0;
//--- Demandes d'images du bot (traitées par le timer : ShotTick)
string   g_shotReq  = "";
string   g_curveReq = "";
//--- Mode vacances, pause programmée, news
bool     g_vacation = false;          // mode vacances : aucune nouvelle série, plafond de risque réduit
datetime g_pauseUntil = 0;            // pause programmée (heure du fuseau)
bool     g_newsBlock = false;         // fenêtre de news : nouvelles séries bloquées
string   g_newsWhy = "";
ulong    g_lastTickMs = 0;
ulong    g_finishRetryMs = 0;         // anti-boucle après une fin d'objectif non confirmée
int      g_finishFails = 0;
string   g_blockReason = "";             // pourquoi le robot n'ouvre pas de nouvelle série

//--- Objectif / risque / session
bool     g_goalActive = false;
bool     g_goalDone   = false;
double   g_goalTarget = 0;
double   g_goalInit   = 0;
double   g_goalPeak   = 0;
double   g_goalMaxDD  = 0;
datetime g_goalStart  = 0;
bool     g_n25 = false, g_n50 = false, g_n75 = false;
double   g_peakEq     = 0;
double   g_dayPeakEq  = 0;           // plus haut d'equity DE LA JOURNÉE (drawdown du jour)
double   g_dayStartBal = 0;          // solde au début de la journée (base du % du rapport journalier)
bool     g_ddNotified = false;
// [ARCHI] début : plus hauts d'equity et dépôts. Un dépôt fait monter l'equity (nouveau plus haut) AVANT que le deal
// de dépôt ne soit traité ; sans correction, le dépôt était compté deux fois dans le plus haut (faux drawdown).
#define PK_ALL  0      // g_peakEq (drawdown global)
#define PK_DAY  1      // g_dayPeakEq (drawdown du jour)
#define PK_GOAL 2      // g_goalPeak (drawdown de l'objectif)
#define PK_WIN_MS 120000
double   g_pkPrev[3] = { 0, 0, 0 };   // plus haut AVANT la première hausse de la fenêtre
ulong    g_pkMs[3]   = { 0, 0, 0 };   // début de la fenêtre (ms)

// Nouveau plus haut : mémorise la valeur d'avant (une fois par fenêtre de 2 min)
void PeakRaise(int k, double &peak, double eq)
{
   if(eq <= peak && peak > 0) return;
   ulong now = GetTickCount64();
   if(g_pkMs[k] == 0 || now - g_pkMs[k] > PK_WIN_MS) { g_pkPrev[k] = peak; g_pkMs[k] = now; }
   peak = eq;
}

// Plus haut après un dépôt / retrait : si l'equity a déjà fait monter le plus haut (dépôt vu avant son deal),
// on repart de la valeur d'avant cette hausse ; sinon on décale simplement du montant.
double PeakAfterFlow(int k, double peak, double amt)
{
   if(amt > 0 && g_pkMs[k] > 0 && GetTickCount64() - g_pkMs[k] <= PK_WIN_MS)
   {
      g_pkMs[k] = 0;
      return MathMax(g_pkPrev[k] + amt, peak);
   }
   return peak + amt;
}
// [ARCHI] fin
double   g_sesStartBal= 0;
datetime g_sesStart   = 0;
int      g_sesWins = 0, g_sesLosses = 0, g_sesGoals = 0;
double   g_sesProfit  = 0;
double   g_profitAccum= 0;

//--- Presets
string   g_psNames[12];
int      g_psCount    = 0;

//--- Graphique / panneau
ulong    g_panelMs    = 0;
datetime g_lastEmaBar = 0;
string   g_atrIndName = "";
int      g_atrSubWin  = -1;
bool     g_chartStyled= false;
long     g_chOrig[24];
bool     g_btnCloseArmed = false;
ulong    g_btnCloseMs = 0;

//+------------------------------------------------------------------+
//| Utilitaires                                                      |
//+------------------------------------------------------------------+
void Log(string m) { Print("[ScalpX4] ", m); }
void Dbg(string m) { if(VerboseLogs) Print("[ScalpX4] ", m); }

string GVN(string suffix) { return SX_PREFIX + IntegerToString(MagicNumber) + "_" + suffix; }

string F0(double v) { return DoubleToString(v, 0); }
string F1(double v) { return DoubleToString(v, 1); }
string F2(double v) { return DoubleToString(v, 2); }
string Sgn(double v) { return (v >= 0 ? "+" : "") + DoubleToString(v, 2); }
string Cur() { return AccountInfoString(ACCOUNT_CURRENCY); }
string PX(double p) { return DoubleToString(p, _Digits); }

int Dec(double step)
{
   if(step >= 1.0) return 0;
   int d = 0;
   double s = step;
   while(d < 6 && MathAbs(s - MathRound(s)) > 1e-9) { s *= 10.0; d++; }
   return d;
}

int TfToIndex(ENUM_TIMEFRAMES tf)
{
   for(int i = 0; i < 6; i++) if(g_tfList[i] == tf) return i;
   return 1; // M5 par défaut
}

ENUM_TIMEFRAMES TfFromIndex(int i)
{
   if(i < 0) i = 0;
   if(i > 5) i = 5;
   return g_tfList[i];
}

//--- Accesseurs de paramètres
double PDbl(int id)  { return g_pVal[id]; }
int    PInt(int id)  { return (int)MathRound(g_pVal[id]); }
bool   PBool(int id) { return g_pVal[id] >= 0.5; }
int    BarShift()    { return PInt(P_BAR); }

//+------------------------------------------------------------------+
//| Enregistrement d'un paramètre                                    |
//+------------------------------------------------------------------+
void Reg(int id, string key, string name, int grp, int type, double def,
         double mn, double mx, double step, string unit, string help,
         string opt = "", int flag = 0)
{
   g_pKey[id] = key;   g_pName[id] = name;  g_pGroup[id] = grp;  g_pType[id] = type;
   g_pDef[id] = def;   g_pVal[id] = def;    g_pMin[id] = mn;     g_pMax[id] = mx;
   g_pStep[id] = step; g_pUnit[id] = unit;  g_pHelp[id] = help;  g_pOpt[id] = opt;
   g_pFlag[id] = flag;
}

// [ARCHI] début : créneaux d'objectif automatique. Réglage = 4 masques de 24 bits (un par minute : 00, 15, 30, 45) ;
// le bit h d'un masque = un créneau à l'heure h. L'input ObjectifAutoHeures ne sert que de valeur de départ.
int SlotNum(string s)
{
   StringTrimLeft(s); StringTrimRight(s);
   int n = StringLen(s);
   if(n == 0 || n > 2) return -1;
   for(int i = 0; i < n; i++)
   {
      ushort c = StringGetCharacter(s, i);
      if(c < '0' || c > '9') return -1;
   }
   return (int)StringToInteger(s);
}

// « 3,6,9:30,12:45 » → masques ; les éléments invalides (heure hors 0-23, minutes autres que 00/15/30/45) sont ignorés
void SlotsMasksFromText(const string txt, int &mk[])
{
   ArrayInitialize(mk, 0);
   string parts[];
   int n = StringSplit(txt, ',', parts);
   for(int i = 0; i < n; i++)
   {
      string hm[];
      int k = StringSplit(parts[i], ':', hm);
      if(k < 1 || k > 2) continue;
      int h = SlotNum(hm[0]);
      int m = (k == 2) ? SlotNum(hm[1]) : 0;
      if(h < 0 || h > 23) continue;
      if(m != 0 && m != 15 && m != 30 && m != 45) continue;
      mk[m / 15] |= (1 << h);
   }
}
// [ARCHI] fin

void InitParams()
{
   g_groupName[G_SIGNAL]  = "📐 Signal & timeframe";
   g_groupName[G_GRID]    = "🧱 Lots & grille";
   g_groupName[G_TP]      = "🎯 Take profit";
   g_groupName[G_FILTERS] = "🛡️ Filtres marché";
   g_groupName[G_MTF]     = "🔭 Filtres multi-timeframe";
   g_groupName[G_TIME]    = "⏰ Filtre horaire";
   g_groupName[G_RISK]    = "⚠️ Risque";
   g_groupName[G_GOAL]    = "🏆 Objectifs";
   g_groupName[G_EXEC]    = "⚡ Exécution";
   g_groupName[G_NOTIF]   = "🔔 Notifications";
   g_groupName[G_CHART]   = "🎨 Graphique";
   g_groupName[G_SMART]   = "🧠 Alertes & news";

   // --- Signal & timeframe
   Reg(P_TF, "tf", "Timeframe de trading", G_SIGNAL, PT_ENUM, TfToIndex(TradingTimeframe), 0, 5, 1, "",
       "Timeframe des EMA, de l'ATR et de tous les filtres. Indépendant du graphique affiché (mets le même pour voir ce que le robot voit).",
       "M1|M5|M15|M30|H1|H4", PF_INDI | PF_CHART);
   Reg(P_BAR, "bar", "Bougie utilisée", G_SIGNAL, PT_ENUM, SignalBar, 0, 1, 1, "",
       "En cours = réagit à chaque tick (comportement v3.3, signal pouvant osciller). Clôturée = signal stable, une seule décision par bougie.",
       "En cours|Clôturée", PF_CHART);
   Reg(P_FAST, "fast", "EMA rapide", G_SIGNAL, PT_INT, FastEMA, 2, 200, 1, "",
       "Période de l'EMA rapide. Fast > Slow = ACHAT, Fast < Slow = VENTE.", "", PF_INDI | PF_CHART);
   Reg(P_SLOW, "slow", "EMA lente", G_SIGNAL, PT_INT, SlowEMA, 3, 400, 1, "",
       "Période de l'EMA lente (doit être supérieure à l'EMA rapide).", "", PF_INDI | PF_CHART);
   Reg(P_ATRP, "atrp", "Période ATR", G_SIGNAL, PT_INT, ATRPeriod, 2, 100, 1, "",
       "Période de l'ATR (volatilité) utilisé pour le TP dynamique et le filtre de volatilité.", "", PF_INDI | PF_CHART | PF_TP);

   // --- Lots & grille
   Reg(P_LOT, "lot", "Taille du lot", G_GRID, PT_DBL, LotSize, 0.01, 10.0, 0.01, " lot",
       "Lot de la première position et des premiers ordres de grille.");
   Reg(P_TBM, "tbm", "Trades avant multiplication", G_GRID, PT_INT, TradesBeforeMultiply, 1, 50, 1, "",
       "Tous les N éléments (positions + ordres), le lot et la distance de grille sont multipliés.");
   Reg(P_LOTMULT, "lotmult", "Multiplicateur de lot", G_GRID, PT_DBL, LotMultiplier, 1.0, 3.0, 0.1, " x",
       "Coefficient appliqué au lot à chaque palier (1.0 = lot constant).");
   Reg(P_GRID, "grid", "Distance de grille", G_GRID, PT_INT, GridStep, 10, 2000, 10, " pts",
       "Distance entre deux ordres limites de la grille, en points.");
   Reg(P_GRIDMULT, "gridmult", "Multiplicateur de distance", G_GRID, PT_DBL, GridStepMultiplier, 1.0, 3.0, 0.05, " x",
       "Élargit la distance de grille à chaque palier (1.0 = distance constante).");
   Reg(P_DYNGRID, "dyngrid", "Grille dynamique (ATR)", G_GRID, PT_BOOL, UseDynamicGrid ? 1 : 0, 0, 1, 1, "",
       "Activé : distance de grille = ATR x multiplicateur, bornée entre min et max (la distance fixe est alors ignorée). Désactivé : distance fixe.");
   Reg(P_GRIDATR, "gridatr", "Multiplicateur ATR (grille)", G_GRID, PT_DBL, GridATRMultiplier, 0.1, 5.0, 0.1, " x",
       "Distance de grille = ATR (en points) x ce coefficient. Le multiplicateur de distance par palier s'applique ensuite.");
   Reg(P_GRIDMIN, "gridmin", "Grille dynamique : minimum", G_GRID, PT_INT, GridMinPoints, 10, 2000, 10, " pts",
       "Borne basse de la distance de grille dynamique.");
   Reg(P_GRIDMAX, "gridmax", "Grille dynamique : maximum", G_GRID, PT_INT, GridMaxPoints, 20, 5000, 10, " pts",
       "Borne haute de la distance de grille dynamique.");
   Reg(P_MAXPEND, "maxpend", "Ordres en attente max", G_GRID, PT_INT, MaxPendingOrders, 1, 50, 1, "",
       "Nombre maximum d'ordres limites de grille placés en même temps.");
   Reg(P_MAXSPREAD, "maxspread", "Spread maximum", G_GRID, PT_INT, MaxSpread, 5, 500, 5, " pts",
       "Au-delà de ce spread, aucune nouvelle position ni ordre de grille n'est placé (les sorties restent actives).");
   Reg(P_WAVG, "wavg", "Prix moyen pondéré", G_GRID, PT_BOOL, UseWeightedAverage ? 1 : 0, 0, 1, 1, "",
       "Activé : moyenne pondérée par les lots. Désactivé : moyenne simple des prix d'ouverture.", "", PF_TP);

   // --- Take profit
   Reg(P_DYNTP, "dyntp", "TP dynamique (ATR)", G_TP, PT_BOOL, UseDynamicTP ? 1 : 0, 0, 1, 1, "",
       "Activé : distance TP = ATR x multiplicateur, bornée entre TP min et TP max. Désactivé : TP fixe.", "", PF_TP);
   Reg(P_ATRMULT, "atrmult", "Multiplicateur ATR", G_TP, PT_DBL, ATRMultiplier, 0.1, 10.0, 0.1, " x",
       "Distance TP = ATR (en points) x ce coefficient.", "", PF_TP);
   Reg(P_TPFIXED, "tpfixed", "TP fixe", G_TP, PT_DBL, AvgTakeProfit, 10, 5000, 10, " pts",
       "Distance TP utilisée quand le TP dynamique est désactivé.", "", PF_TP);
   Reg(P_TPMIN, "tpmin", "TP minimum", G_TP, PT_INT, TPMinPoints, 5, 2000, 5, " pts",
       "Borne basse du TP dynamique.", "", PF_TP);
   Reg(P_TPMAX, "tpmax", "TP maximum", G_TP, PT_INT, TPMaxPoints, 10, 5000, 10, " pts",
       "Borne haute du TP dynamique. 100 = comportement réel de la v3.3 (la fermeture était plafonnée à 100 pts).", "", PF_TP);

   // --- Filtres marché
   Reg(P_F_ATR, "f_atr", "Filtre volatilité (ATR)", G_FILTERS, PT_BOOL, UseVolatilityFilter ? 1 : 0, 0, 1, 1, "",
       "Bloque les nouvelles séries si l'ATR est hors de la plage min-max.");
   Reg(P_ATRMIN, "atrmin", "ATR minimum", G_FILTERS, PT_DBL, MinATRForVolatility, 10, 5000, 10, " pts",
       "En dessous : marché trop calme (consolidation).");
   Reg(P_ATRMAX, "atrmax", "ATR maximum", G_FILTERS, PT_DBL, MaxATRForVolatility, 20, 10000, 10, " pts",
       "Au-dessus : marché trop volatil.");
   Reg(P_F_EMA, "f_ema", "Filtre Prix / EMA", G_FILTERS, PT_BOOL, UsePriceEMAFilter ? 1 : 0, 0, 1, 1, "",
       "Bloque si le prix est contre la tendance (sous l'EMA rapide en hausse, au-dessus en baisse) ou entre les deux EMA.");
   Reg(P_F_VOL, "f_vol", "Filtre de volume", G_FILTERS, PT_BOOL, VolumeFilterEnabledAtStart ? 1 : 0, 0, 1, 1, "",
       "Bloque si le volume de la dernière bougie clôturée est hors de la plage (en % de la moyenne).");
   Reg(P_VOLMIN, "volmin", "Volume minimum", G_FILTERS, PT_DBL, VolumeMinRatio, 0.1, 1.5, 0.05, " x moy.",
       "Ratio minimum volume / moyenne.");
   Reg(P_VOLMAX, "volmax", "Volume maximum", G_FILTERS, PT_DBL, VolumeMaxRatio, 1.2, 10.0, 0.1, " x moy.",
       "Ratio maximum volume / moyenne.");
   Reg(P_VOLBARS, "volbars", "Bougies pour la moyenne", G_FILTERS, PT_INT, VolumeBars, 5, 100, 5, "",
       "Nombre de bougies clôturées utilisées pour la moyenne du volume.");

   // --- Filtres multi-timeframe
   Reg(P_F_LO, "f_lo", "Filtre EMA timeframe inférieur", G_MTF, PT_BOOL, UseLowerTFFilter ? 1 : 0, 0, 1, 1, "",
       "Bloque une nouvelle série si les EMA du timeframe inférieur vont dans le sens opposé au signal (détecte un retournement plus tôt).");
   Reg(P_LOTF, "lotf", "Timeframe inférieur", G_MTF, PT_ENUM, TfToIndex(LowerTimeframe), 0, 5, 1, "",
       "Timeframe du filtre inférieur (M1 par défaut pour un robot en M5).", "M1|M5|M15|M30|H1|H4", PF_INDI);
   Reg(P_LOFAST, "lofast", "Inférieur : EMA rapide", G_MTF, PT_INT, LowerFastEMA, 2, 200, 1, "",
       "Période de l'EMA rapide du timeframe inférieur.", "", PF_INDI);
   Reg(P_LOSLOW, "loslow", "Inférieur : EMA lente", G_MTF, PT_INT, LowerSlowEMA, 3, 400, 1, "",
       "Période de l'EMA lente du timeframe inférieur (supérieure à la rapide).", "", PF_INDI);
   Reg(P_LOBAR, "lobar", "Inférieur : bougie utilisée", G_MTF, PT_ENUM, LowerSignalBar, 0, 1, 1, "",
       "En cours = réagit plus vite (plus de bruit). Clôturée = plus stable.", "En cours|Clôturée");
   Reg(P_F_HI, "f_hi", "Filtre EMA timeframe supérieur", G_MTF, PT_BOOL, UseUpperTFFilter ? 1 : 0, 0, 1, 1, "",
       "Bloque une nouvelle série si les EMA du timeframe supérieur vont dans le sens opposé au signal (tendance de fond).");
   Reg(P_HITF, "hitf", "Timeframe supérieur", G_MTF, PT_ENUM, TfToIndex(UpperTimeframe), 0, 5, 1, "",
       "Timeframe du filtre supérieur et de la bougie précédente (M15 par défaut pour un robot en M5).", "M1|M5|M15|M30|H1|H4", PF_INDI);
   Reg(P_HIFAST, "hifast", "Supérieur : EMA rapide", G_MTF, PT_INT, UpperFastEMA, 2, 200, 1, "",
       "Période de l'EMA rapide du timeframe supérieur.", "", PF_INDI);
   Reg(P_HISLOW, "hislow", "Supérieur : EMA lente", G_MTF, PT_INT, UpperSlowEMA, 3, 400, 1, "",
       "Période de l'EMA lente du timeframe supérieur (supérieure à la rapide).", "", PF_INDI);
   Reg(P_HIBAR, "hibar", "Supérieur : bougie utilisée", G_MTF, PT_ENUM, UpperSignalBar, 0, 1, 1, "",
       "Clôturée recommandé : signal de tendance stable.", "En cours|Clôturée");
   Reg(P_F_CAN, "f_can", "Filtre bougie précédente (sup.)", G_MTF, PT_BOOL, UseUpperCandleFilter ? 1 : 0, 0, 1, 1, "",
       "Bloque si la dernière bougie clôturée du timeframe supérieur est dans le sens opposé au signal. Une bougie au corps trop petit est ignorée (neutre).");
   Reg(P_CANBODY, "canbody", "Corps minimum de la bougie", G_MTF, PT_DBL, UpperCandleMinBody, 0.0, 3.0, 0.05, " x ATR",
       "Corps minimum (|clôture - ouverture|) en multiple de l'ATR du timeframe supérieur. En dessous : bougie neutre, le filtre ne bloque pas.");

   // --- Fuseau horaire : toutes les heures affichées / utilisées par le robot
   Reg(P_TZ, "tz", "Fuseau horaire", G_TIME, PT_ENUM, 0, 0, 4, 1, "",
       "Toutes les heures (panneau, alertes, filtre horaire, mode silencieux, news, rapport) sont exprimées dans ce fuseau, avec heure d'été automatique. Indépendant du fuseau du PC / VPS.",
       "France (Paris)|Londres|UTC|New York|Heure du PC");

   // --- Filtre horaire
   Reg(P_F_TIME, "f_time", "Filtre horaire", G_TIME, PT_BOOL, TradingBlockEnabledAtStart ? 1 : 0, 0, 1, 1, "",
       "Bloque les NOUVELLES séries pendant la plage horaire (la grille et les sorties continuent).");
   Reg(P_T_START, "t_start", "Début du blocage", G_TIME, PT_INT, TradingBlockStartHour, 0, 23, 1, " h",
       "Heure de début (incluse).");
   Reg(P_T_END, "t_end", "Fin du blocage", G_TIME, PT_INT, TradingBlockEndHour, 0, 23, 1, " h",
       "Heure de fin (exclue). Si fin < début, la plage passe minuit.");
   Reg(P_T_SERVER, "t_server", "Heure du serveur", G_TIME, PT_BOOL, TimeBlockUseServer ? 1 : 0, 0, 1, 1, "",
       "Activé : heure du serveur broker. Désactivé : heure du fuseau choisi ci-dessus.");

   // --- Risque
   Reg(P_DDTHR, "ddthr", "Seuil de drawdown", G_RISK, PT_DBL, DrawdownThreshold, 1, 95, 1, " %",
       "Alerte quand l'equity recule de ce pourcentage depuis son plus haut.");
   Reg(P_DDSTOP, "ddstop", "Arrêt auto sur drawdown", G_RISK, PT_BOOL, AutoStopOnDrawdown ? 1 : 0, 0, 1, 1, "",
       "Met le robot en pause et annule les ordres en attente quand le seuil est atteint.");
   Reg(P_EMSL, "emsl", "Stop loss d'urgence", G_RISK, PT_BOOL, UseEmergencyStopLoss ? 1 : 0, 0, 1, 1, "",
       "Ferme tout si l'equity passe sous la balance initiale de l'objectif moins ce pourcentage.");
   Reg(P_EMSLPCT, "emslpct", "SL d'urgence", G_RISK, PT_DBL, EmergencyStopLossPct, 1, 90, 1, " %",
       "Perte maximale tolérée par objectif, en % de la balance initiale.");
   Reg(P_SESSLIM, "sesslim", "Limite de perte session", G_RISK, PT_BOOL, UseSessionLossLimit ? 1 : 0, 0, 1, 1, "",
       "Met le robot en pause si la perte de la session dépasse la limite.");
   Reg(P_SESSPCT, "sesspct", "Perte session max", G_RISK, PT_DBL, MaxSessionLossPct, 1, 90, 1, " %",
       "Perte maximale de la session, en % de la balance de départ.");

   // --- Objectifs
   Reg(P_EARLY, "early", "Clôture anticipée", G_GOAL, PT_BOOL, UseEarlyGoalClose ? 1 : 0, 0, 1, 1, "",
       "Ferme tout dès que profit réalisé + flottant atteint l'objectif, sans attendre le TP.");
   Reg(P_GOALMAX, "goalmax", "Plafond d'objectif (montant)", G_GOAL, PT_DBL, GoalMaxAmount, 1, 100000, 5, "",
       "Montant maximum accepté pour un objectif exprimé en montant (garde-fou).");
   Reg(P_PROFNOTIF, "profnotif", "Alerte profit cumulé", G_GOAL, PT_DBL, ProfitNotifyAmount, 0, 10000, 1, "",
       "Notifie à chaque fois que le profit cumulé des trades fermés atteint ce montant (0 = désactivé).");

   // [ARCHI] début : objectifs automatiques aux créneaux et seuils 500 / 125 (valeurs de départ = inputs, modifiables à chaud)
   int slotMk[4];
   SlotsMasksFromText(ObjectifAutoHeures, slotMk);
   Reg(P_SL_ON, "sl_on", "Objectif auto aux créneaux", G_GOAL, PT_BOOL, ObjectifAutoCreneaux ? 1 : 0, 0, 1, 1, "",
       "Activé : un objectif est lancé automatiquement à chaque créneau libre (lundi à vendredi). Désactivé : aucun lancement automatique, objectifs manuels seulement.");
   Reg(P_SL_PCT, "sl_pct", "Objectif auto : gain", G_GOAL, PT_DBL, ObjectifAutoPct, 0.1, 20.0, 0.1, " %",
       "Gain visé à chaque créneau, en % du solde au moment du lancement.");
   Reg(P_SL_WIN, "sl_win", "Fenêtre de lancement", G_GOAL, PT_INT, MathMax(5, MathMin(15, ObjectifAutoFenetreMin)), 5, 15, 5, " min",
       "Délai pendant lequel un créneau peut encore être lancé après son heure (5, 10 ou 15 min). Passé ce délai, le créneau est perdu : il n'est jamais rattrapé.");
   Reg(P_SL_M00, "sl_m00", "Créneaux à l'heure pile (:00)", G_GOAL, PT_INT, slotMk[0], 0, 16777215, 1, "",
       "Heures des créneaux à :00 (un bit par heure, 0 à 23). Se règle avec l'écran Créneaux du bot.");
   Reg(P_SL_M15, "sl_m15", "Créneaux à :15", G_GOAL, PT_INT, slotMk[1], 0, 16777215, 1, "",
       "Heures des créneaux à :15 (un bit par heure, 0 à 23). Se règle avec l'écran Créneaux du bot.");
   Reg(P_SL_M30, "sl_m30", "Créneaux à :30", G_GOAL, PT_INT, slotMk[2], 0, 16777215, 1, "",
       "Heures des créneaux à :30 (un bit par heure, 0 à 23). Se règle avec l'écran Créneaux du bot.");
   Reg(P_SL_M45, "sl_m45", "Créneaux à :45", G_GOAL, PT_INT, slotMk[3], 0, 16777215, 1, "",
       "Heures des créneaux à :45 (un bit par heure, 0 à 23). Se règle avec l'écran Créneaux du bot.");
   Reg(P_SEUIL_ON, "seuil_on", "Clôture anticipée au seuil", G_GOAL, PT_BOOL, ActiverSeuil500 ? 1 : 0, 0, 1, 1, "",
       "Activé : dès que solde + flottant de l'EA atteint le seuil d'arrêt, tout est fermé et le compte attend un retrait.");
   Reg(P_SEUIL_ARRET, "seuil_arret", "Seuil d'arrêt", G_GOAL, PT_DBL, SeuilArret, 10, 1000000, 10, "",
       "Solde (devise du compte) à partir duquel le robot ferme tout et s'arrête en attendant un retrait. Doit rester supérieur au seuil de reprise.");
   Reg(P_SEUIL_REPRISE, "seuil_reprise", "Seuil de reprise", G_GOAL, PT_DBL, SeuilReprise, 0, 999990, 5, "",
       "Solde minimum (devise du compte) pour trader et pour reprendre après un retrait. Doit rester inférieur au seuil d'arrêt.");
   // [ARCHI] fin

   // --- Exécution
   Reg(P_DEVIATION, "dev", "Déviation max", G_EXEC, PT_INT, Deviation, 0, 500, 5, " pts",
       "Écart de prix toléré à l'exécution des ordres au marché.");
   Reg(P_RETRIES, "retries", "Nouvelles tentatives", G_EXEC, PT_INT, MaxRetries, 0, 5, 1, "",
       "Nombre de nouvelles tentatives immédiates sur requote / prix changé / timeout.");

   // --- Notifications
   Reg(P_N_FILTERS, "n_filters", "Alertes globales des filtres", G_NOTIF, PT_BOOL, NotifyFilters ? 1 : 0, 0, 1, 1, "",
       "Une seule alerte quand les filtres bloquent / quand tout repasse au vert. Le détail est dans le bot (🧭 Filtres).");
   Reg(P_N_PROGRESS, "n_progress", "Progression de l'objectif", G_NOTIF, PT_BOOL, NotifyProgress ? 1 : 0, 0, 1, 1, "",
       "Messages à 25 %, 50 % et 75 % de l'objectif.");
   Reg(P_N_ERRORS, "n_errors", "Erreurs d'ordres", G_NOTIF, PT_BOOL, NotifyErrors ? 1 : 0, 0, 1, 1, "",
       "Message en cas d'échecs d'ordres répétés.");

   // --- Graphique
   Reg(P_CH_STYLE, "ch_style", "Style moderne du graphique", G_CHART, PT_BOOL, ChartModernStyle ? 1 : 0, 0, 1, 1, "",
       "Thème sombre, bougies nettes, sans grille ni volumes. Le style d'origine est restauré au retrait du robot.", "", PF_CHART);
   Reg(P_CH_EMA, "ch_ema", "Afficher les EMA", G_CHART, PT_BOOL, ChartShowEMA ? 1 : 0, 0, 1, 1, "",
       "Trace les deux EMA du robot directement sur le graphique.", "", PF_CHART);
   Reg(P_CH_LEVELS, "ch_levels", "Afficher TP / prix moyen / grille", G_CHART, PT_BOOL, ChartShowLevels ? 1 : 0, 0, 1, 1, "",
       "Lignes du prix moyen, du TP et des prochains niveaux de grille (remplace les lignes d'ordres natives).", "", PF_CHART);
   Reg(P_CH_PANEL, "ch_panel", "Afficher le panneau", G_CHART, PT_BOOL, ChartShowPanel ? 1 : 0, 0, 1, 1, "",
       "Panneau d'informations dynamique sur le graphique.", "", PF_CHART);

   // --- Alertes, news, mode vacances
   Reg(P_N_WATCHDOG, "n_watchdog", "Surveillance (watchdog)", G_SMART, PT_BOOL, 1, 0, 1, 1, "",
       "Alerte en cas de perte de connexion au broker, d'AutoTrading coupé, de ticks absents ou de marge faible.");
   Reg(P_N_MARKET, "n_market", "Alertes spread / swap", G_SMART, PT_BOOL, 1, 0, 1, 1, "",
       "Alerte si le spread reste trop large et avant le rollover (swap) quand une série est ouverte.");
   Reg(P_N_NEWS, "n_news", "Alerte news économiques", G_SMART, PT_BOOL, 1, 0, 1, 1, "",
       "Prévient avant une annonce économique (calendrier MT5) concernant les devises du symbole.");
   Reg(P_NEWS_MIN, "news_min", "News : préavis", G_SMART, PT_INT, 30, 5, 180, 5, " min", "Délai d'avance de l'alerte.");
   Reg(P_NEWS_IMP, "news_imp", "News : importance", G_FILTERS, PT_ENUM, 0, 0, 1, 1, "",
       "Haute = annonces majeures seulement. Moyenne+Haute = plus d'annonces (alertes et filtre News).", "Haute|Moyenne + Haute");
   Reg(P_NEWS_BLOCK, "news_block", "Filtre News (pas d'entrée autour des annonces)", G_FILTERS, PT_BOOL, NewsPauseEnabledAtStart ? 1 : 0, 0, 1, 1, "",   // [ARCHI] activé par défaut
       "Filtre News : aucune nouvelle série autour d'une annonce du calendrier MT5 (la grille et les sorties continuent). Désactivé = le robot trade pendant les news.");
   Reg(P_NEWS_BEFORE, "news_before", "Filtre News : blocage avant l'annonce", G_FILTERS, PT_INT, 10, 0, 120, 5, " min",
       "Les nouvelles séries sont bloquées pendant ce délai AVANT l'annonce.");
   Reg(P_NEWS_AFTER, "news_after", "Filtre News : blocage après l'annonce", G_FILTERS, PT_INT, 15, 0, 180, 5, " min",
       "Les nouvelles séries restent bloquées pendant ce délai APRÈS l'annonce.");
   Reg(P_VAC_RISK, "vac_risk", "Mode vacances : plafond de risque", G_RISK, PT_DBL, 5.0, 0.5, 50.0, 0.5, " %",
       "En mode vacances, toutes les positions sont fermées si la perte flottante dépasse ce % de la balance.");
}

//+------------------------------------------------------------------+
//| Texte d'une valeur de paramètre                                  |
//+------------------------------------------------------------------+
string OptAt(int id, int idx)
{
   string parts[];
   int n = StringSplit(g_pOpt[id], '|', parts);
   if(idx < 0 || idx >= n) return IntegerToString(idx);
   return parts[idx];
}

string PValStr(int id)
{
   double v = g_pVal[id];
   switch(g_pType[id])
   {
      case PT_BOOL: return (v >= 0.5) ? "✅ Activé" : "❌ Désactivé";
      case PT_INT:  return IntegerToString((int)MathRound(v)) + g_pUnit[id];
      case PT_DBL:  return DoubleToString(v, Dec(g_pStep[id])) + g_pUnit[id];
      case PT_ENUM: return OptAt(id, (int)MathRound(v));
   }
   return "";
}

string PValShort(int id)   // version courte pour les boutons
{
   double v = g_pVal[id];
   switch(g_pType[id])
   {
      case PT_BOOL: return (v >= 0.5) ? "✅" : "❌";
      case PT_INT:  return IntegerToString((int)MathRound(v)) + g_pUnit[id];
      case PT_DBL:  return DoubleToString(v, Dec(g_pStep[id])) + g_pUnit[id];
      case PT_ENUM: return OptAt(id, (int)MathRound(v));
   }
   return "";
}

int FindParamByKey(string key)
{
   StringToLower(key);
   for(int i = 0; i < P_COUNT; i++) if(g_pKey[i] == key) return i;
   return -1;
}

//+------------------------------------------------------------------+
//| Validation / application d'un paramètre                          |
//+------------------------------------------------------------------+
double NormVolume(double v)
{
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0) step = 0.01;
   if(v < vmin) v = vmin;
   if(v > vmax) v = vmax;
   v = MathFloor(v / step + 1e-7) * step;       // correction de l'erreur flottante (0.07 -> 0.06)
   if(v < vmin) v = vmin;
   return NormalizeDouble(v, Dec(step));
}

bool ValidateParam(int id, double &v, string &err)
{
   err = "";
   if(g_pType[id] == PT_BOOL) { v = (v >= 0.5) ? 1.0 : 0.0; return true; }
   if(v < g_pMin[id] || v > g_pMax[id])
   {
      err = "Valeur hors limites (" + DoubleToString(g_pMin[id], Dec(g_pStep[id])) + " à " +
            DoubleToString(g_pMax[id], Dec(g_pStep[id])) + ")";
      return false;
   }
   if(g_pType[id] == PT_INT || g_pType[id] == PT_ENUM) v = MathRound(v);

   switch(id)
   {
      case P_LOT:
      {
         double nv = NormVolume(v);
         if(nv < SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN)) { err = "Lot inférieur au minimum du broker"; return false; }
         v = nv;
         break;
      }
      case P_FAST: if(v >= g_pVal[P_SLOW]) { err = "L'EMA rapide doit être inférieure à l'EMA lente (" + IntegerToString(PInt(P_SLOW)) + ")"; return false; } break;
      case P_LOFAST: if(v >= g_pVal[P_LOSLOW]) { err = "EMA rapide (inf.) doit être inférieure à l'EMA lente (" + IntegerToString(PInt(P_LOSLOW)) + ")"; return false; } break;
      case P_LOSLOW: if(v <= g_pVal[P_LOFAST]) { err = "EMA lente (inf.) doit être supérieure à l'EMA rapide (" + IntegerToString(PInt(P_LOFAST)) + ")"; return false; } break;
      case P_HIFAST: if(v >= g_pVal[P_HISLOW]) { err = "EMA rapide (sup.) doit être inférieure à l'EMA lente (" + IntegerToString(PInt(P_HISLOW)) + ")"; return false; } break;
      case P_HISLOW: if(v <= g_pVal[P_HIFAST]) { err = "EMA lente (sup.) doit être supérieure à l'EMA rapide (" + IntegerToString(PInt(P_HIFAST)) + ")"; return false; } break;
      case P_SLOW: if(v <= g_pVal[P_FAST]) { err = "L'EMA lente doit être supérieure à l'EMA rapide (" + IntegerToString(PInt(P_FAST)) + ")"; return false; } break;
      case P_ATRMIN: if(v >= g_pVal[P_ATRMAX]) { err = "ATR min doit être inférieur à ATR max (" + F0(g_pVal[P_ATRMAX]) + ")"; return false; } break;
      case P_ATRMAX: if(v <= g_pVal[P_ATRMIN]) { err = "ATR max doit être supérieur à ATR min (" + F0(g_pVal[P_ATRMIN]) + ")"; return false; } break;
      case P_VOLMIN: if(v >= g_pVal[P_VOLMAX]) { err = "Volume min doit être inférieur à volume max"; return false; } break;
      case P_VOLMAX: if(v <= g_pVal[P_VOLMIN]) { err = "Volume max doit être supérieur à volume min"; return false; } break;
      case P_TPMIN:  if(v > g_pVal[P_TPMAX]) { err = "TP min doit être inférieur ou égal à TP max (" + IntegerToString(PInt(P_TPMAX)) + ")"; return false; } break;
      case P_TPMAX:  if(v < g_pVal[P_TPMIN]) { err = "TP max doit être supérieur ou égal à TP min (" + IntegerToString(PInt(P_TPMIN)) + ")"; return false; } break;
      case P_GRIDMIN: if(v >= g_pVal[P_GRIDMAX]) { err = "Grille min doit être inférieure à grille max (" + IntegerToString(PInt(P_GRIDMAX)) + ")"; return false; } break;
      case P_GRIDMAX: if(v <= g_pVal[P_GRIDMIN]) { err = "Grille max doit être supérieure à grille min (" + IntegerToString(PInt(P_GRIDMIN)) + ")"; return false; } break;
      // [ARCHI] début
      case P_SEUIL_ARRET: if(v <= g_pVal[P_SEUIL_REPRISE]) { err = "Le seuil d'arrêt doit être supérieur au seuil de reprise (" + F0(g_pVal[P_SEUIL_REPRISE]) + ")"; return false; } break;
      case P_SEUIL_REPRISE: if(v >= g_pVal[P_SEUIL_ARRET]) { err = "Le seuil de reprise doit être inférieur au seuil d'arrêt (" + F0(g_pVal[P_SEUIL_ARRET]) + ")"; return false; } break;
      // [ARCHI] fin
      case P_GRID:
      {
         long sl = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
         if(v < (double)sl) { err = "Distance inférieure au niveau de stops du broker (" + IntegerToString((int)sl) + " pts)"; return false; }
         break;
      }
   }
   return true;
}

// Effets de bord après modification
void SlotsRefresh();   // [ARCHI] défini dans SX_Archi.mqh : relit les masques de créneaux

void ApplyParamEffects(int id)
{
   int fl = g_pFlag[id];
   if((fl & PF_INDI) != 0) { RebuildIndicators(); }
   if((fl & PF_TP) != 0)   { g_tpSig = -1; }
   if((fl & PF_CHART) != 0 || id == P_CH_STYLE) { ChartRefreshAll(true); }
   if(id == P_TF) { g_lastEmaBar = 0; }
   if(id >= P_SL_M00 && id <= P_SL_M45) { SlotsRefresh(); }   // [ARCHI] créneaux modifiés
}

void SaveParams();

bool SetParam(int id, double v, string &err)
{
   if(!ValidateParam(id, v, err)) return false;
   g_pVal[id] = v;
   ApplyParamEffects(id);
   SaveParams();
   return true;
}

//+------------------------------------------------------------------+
//| Persistance (fichiers dans MQL5\Files)                           |
//+------------------------------------------------------------------+
string CfgName() { return "ScalpX4_" + IntegerToString(MagicNumber) + "_params.cfg"; }

bool SaveParamsTo(string fname)
{
   int h = FileOpen(fname, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE) return false;
   for(int i = 0; i < P_COUNT; i++)
      FileWriteString(h, g_pKey[i] + "=" + DoubleToString(g_pVal[i], 8) + "\r\n");
   FileClose(h);
   return true;
}

void SaveParams() { SaveParamsTo(CfgName()); }

int LoadParamsFrom(string fname)
{
   int h = FileOpen(fname, FILE_READ | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE) return -1;
   int n = 0;
   while(!FileIsEnding(h))
   {
      string line = FileReadString(h);
      int p = StringFind(line, "=");
      if(p <= 0) continue;
      string key = StringSubstr(line, 0, p);
      double val = StringToDouble(StringSubstr(line, p + 1));
      int id = FindParamByKey(key);
      if(id < 0) continue;
      if(val < g_pMin[id]) val = g_pMin[id];
      if(val > g_pMax[id]) val = g_pMax[id];
      g_pVal[id] = val;
      n++;
   }
   FileClose(h);
   return n;
}

void ResetParamsToDefaults()
{
   for(int i = 0; i < P_COUNT; i++) g_pVal[i] = g_pDef[i];
   SlotsRefresh();   // [ARCHI]
   SaveParams();
}

//+------------------------------------------------------------------+
//| Presets                                                          |
//+------------------------------------------------------------------+
string PresetPrefix() { return "ScalpX4_" + IntegerToString(MagicNumber) + "_preset_"; }

string SanitizeName(string s)
{
   string out = "";
   for(int i = 0; i < StringLen(s) && StringLen(out) < 20; i++)
   {
      ushort c = StringGetCharacter(s, i);
      if((c >= '0' && c <= '9') || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '_' || c == '-')
         out += ShortToString(c);
      else if(c == ' ')
         out += "_";
   }
   return out;
}

bool PresetSave(string name)
{
   name = SanitizeName(name);
   if(name == "") return false;
   return SaveParamsTo(PresetPrefix() + name + ".cfg");
}

void PresetList()
{
   g_psCount = 0;
   string fn;
   long h = FileFindFirst(PresetPrefix() + "*.cfg", fn);
   if(h == INVALID_HANDLE) return;
   do
   {
      if(g_psCount >= 12) break;
      string nm = StringSubstr(fn, StringLen(PresetPrefix()));
      nm = StringSubstr(nm, 0, StringLen(nm) - 4);
      g_psNames[g_psCount++] = nm;
   }
   while(FileFindNext(h, fn));
   FileFindClose(h);
}

bool PresetLoad(string name)
{
   int n = LoadParamsFrom(PresetPrefix() + name + ".cfg");
   if(n <= 0) return false;
   RebuildIndicators();
   g_tpSig = -1;
   ChartRefreshAll(true);
   SlotsRefresh();   // [ARCHI]
   SaveParams();
   return true;
}

bool PresetDelete(string name)
{
   return FileDelete(PresetPrefix() + name + ".cfg");
}
//+------------------------------------------------------------------+

//+------------------------------------------------------------------+
//| Fuseau horaire : heure « locale » calculée depuis l'UTC du       |
//| terminal (indépendante du fuseau du PC / VPS), heure d'été auto  |
//+------------------------------------------------------------------+
datetime TzMake(int y, int m, int d, int h)
{
   MqlDateTime t;
   t.year = y; t.mon = m; t.day = d; t.hour = h; t.min = 0; t.sec = 0;
   return StructToTime(t);
}

int TzDow(int y, int m, int d)
{
   MqlDateTime t;
   TimeToStruct(TzMake(y, m, d, 0), t);
   return t.day_of_week;
}

int TzLastSunday(int y, int m)
{
   int d = 31;
   while(TzDow(y, m, d) != 0) d--;
   return d;
}

int TzNthSunday(int y, int m, int n)
{
   int d = 1;
   while(TzDow(y, m, d) != 0) d++;
   return d + 7 * (n - 1);
}

// Heure d'été européenne : du dernier dimanche de mars 01:00 UTC au dernier dimanche d'octobre 01:00 UTC
bool TzEuDst(datetime utc)
{
   MqlDateTime t;
   TimeToStruct(utc, t);
   return (utc >= TzMake(t.year, 3, TzLastSunday(t.year, 3), 1) && utc < TzMake(t.year, 10, TzLastSunday(t.year, 10), 1));
}

// Heure d'été américaine : 2e dimanche de mars 07:00 UTC au 1er dimanche de novembre 06:00 UTC (New York)
bool TzUsDst(datetime utc)
{
   MqlDateTime t;
   TimeToStruct(utc, t);
   return (utc >= TzMake(t.year, 3, TzNthSunday(t.year, 3, 2), 7) && utc < TzMake(t.year, 11, TzNthSunday(t.year, 11, 1), 6));
}

datetime TzFromUtc(datetime utc)
{
   switch(PInt(P_TZ))
   {
      case 0:  return utc + 3600 + (TzEuDst(utc) ? 3600 : 0);        // France
      case 1:  return utc + (TzEuDst(utc) ? 3600 : 0);               // Londres
      case 2:  return utc;                                            // UTC
      case 3:  return utc - 18000 + (TzUsDst(utc) ? 3600 : 0);       // New York
   }
   return TimeLocal();                                                // heure du PC
}

// Remplace TimeLocal() partout dans le robot
datetime LocalNow() { return TzFromUtc(TimeGMT()); }

// Heure du serveur broker -> heure du fuseau (décalage serveur arrondi au quart d'heure)
datetime ZoneFromServer(datetime srv)
{
   long srvOff = (long)MathRound((double)(TimeTradeServer() - TimeGMT()) / 900.0) * 900;
   return TzFromUtc((datetime)((long)srv - srvOff));
}

string TzLabel()
{
   switch(PInt(P_TZ))
   {
      case 0:  return "heure de France";
      case 1:  return "heure de Londres";
      case 2:  return "UTC";
      case 3:  return "heure de New York";
   }
   return "heure du PC";
}
//+------------------------------------------------------------------+
