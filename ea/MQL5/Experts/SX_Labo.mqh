//+------------------------------------------------------------------+
//| SX_Labo.mqh - ScalpX 7.8.0                                       |
//| 7.8.0 : option ct=1 / ct=0 : mode continu / créneaux du scénario |
//| 7.7.0 : séries virtuelles réservées au backtest de l'optimiseur  |
//|   (en réel : mesure du slippage et de la commission seulement).  |
//|   Options par scénario : tb, ef/es/ap (EMA, ATR), am/tn/tx (TP), |
//|   an/ax/vn/vx/ea/cb (seuils des filtres), r=1 (filtres recalculés|
//|   pour le scénario), sm=<4 masques> (créneaux propres).          |
//| [ARCHI] LABO DES FILTRES : mesure seulement, AUCUN ordre réel.   |
//|                                                                  |
//| 1) Journal des blocages RÉELS : pendant chaque objectif, combien |
//|    de secondes chaque filtre a empêché l'ouverture d'une série   |
//|    (signal présent, aucune position), et s'il était le SEUL à    |
//|    bloquer. Envoyé au bot à la fin de l'objectif (LABO_REEL).    |
//|                                                                  |
//| 2) Séries VIRTUELLES (scénarios) jouées en parallèle sur les     |
//|    mêmes prix avec d'autres réglages de filtres : même signal,   |
//|    mêmes lots, même grille, même TP, mêmes créneaux, coupures,   |
//|    fenêtre d'entrée, objectif, clôture anticipée, SL d'urgence   |
//|    et seuil 500 ; spread réel, slippage (moyenne par heure) et   |
//|    commission mesurés sur les VRAIS ordres du compte ; stop-out  |
//|    simulé avec la marge du broker. Chaque scénario a son propre  |
//|    solde virtuel (cycles 500 et recharges à 125 comptés).        |
//|    Fin de chaque objectif virtuel : événement LABO_SERIE.        |
//|                                                                  |
//| Scénarios : 13 par défaut (réglages actuels, chaque filtre       |
//| inversé, aucun filtre) + scénarios perso écrits par le bot dans  |
//| Common\Files\labo_scenarios.txt :                                |
//|    actif=1                                                       |
//|    reset=<jeton>          (change → soldes virtuels remis à zéro)|
//|    p1|Nom|t0,t1,...,t10   t = « = » (comme le compte), « off »,  |
//|                           « on », « +N » / « -N » (activé, seuils |
//|                           assouplis / durcis de N %), « ~ »      |
//|                           (inverse du compte)                    |
//| État persistant : Common\Files\labo_<id>.dat (toutes les 60 s).  |
//| Rien ici ne modifie la logique de trading réelle.                |
//+------------------------------------------------------------------+
#define LB_NF        FB_N        // 11 filtres, même numérotation que le tableau des filtres
#define LB_MAXSC     40          // 7.7.0 : 20 → 40 (optimiseur : une catégorie entière par backtest)
#define LB_MAXPOS    60
#define LB_MAXPEND   40
#define LB_SCEN_FILE "labo_scenarios.txt"

#define LBM_SAME     0           // comme le compte réel
#define LBM_OFF      1           // désactivé
#define LBM_ON       2           // activé (seuils du compte ajustés de adj %)
#define LBM_TOGGLE   3           // l'inverse du compte réel

string g_lbCode[LB_NF] = { "atr", "ema", "vol", "lo", "hi", "can", "time", "news", "spread", "ext", "fresh" };

struct LbScen
{
   string   key;
   string   name;
   int      mode[LB_NF];
   int      adj[LB_NF];        // % : > 0 seuils assouplis, < 0 durcis
   double   gm;                // 7.3.0 : multiplicateur de l'écart de grille (1 = comme le compte)
   int      noise;             // 7.3.0 : signal récent, recroisements ignorés (-1 = comme le compte)
   int      extTf;             // 7.3.0 : extension mesurée sur 0 = comme le compte, 1 = TF du robot, 2 = M15, 3 = H1
   int      fbars;             // 7.4.0 : signal récent, nombre de bougies max (0 = comme le compte)
   double   opct;              // 7.5.0 : objectif en % du solde (0 = comme le compte)
   double   ga, gn, gx, lm;    // 7.6.0 : valeurs ABSOLUES de l'optimiseur (0 = comme le compte) : multiplicateur ATR de la grille,
   int      mp;                //         grille min / max (pts), multiplicateur de lot, ordres en attente max
   int      tb;                // 7.7.0 : trades avant multiplication (0 = comme le compte)
   int      ef, es, ap;        // 7.7.0 : périodes EMA rapide / lente et ATR du signal (0 = comme le compte)
   double   am, tn, tx;        // 7.7.0 : TP dynamique : multiplicateur ATR, minimum, maximum en pts (0 = comme le compte)
   double   an, ax, vn, vx;    // 7.7.0 : ATR min / max (pts), volume min / max (x moyenne) (0 = comme le compte)
   double   ea, cb;            // 7.7.0 : extension max (x ATR, 0 = comme le compte), corps de bougie (x ATR, < 0 = comme le compte)
   bool     raw;               // 7.7.0 : tous les filtres recalculés pour le scénario (sans l'antirebond du compte)
   bool     hasSm;             // 7.7.0 : créneaux propres au scénario (masques sm, même codage que sl_m00..sl_m45)
   int      sm[4];
   long     slotKey;           //         dernier créneau propre consommé
   int      ct;                // 7.8.0 : mode continu du scénario (-1 = comme le compte, 0 = créneaux, 1 = continu)
   datetime contArm;           //         bougie M5 pendant laquelle les filtres du scénario étaient OK (0 = pas armé)
   int      hF, hS, hA;        //         EMA rapide / lente et ATR propres (INVALID_HANDLE = ceux du robot)
   //--- compte virtuel
   double   bal;
   double   start;
   double   withdrawn;         // retiré aux cycles 500 (solde ramené au seuil de reprise)
   double   reloaded;          // rechargé après une perte sous le seuil de reprise
   int      cycles;
   int      reloads;
   //--- objectif virtuel
   bool     active;
   bool     cutPend;
   double   init;
   double   target;
   double   peak;
   double   maxDD;
   double   minML;             // niveau de marge minimum (%), 0 = jamais de position
   datetime t0;                // début (GMT)
   datetime tEntry;            // première série (GMT), 0 = aucune
   string   slot;
   int      maxPos;
   int      nSeries;
   //--- série en cours
   int      dir;               // +1 achat, -1 vente, 0 aucune
   int      nPos;
   int      nPend;
   double   pOp[LB_MAXPOS];
   double   pLot[LB_MAXPOS];
   double   qPx[LB_MAXPEND];
   double   qLot[LB_MAXPEND];
   double   sumL;
   double   sumPL;
   double   tp;
   double   tpSig;
   //--- filtres (1 Hz)
   bool     ok;
   string   why;
};

LbScen   g_lb[LB_MAXSC];
LbScen   g_lbTmp[LB_MAXSC];      // reconstruction de la liste (évite un gros tableau sur la pile)
int      g_lbN        = 0;
bool     g_lbOn       = false;   // labo en marche (jamais dans le Strategy Tester)
bool     g_lbWanted   = true;    // « actif= » du fichier des scénarios
string   g_lbReset    = "";      // jeton de remise à zéro appliqué
long     g_lbFileMod  = -1;      // date de modification du fichier des scénarios déjà lu
double   g_lbK        = 0;       // valeur monétaire d'un écart de prix de 1.0 pour 1 lot
double   g_lbSlip[25];           // slippage défavorable moyen (points) par heure (fuseau de l'EA), 24 = toutes heures
int      g_lbSlipN[25];
double   g_lbComm     = 0;       // commission moyenne par lot et par côté (montant positif)
int      g_lbCommN    = 0;
bool     g_lbDirty    = false;
datetime g_lbVolBar   = 0;
double   g_lbVolRatio = 0;
//--- journal des blocages réels (objectif réel en cours)
bool     g_lrOn       = false;
datetime g_lrT0       = 0;
datetime g_lrEntry    = 0;
string   g_lrSlot     = "";
double   g_lrInit     = 0;
int      g_lrWait     = 0;       // s : objectif actif, robot actif, aucune position
int      g_lrSig      = 0;       // s : dont signal présent
int      g_lrBlk      = 0;       // s : dont au moins un filtre bloquant
int      g_lrOther    = 0;       // s : signal présent, filtres OK mais autre blocage (spread de l'ordre, coupure, échec…)
int      g_lrSec[LB_NF];
int      g_lrSole[LB_NF];

string LbFile() { return FileId("labo", ".dat"); }

//--- 7.3.0 : options par scénario (écart de grille, respirations du signal récent, timeframe de l'extension)
int      g_lbCurNoise = -1;
int      g_lbCurExt   = 0;
int      g_lbCurBars  = 0;
int      g_lbExtSlow[2] = { INVALID_HANDLE, INVALID_HANDLE };   // EMA lente M15, H1
int      g_lbExtAtr[2]  = { INVALID_HANDLE, INVALID_HANDLE };   // ATR M15, H1

void LbOptsParse(LbScen &x, string o)
{
   x.gm = 1.0; x.noise = -1; x.extTf = 0; x.fbars = 0; x.opct = 0;
   x.ga = 0; x.gn = 0; x.gx = 0; x.lm = 0; x.mp = 0;
   x.tb = 0; x.ef = 0; x.es = 0; x.ap = 0; x.am = 0; x.tn = 0; x.tx = 0;
   x.an = 0; x.ax = 0; x.vn = 0; x.vx = 0; x.ea = 0; x.cb = -1; x.raw = false; x.hasSm = false;
   x.ct = -1; x.contArm = 0;
   for(int k = 0; k < 4; k++) x.sm[k] = 0;
   string p[];
   int n = StringSplit(o, ';', p);
   for(int i = 0; i < n; i++)
   {
      string kv[];
      if(StringSplit(p[i], '=', kv) != 2) continue;
      if(kv[0] == "g") x.gm = MathMax(0.5, MathMin(4.0, StringToDouble(kv[1])));
      else if(kv[0] == "n") x.noise = (int)MathMax(-1, MathMin(20, StringToInteger(kv[1])));
      else if(kv[0] == "x") x.extTf = (int)MathMax(0, MathMin(3, StringToInteger(kv[1])));
      else if(kv[0] == "b") x.fbars = (int)MathMax(0, MathMin(200, StringToInteger(kv[1])));
      else if(kv[0] == "o") x.opct = MathMax(0.0, MathMin(20.0, StringToDouble(kv[1])));
      else if(kv[0] == "ga") x.ga = MathMax(0.0, MathMin(10.0, StringToDouble(kv[1])));
      else if(kv[0] == "gn") x.gn = MathMax(0.0, MathMin(5000.0, StringToDouble(kv[1])));
      else if(kv[0] == "gx") x.gx = MathMax(0.0, MathMin(10000.0, StringToDouble(kv[1])));
      else if(kv[0] == "lm") x.lm = MathMax(0.0, MathMin(3.0, StringToDouble(kv[1])));
      else if(kv[0] == "mp") x.mp = (int)MathMax(0, MathMin(LB_MAXPEND, StringToInteger(kv[1])));
      else if(kv[0] == "tb") x.tb = (int)MathMax(0, MathMin(50, StringToInteger(kv[1])));
      else if(kv[0] == "ef") x.ef = (int)MathMax(0, MathMin(500, StringToInteger(kv[1])));
      else if(kv[0] == "es") x.es = (int)MathMax(0, MathMin(1000, StringToInteger(kv[1])));
      else if(kv[0] == "ap") x.ap = (int)MathMax(0, MathMin(500, StringToInteger(kv[1])));
      else if(kv[0] == "am") x.am = MathMax(0.0, MathMin(20.0, StringToDouble(kv[1])));
      else if(kv[0] == "tn") x.tn = MathMax(0.0, MathMin(10000.0, StringToDouble(kv[1])));
      else if(kv[0] == "tx") x.tx = MathMax(0.0, MathMin(20000.0, StringToDouble(kv[1])));
      else if(kv[0] == "an") x.an = MathMax(0.0, MathMin(20000.0, StringToDouble(kv[1])));
      else if(kv[0] == "ax") x.ax = MathMax(0.0, MathMin(50000.0, StringToDouble(kv[1])));
      else if(kv[0] == "vn") x.vn = MathMax(0.0, MathMin(20.0, StringToDouble(kv[1])));
      else if(kv[0] == "vx") x.vx = MathMax(0.0, MathMin(50.0, StringToDouble(kv[1])));
      else if(kv[0] == "ea") x.ea = MathMax(0.0, MathMin(20.0, StringToDouble(kv[1])));
      else if(kv[0] == "cb") x.cb = MathMax(0.0, MathMin(5.0, StringToDouble(kv[1])));
      else if(kv[0] == "r") x.raw = (kv[1] != "0");
      else if(kv[0] == "ct") x.ct = (StringToInteger(kv[1]) != 0) ? 1 : 0;
      else if(kv[0] == "sm")
      {
         string m[];
         if(StringSplit(kv[1], ',', m) == 4)
         {
            x.hasSm = true;
            for(int k = 0; k < 4; k++) x.sm[k] = (int)MathMax(0, MathMin(16777215, StringToInteger(m[k])));
         }
      }
   }
   // réglages qui changent la lecture du marché ou un seuil : tous les filtres du scénario sont recalculés
   if(x.ef > 0 || x.es > 0 || x.ap > 0 || x.an > 0 || x.ax > 0 || x.vn > 0 || x.vx > 0 || x.ea > 0 || x.cb >= 0) x.raw = true;
}

void LbAddOpt(string &s, string kv) { s += (s != "" ? ";" : "") + kv; }

string LbOpts(const LbScen &x)
{
   string s = "";
   if(x.gm != 1.0) s += "g=" + DoubleToString(x.gm, 2);
   if(x.noise >= 0) s += (s != "" ? ";" : "") + "n=" + IntegerToString(x.noise);
   if(x.extTf > 0) s += (s != "" ? ";" : "") + "x=" + IntegerToString(x.extTf);
   if(x.fbars > 0) s += (s != "" ? ";" : "") + "b=" + IntegerToString(x.fbars);
   if(x.opct > 0) s += (s != "" ? ";" : "") + "o=" + DoubleToString(x.opct, 2);
   if(x.ga > 0) s += (s != "" ? ";" : "") + "ga=" + DoubleToString(x.ga, 2);
   if(x.gn > 0) s += (s != "" ? ";" : "") + "gn=" + DoubleToString(x.gn, 0);
   if(x.gx > 0) s += (s != "" ? ";" : "") + "gx=" + DoubleToString(x.gx, 0);
   if(x.lm > 0) s += (s != "" ? ";" : "") + "lm=" + DoubleToString(x.lm, 2);
   if(x.mp > 0) s += (s != "" ? ";" : "") + "mp=" + IntegerToString(x.mp);
   if(x.tb > 0) LbAddOpt(s, "tb=" + IntegerToString(x.tb));
   if(x.ef > 0) LbAddOpt(s, "ef=" + IntegerToString(x.ef));
   if(x.es > 0) LbAddOpt(s, "es=" + IntegerToString(x.es));
   if(x.ap > 0) LbAddOpt(s, "ap=" + IntegerToString(x.ap));
   if(x.am > 0) LbAddOpt(s, "am=" + DoubleToString(x.am, 2));
   if(x.tn > 0) LbAddOpt(s, "tn=" + DoubleToString(x.tn, 0));
   if(x.tx > 0) LbAddOpt(s, "tx=" + DoubleToString(x.tx, 0));
   if(x.an > 0) LbAddOpt(s, "an=" + DoubleToString(x.an, 0));
   if(x.ax > 0) LbAddOpt(s, "ax=" + DoubleToString(x.ax, 0));
   if(x.vn > 0) LbAddOpt(s, "vn=" + DoubleToString(x.vn, 2));
   if(x.vx > 0) LbAddOpt(s, "vx=" + DoubleToString(x.vx, 2));
   if(x.ea > 0) LbAddOpt(s, "ea=" + DoubleToString(x.ea, 2));
   if(x.cb >= 0) LbAddOpt(s, "cb=" + DoubleToString(x.cb, 2));
   if(x.raw) LbAddOpt(s, "r=1");
   if(x.ct >= 0) LbAddOpt(s, "ct=" + IntegerToString(x.ct));
   if(x.hasSm) LbAddOpt(s, "sm=" + IntegerToString(x.sm[0]) + "," + IntegerToString(x.sm[1]) + "," + IntegerToString(x.sm[2]) + "," + IntegerToString(x.sm[3]));
   return s;
}

//--- 7.7.0 : contexte du scénario évalué (valeurs du robot hors scénario à réglages propres)
int      g_lbCurS   = -1;
bool     g_lbOwnSig = false;          // EMA ou ATR propres au scénario
double   g_lbF = 0, g_lbS = 0, g_lbA = 0;   // EMA rapide, EMA lente, ATR (pts)
int      g_lbHF = INVALID_HANDLE, g_lbHS = INVALID_HANDLE;
double   g_lbAn = 0, g_lbAx = 0, g_lbVn = 0, g_lbVx = 0, g_lbEa = 0, g_lbCb = -1;

// Extension du scénario : 1 = TF du robot, 2 = M15, 3 = H1 (multiple de l'ATR de ce timeframe)
double LbExtAtr(int x)
{
   if(x == 1) return (g_lbA > 0 && g_lbS > 0) ? ((mk_bid - g_lbS) / _Point) / g_lbA : 0;   // 7.7.0 : EMA / ATR du scénario
   int k = x - 2;
   if(k < 0 || k > 1 || g_lbExtSlow[k] == INVALID_HANDLE || g_lbExtAtr[k] == INVALID_HANDLE) return mk_extAtr;
   double e[1], at[1];
   if(CopyBuffer(g_lbExtSlow[k], 0, 0, 1, e) != 1 || CopyBuffer(g_lbExtAtr[k], 0, 1, 1, at) != 1 || at[0] <= 0) return 0;
   return (mk_bid - e[0]) / at[0];
}

//+------------------------------------------------------------------+
//| Mesures sur les vrais ordres : slippage et commission           |
//+------------------------------------------------------------------+
void LbAvg(double &avg, int &n, double v)
{
   n++;
   int k = (n < 20) ? n : 20;                       // moyenne simple puis moyenne glissante (20 derniers)
   avg += (v - avg) / k;
}

// Appelé pour chaque requête acceptée (OnTradeTransaction, TRADE_TRANSACTION_REQUEST)
void LaboOnRequest(const MqlTradeRequest &rq, const MqlTradeResult &rs)
{
   if(!g_lbOn || g_bt) return;                      // backtest : valeurs mesurées en réel, passées par le bot
   if(rq.action != TRADE_ACTION_DEAL || rq.symbol != _Symbol || rq.magic != MagicNumber) return;
   if(rq.price <= 0 || rs.price <= 0) return;
   double adv = (rq.type == ORDER_TYPE_BUY) ? (rs.price - rq.price) / _Point : (rq.price - rs.price) / _Point;
   if(MathAbs(adv) > 5000) return;                  // valeur aberrante ignorée
   MqlDateTime d;
   TimeToStruct(LocalNow(), d);
   LbAvg(g_lbSlip[d.hour], g_lbSlipN[d.hour], adv);
   LbAvg(g_lbSlip[24], g_lbSlipN[24], adv);
   g_lbDirty = true;
}

// Appelé pour chaque deal ajouté (OnTradeTransaction, TRADE_TRANSACTION_DEAL_ADD)
void LaboOnDeal(ulong deal)
{
   if(!g_lbOn || g_bt || !HistoryDealSelect(deal)) return;
   if(HistoryDealGetString(deal, DEAL_SYMBOL) != _Symbol) return;
   if(HistoryDealGetInteger(deal, DEAL_MAGIC) != MagicNumber) return;
   long t = HistoryDealGetInteger(deal, DEAL_TYPE);
   if(t != DEAL_TYPE_BUY && t != DEAL_TYPE_SELL) return;
   double v = HistoryDealGetDouble(deal, DEAL_VOLUME);
   if(v <= 0) return;
   LbAvg(g_lbComm, g_lbCommN, MathAbs(HistoryDealGetDouble(deal, DEAL_COMMISSION)) / v);
   g_lbDirty = true;
}

double LbSlipPts()
{
   MqlDateTime d;
   TimeToStruct(LocalNow(), d);
   if(g_lbSlipN[d.hour] >= 5) return g_lbSlip[d.hour];
   if(g_lbSlipN[24] >= 1) return g_lbSlip[24];
   return 0;
}

double LbComm(double lots) { return g_lbComm * lots; }

//+------------------------------------------------------------------+
//| Scénarios                                                        |
//+------------------------------------------------------------------+
void LbCopy(LbScen &d, const LbScen &s)
{
   d.key = s.key; d.name = s.name;
   for(int i = 0; i < LB_NF; i++) { d.mode[i] = s.mode[i]; d.adj[i] = s.adj[i]; }
   d.gm = s.gm; d.noise = s.noise; d.extTf = s.extTf; d.fbars = s.fbars; d.opct = s.opct;
   d.ga = s.ga; d.gn = s.gn; d.gx = s.gx; d.lm = s.lm; d.mp = s.mp;
   d.tb = s.tb; d.ef = s.ef; d.es = s.es; d.ap = s.ap; d.am = s.am; d.tn = s.tn; d.tx = s.tx;
   d.an = s.an; d.ax = s.ax; d.vn = s.vn; d.vx = s.vx; d.ea = s.ea; d.cb = s.cb; d.raw = s.raw; d.hasSm = s.hasSm;
   for(int k = 0; k < 4; k++) d.sm[k] = s.sm[k];
   d.slotKey = s.slotKey; d.hF = s.hF; d.hS = s.hS; d.hA = s.hA;
   d.ct = s.ct; d.contArm = s.contArm;
   d.bal = s.bal; d.start = s.start; d.withdrawn = s.withdrawn; d.reloaded = s.reloaded; d.cycles = s.cycles; d.reloads = s.reloads;
   d.active = s.active; d.cutPend = s.cutPend; d.init = s.init; d.target = s.target; d.peak = s.peak; d.maxDD = s.maxDD; d.minML = s.minML;
   d.t0 = s.t0; d.tEntry = s.tEntry; d.slot = s.slot; d.maxPos = s.maxPos; d.nSeries = s.nSeries;
   d.dir = s.dir; d.nPos = s.nPos; d.nPend = s.nPend;
   for(int i = 0; i < s.nPos && i < LB_MAXPOS; i++) { d.pOp[i] = s.pOp[i]; d.pLot[i] = s.pLot[i]; }
   for(int i = 0; i < s.nPend && i < LB_MAXPEND; i++) { d.qPx[i] = s.qPx[i]; d.qLot[i] = s.qLot[i]; }
   d.sumL = s.sumL; d.sumPL = s.sumPL; d.tp = s.tp; d.tpSig = s.tpSig;
   d.ok = s.ok; d.why = s.why;
}

void LbClearSeries(LbScen &x)
{
   x.dir = 0; x.nPos = 0; x.nPend = 0; x.sumL = 0; x.sumPL = 0; x.tp = 0; x.tpSig = -1;
}

void LbClearGoal(LbScen &x)
{
   x.active = false; x.cutPend = false; x.init = 0; x.target = 0; x.peak = 0; x.maxDD = 0; x.minML = 0;
   x.t0 = 0; x.tEntry = 0; x.slot = ""; x.maxPos = 0; x.nSeries = 0;
   LbClearSeries(x);
}

// Compte virtuel neuf : solde réel du moment
void LbFresh(LbScen &x)
{
   x.bal = AccountInfoDouble(ACCOUNT_BALANCE);
   x.start = x.bal; x.withdrawn = 0; x.reloaded = 0; x.cycles = 0; x.reloads = 0;
   x.slotKey = 0;
   x.contArm = 0;
   LbClearGoal(x);
   x.ok = false; x.why = "";
}

// Jeton → mode / ajustement
void LbParseTok(string t, int &mode, int &adj)
{
   StringTrimLeft(t); StringTrimRight(t);
   mode = LBM_SAME; adj = 0;
   if(t == "" || t == "=") return;
   if(t == "off") { mode = LBM_OFF; return; }
   if(t == "on")  { mode = LBM_ON; return; }
   if(t == "~")   { mode = LBM_TOGGLE; return; }
   ushort c = StringGetCharacter(t, 0);
   if(c == '+' || c == '-')
   {
      int v = (int)StringToInteger(StringSubstr(t, 1));
      if(v > 90) v = 90;
      mode = LBM_ON;
      adj = (c == '+') ? v : -v;
   }
}

string LbTok(int mode, int adj)
{
   if(mode == LBM_OFF) return "off";
   if(mode == LBM_TOGGLE) return "~";
   if(mode == LBM_ON) return (adj == 0) ? "on" : (adj > 0 ? "+" + IntegerToString(adj) : IntegerToString(adj));
   return "=";
}

bool LbOn(const LbScen &x, int i)
{
   if(x.mode[i] == LBM_SAME) return FbOn(i);
   if(x.mode[i] == LBM_OFF) return false;
   if(x.mode[i] == LBM_TOGGLE) return !FbOn(i);
   return true;
}

// Réglages EFFECTIFS du scénario (pour les rapports) : jeton par filtre, en tenant compte du compte réel
string LbSpec(const LbScen &x)
{
   string s = "";
   for(int i = 0; i < LB_NF; i++)
   {
      bool on = LbOn(x, i);
      string t = !on ? "off" : ((x.mode[i] == LBM_ON && x.adj[i] != 0) ? LbTok(LBM_ON, x.adj[i]) : "on");
      s += (i > 0 ? "," : "") + t;
   }
   return s;
}

// Nom affiché (les scénarios « inverse » suivent les réglages du compte)
string LbName(const LbScen &x)
{
   for(int i = 0; i < LB_NF; i++)
      if(x.mode[i] == LBM_TOGGLE && StringFind(x.key, "t") == 0)
         return (FbOn(i) ? "Sans " : "Avec ") + FbName(i);
   return x.name;
}

int LbFind(LbScen &arr[], int n, string key)
{
   for(int i = 0; i < n; i++) if(arr[i].key == key) return i;
   return -1;
}

void LbDefine(LbScen &x, string key, string name, string toks, string opts = "")
{
   x.key = key; x.name = name;
   LbOptsParse(x, opts);
   string a[];
   int n = StringSplit(toks, ',', a);
   for(int i = 0; i < LB_NF; i++)
   {
      int m, j;
      LbParseTok(i < n ? a[i] : "=", m, j);
      x.mode[i] = m; x.adj[i] = j;
   }
}

//--- 7.7.0 : indicateurs propres aux scénarios (timeframe du robot), partagés entre scénarios de même période
#define LB_MAXH 24
int g_lbHk[LB_MAXH], g_lbHp[LB_MAXH], g_lbHh[LB_MAXH];
int g_lbHn = 0;

int LbHandle(int kind, int period)          // kind 0 = EMA, 1 = ATR
{
   if(period <= 0) return INVALID_HANDLE;
   for(int i = 0; i < g_lbHn; i++) if(g_lbHk[i] == kind && g_lbHp[i] == period) return g_lbHh[i];
   if(g_lbHn >= LB_MAXH) return INVALID_HANDLE;
   int h = (kind == 0) ? iMA(_Symbol, g_sigTF, period, 0, MODE_EMA, PRICE_CLOSE) : iATR(_Symbol, g_sigTF, period);
   if(h == INVALID_HANDLE) { Log("Labo : indicateur " + (kind == 0 ? "EMA " : "ATR ") + IntegerToString(period) + " impossible"); return INVALID_HANDLE; }
   g_lbHk[g_lbHn] = kind; g_lbHp[g_lbHn] = period; g_lbHh[g_lbHn] = h; g_lbHn++;
   return h;
}

void LbReleaseHandles()
{
   for(int i = 0; i < g_lbHn; i++) if(g_lbHh[i] != INVALID_HANDLE) IndicatorRelease(g_lbHh[i]);
   g_lbHn = 0;
}

// Indicateurs du scénario : une période égale à celle du compte garde l'indicateur du robot
void LbPrepare(LbScen &x)
{
   x.hF = (x.ef > 0 && x.ef != PInt(P_FAST)) ? LbHandle(0, x.ef) : INVALID_HANDLE;
   x.hS = (x.es > 0 && x.es != PInt(P_SLOW)) ? LbHandle(0, x.es) : INVALID_HANDLE;
   x.hA = (x.ap > 0 && x.ap != PInt(P_ATRP)) ? LbHandle(1, x.ap) : INVALID_HANDLE;
}

double LbAtrH(int h, int sh)
{
   double b[1];
   if(h == INVALID_HANDLE || CopyBuffer(h, 0, sh, 1, b) != 1) return 0.0;
   return b[0] / _Point;
}

// ATR (pts) utilisé par la grille et le TP du scénario
double LbAtr(const LbScen &x) { return (x.hA != INVALID_HANDLE) ? LbAtrH(x.hA, BarShift()) : ATRPoints(BarShift()); }

string BtFile(string kind);   // [ARCHI] 7.6.0 (SX_Backtest.mqh)

// Recharge la liste : scénarios par défaut + scénarios perso ; l'état des scénarios déjà connus est conservé
void LaboLoadScenarios()
{
   int n = 0;
   string allSame = "", allOff = "";
   for(int i = 0; i < LB_NF; i++) { allSame += (i ? "," : "") + "="; allOff += (i ? "," : "") + "off"; }
   // 7.6.0 : backtest de l'optimiseur : son propre fichier de scénarios ; « auto=0 » = sans les 12 scénarios automatiques
   string scen = LB_SCEN_FILE, txt0 = "";
   if(g_bt && FileIsExist(BtFile("scen.txt"), FILE_COMMON)) scen = BtFile("scen.txt");
   bool autoSc = true;
   if(AFileRead(scen, txt0) && StringFind(txt0, "auto=0") >= 0) autoSc = false;
   LbDefine(g_lbTmp[n], "miroir", "Réglages actuels", allSame);
   n++;
   for(int i = 0; i < LB_NF && autoSc; i++)
   {
      string t = "";
      for(int k = 0; k < LB_NF; k++) t += (k ? "," : "") + (k == i ? "~" : "=");
      LbDefine(g_lbTmp[n], "t" + IntegerToString(i), FbName(i), t);
      n++;
   }
   if(autoSc)
   {
      LbDefine(g_lbTmp[n], "aucun", "Aucun filtre", allOff);
      n++;
   }

   string txt, reset = "";
   g_lbWanted = true;
   if(AFileRead(scen, txt))
   {
      StringReplace(txt, "\r", "");
      string lines[];
      int nl = StringSplit(txt, '\n', lines);
      for(int li = 0; li < nl; li++)
      {
         string l = lines[li];
         StringTrimLeft(l); StringTrimRight(l);
         if(l == "" || StringGetCharacter(l, 0) == '#') continue;
         if(StringFind(l, "actif=") == 0) { g_lbWanted = (StringSubstr(l, 6) != "0"); continue; }
         if(StringFind(l, "reset=") == 0) { reset = StringSubstr(l, 6); continue; }
         string f[];
         if(StringSplit(l, '|', f) < 3) continue;
         if(StringFind(f[0], "p") != 0 || n >= LB_MAXSC) continue;   // scénarios perso : clés p…
         if(LbFind(g_lbTmp, n, f[0]) >= 0) continue;
         LbDefine(g_lbTmp[n], f[0], f[1], f[2], ArraySize(f) > 3 ? f[3] : "");
         n++;
      }
   }
   bool doReset = (reset != "" && reset != g_lbReset);
   for(int i = 0; i < n; i++)
   {
      int j = doReset ? -1 : LbFind(g_lb, g_lbN, g_lbTmp[i].key);
      if(j >= 0)
      {
         int md[LB_NF], aj[LB_NF];
         for(int k = 0; k < LB_NF; k++) { md[k] = g_lbTmp[i].mode[k]; aj[k] = g_lbTmp[i].adj[k]; }
         string nm = g_lbTmp[i].name;
         string os = LbOpts(g_lbTmp[i]);              // options du fichier (gardées ; l'état vient de g_lb)
         LbCopy(g_lbTmp[i], g_lb[j]);
         g_lbTmp[i].name = nm;
         LbOptsParse(g_lbTmp[i], os);
         for(int k = 0; k < LB_NF; k++) { g_lbTmp[i].mode[k] = md[k]; g_lbTmp[i].adj[k] = aj[k]; }
      }
      else
         LbFresh(g_lbTmp[i]);
   }
   for(int i = 0; i < n; i++) LbCopy(g_lb[i], g_lbTmp[i]);
   g_lbN = n;
   for(int i = 0; i < n; i++) LbPrepare(g_lb[i]);                // 7.7.0 : EMA / ATR propres
   if(reset != "") g_lbReset = reset;
   if(doReset) Log("Labo des filtres : comptes virtuels remis à zéro (" + IntegerToString(n) + " scénarios)");
   g_lbDirty = true;
}

//+------------------------------------------------------------------+
//| Filtres d'un scénario (seuils éventuellement ajustés)            |
//+------------------------------------------------------------------+
double LbVolRatio()
{
   if(PBool(P_F_VOL)) return mk_volRatio;           // déjà calculé par le filtre réel
   datetime bar1 = iTime(_Symbol, g_sigTF, 1);
   if(bar1 != g_lbVolBar)
   {
      int nb = PInt(P_VOLBARS);
      long vols[];
      ArraySetAsSeries(vols, true);
      if(nb > 0 && CopyTickVolume(_Symbol, g_sigTF, 1, nb + 1, vols) == nb + 1)
      {
         double sum = 0;
         for(int k = 1; k <= nb; k++) sum += (double)vols[k];
         double avg = sum / nb;
         g_lbVolRatio = (avg > 0) ? (double)vols[0] / avg : 1.0;
         g_lbVolBar = bar1;
      }
   }
   return g_lbVolRatio;
}

bool LbTimeBlocked()
{
   MqlDateTime dt;
   TimeToStruct(PBool(P_T_SERVER) ? TimeTradeServer() : LocalNow(), dt);
   int h = dt.hour, s = PInt(P_T_START), e = PInt(P_T_END);
   if(s == e) return false;
   if(s < e) return (h >= s && h < e);
   return (h >= s || h < e);
}

// 7.7.0 : contexte d'évaluation du scénario s (-1 = valeurs du robot)
void LbSetCtx(int s)
{
   g_lbCurS = s;
   g_lbF = mk_fast; g_lbS = mk_slow; g_lbA = mk_atrPts; g_lbOwnSig = false;
   g_lbHF = g_hFast; g_lbHS = g_hSlow;
   g_lbAn = 0; g_lbAx = 0; g_lbVn = 0; g_lbVx = 0; g_lbEa = 0; g_lbCb = -1;
   if(s < 0) return;
   g_lbAn = g_lb[s].an; g_lbAx = g_lb[s].ax; g_lbVn = g_lb[s].vn; g_lbVx = g_lb[s].vx; g_lbEa = g_lb[s].ea; g_lbCb = g_lb[s].cb;
   if(g_lb[s].hF == INVALID_HANDLE && g_lb[s].hS == INVALID_HANDLE && g_lb[s].hA == INVALID_HANDLE) return;
   g_lbOwnSig = true;
   int sh = BarShift();
   if(g_lb[s].hF != INVALID_HANDLE) g_lbHF = g_lb[s].hF;
   if(g_lb[s].hS != INVALID_HANDLE) g_lbHS = g_lb[s].hS;
   double f, sl;
   if(ReadEMAsH(g_lbHF, g_lbHS, sh, f, sl)) { g_lbF = f; g_lbS = sl; }
   else { g_lbF = 0; g_lbS = 0; }
   if(g_lb[s].hA != INVALID_HANDLE) g_lbA = LbAtrH(g_lb[s].hA, sh);
}

// 7.7.0 : même calcul que BarsSinceCrossN, sur les EMA du contexte (scénario à EMA propres)
int LbBarsSinceCross(int sh, int maxBars, int noise)
{
   if(!g_lbOwnSig) return BarsSinceCrossN(sh, maxBars, noise);
   if(noise < 0) noise = 0;
   int n = maxBars + noise + 2;
   double a[], b[];
   ArraySetAsSeries(a, true);
   ArraySetAsSeries(b, true);
   if(CopyBuffer(g_lbHF, 0, sh, n, a) != n || CopyBuffer(g_lbHS, 0, sh, n, b) != n) return -1;
   int s0 = (a[0] > b[0]) ? 1 : ((a[0] < b[0]) ? -1 : 0);
   if(s0 == 0) return 0;
   int k = 1;
   while(k < n)
   {
      int sk = (a[k] > b[k]) ? 1 : ((a[k] < b[k]) ? -1 : 0);
      if(sk == s0) { k++; continue; }
      int r = 0;
      while(k + r < n)
      {
         int sr = (a[k + r] > b[k + r]) ? 1 : ((a[k + r] < b[k + r]) ? -1 : 0);
         if(sr == s0) break;
         r++;
      }
      if(noise > 0 && r <= noise && k + r < n) { k += r; continue; }
      return (k - 1 > maxBars) ? -1 : k - 1;
   }
   return -1;
}

// Filtre i du scénario (activé) : true = laisse passer. a = ajustement en fraction (+0.2 = assoupli de 20 %)
bool LbRaw(int i, double a, int dir, string &why)
{
   why = "";
   switch(i)
   {
      case 0:
      {
         if(g_lbA <= 0) return true;
         double lo = (g_lbAn > 0 ? g_lbAn : PDbl(P_ATRMIN)) * (1.0 - a), hi = (g_lbAx > 0 ? g_lbAx : PDbl(P_ATRMAX)) * (1.0 + a);
         if(g_lbA >= lo && g_lbA <= hi) return true;
         why = "ATR " + F0(g_lbA) + " hors " + F0(lo) + "–" + F0(hi);
         return false;
      }
      case 1:
      {
         double f = g_lbF, s = g_lbS, p = mk_bid;
         if(f <= 0 || s <= 0) return true;
         if((f > s && p < f) || (f < s && p > f) || (p > f && p < s) || (p < f && p > s)) { why = "Prix / EMA"; return false; }
         return true;
      }
      case 2:
      {
         double r = LbVolRatio();
         if(r <= 0) return true;
         double lo = (g_lbVn > 0 ? g_lbVn : PDbl(P_VOLMIN)) * (1.0 - a), hi = (g_lbVx > 0 ? g_lbVx : PDbl(P_VOLMAX)) * (1.0 + a);
         if(r >= lo && r <= hi) return true;
         why = "Volume " + F0(r * 100) + " %";
         return false;
      }
      case 3:
      case 4:
         return MtfRaw(i, dir, why);
      case 5:
      {
         if(dir == 0) return true;
         double o = iOpen(_Symbol, g_hiTF, 1), c = iClose(_Symbol, g_hiTF, 1);
         double b[1];
         if(o <= 0 || c <= 0 || g_hATRHi == INVALID_HANDLE || CopyBuffer(g_hATRHi, 0, 1, 1, b) != 1) { why = "Bougie indisponible"; return false; }
         double body = (c - o) / _Point, mn = (g_lbCb >= 0 ? g_lbCb : PDbl(P_CANBODY)) * (1.0 - a) * b[0] / _Point;
         int cd = (body > mn) ? 1 : ((body < -mn) ? -1 : 0);
         if(cd == 0 || cd == dir) return true;
         why = "Bougie contre le signal";
         return false;
      }
      case 6:
         if(!LbTimeBlocked()) return true;
         why = "Horaire";
         return false;
      case 7:
      {
         datetime srv = TimeTradeServer();
         int before = (int)MathRound(PInt(P_NEWS_BEFORE) * 60 * (1.0 - a)), after = (int)MathRound(PInt(P_NEWS_AFTER) * 60 * (1.0 - a));
         if(before < 0) before = 0;
         if(after < 0) after = 0;
         for(int k = 0; k < g_nwN; k++)
         {
            int dt = (int)(g_nwT[k] - srv);
            if(dt <= before && dt >= -after) { why = "News " + g_nwCur[k]; return false; }
         }
         return true;
      }
      case 8:
      {
         double mx = PInt(P_MAXSPREAD) * (1.0 + a);
         if(mk_spread <= mx) return true;
         why = "Spread " + F0(mk_spread);
         return false;
      }
      case 9:
      {
         if(dir == 0 || g_lbA <= 0) return true;
         double ex = (g_lbCurExt > 0) ? LbExtAtr(g_lbCurExt) : ((g_lbOwnSig && PInt(P_EXTTF) == 0) ? LbExtAtr(1) : mk_extAtr);
         double e = (dir > 0) ? ex : -ex, mx = (g_lbEa > 0 ? g_lbEa : PDbl(P_EXTATR)) * (1.0 + a);
         if(e <= mx) return true;
         why = "Extension " + F1(e) + " x ATR";
         return false;
      }
      case 10:
      {
         if(dir == 0) return true;
         int bars = (g_lbCurBars > 0) ? g_lbCurBars : (int)MathMax(1, MathRound(PInt(P_FRESHBARS) * (1.0 + a)));   // 7.4.0 : bougies du scénario
         int age = LbBarsSinceCross(BarShift(), bars, g_lbCurNoise >= 0 ? g_lbCurNoise : PInt(P_FRESHNOISE));   // 7.7.0 : EMA du scénario
         if(age >= 0 && age <= bars) return true;
         why = "Croisement trop ancien";
         return false;
      }
   }
   return true;
}

// 1 Hz : verdict des filtres de chaque scénario
void LbEvalAll()
{
   int dir0 = (mk_fast > 0 && mk_slow > 0) ? ((mk_fast > mk_slow) ? 1 : ((mk_fast < mk_slow) ? -1 : 0)) : 0;
   bool   raw0[LB_NF], done0[LB_NF];
   string why0[LB_NF];
   for(int i = 0; i < LB_NF; i++) { done0[i] = false; raw0[i] = true; why0[i] = ""; }
   for(int s = 0; s < g_lbN; s++)
   {
      g_lb[s].ok = true;
      g_lb[s].why = "";
      g_lbCurNoise = g_lb[s].noise;
      g_lbCurExt = g_lb[s].extTf;
      g_lbCurBars = g_lb[s].fbars;
      bool own = g_lb[s].raw;                                  // 7.7.0 : tous les filtres recalculés pour le scénario
      LbSetCtx(own ? s : -1);
      int dir = own ? ((g_lbF > 0 && g_lbS > 0) ? ((g_lbF > g_lbS) ? 1 : ((g_lbF < g_lbS) ? -1 : 0)) : 0) : dir0;
      for(int i = 0; i < LB_NF; i++)
      {
         int m = g_lb[s].mode[i];
         bool pass;
         string w = "";
         if(m == LBM_OFF) continue;
         if(own)
         {
            if(!LbOn(g_lb[s], i)) continue;
            pass = LbRaw(i, (m == LBM_ON ? g_lb[s].adj[i] : 0) / 100.0, dir, w);
         }
         else if((i == 9 && g_lb[s].extTf > 0) || (i == 10 && (g_lb[s].noise >= 0 || g_lb[s].fbars > 0)))   // 7.3.0 : option du scénario : calcul propre
         {
            if(!LbOn(g_lb[s], i)) continue;
            pass = LbRaw(i, (m == LBM_ON ? g_lb[s].adj[i] : 0) / 100.0, dir, w);
         }
         else if(m == LBM_SAME || (m == LBM_TOGGLE && FbOn(i)))
         {
            if(m == LBM_TOGGLE || !FbOn(i)) continue;  // inversé d'un filtre actif = désactivé
            pass = FbOK(i);                              // exactement le verdict du compte réel (antirebond compris)
            if(!pass) w = FbWhy(i);
         }
         else if(g_lb[s].adj[i] == 0)
         {
            if(!done0[i]) { string w0; raw0[i] = LbRaw(i, 0, dir, w0); why0[i] = w0; done0[i] = true; }
            pass = raw0[i];
            w = why0[i];
         }
         else
            pass = LbRaw(i, g_lb[s].adj[i] / 100.0, dir, w);
         if(!pass)
         {
            g_lb[s].ok = false;
            g_lb[s].why = (w != "") ? w : FbName(i);
            break;
         }
      }
   }
   LbSetCtx(-1);
   g_lbCurNoise = -1;
   g_lbCurExt = 0;
   g_lbCurBars = 0;
}

//+------------------------------------------------------------------+
//| Séries virtuelles                                                |
//+------------------------------------------------------------------+
double LbFloating(const LbScen &x, double bid, double ask)
{
   if(x.nPos == 0) return 0;
   double px = (x.dir > 0) ? bid : ask;
   return x.dir * (px * x.sumL - x.sumPL) * g_lbK;
}

void LbAddPos(LbScen &x, double px, double lot)
{
   if(x.nPos >= LB_MAXPOS || lot <= 0) return;
   x.pOp[x.nPos] = px;
   x.pLot[x.nPos] = lot;
   x.nPos++;
   x.sumL += lot;
   x.sumPL += px * lot;
   x.bal -= LbComm(lot);
   x.tpSig = -1;
   if(x.nPos > x.maxPos) x.maxPos = x.nPos;
}

// Ferme toute la série virtuelle au prix px (TP : prix du TP ; marché : bid/ask avec slippage)
void LbCloseAt(LbScen &x, double px)
{
   if(x.nPos > 0) x.bal += x.dir * (px * x.sumL - x.sumPL) * g_lbK - LbComm(x.sumL);
   LbClearSeries(x);
}

void LbCloseMarket(LbScen &x, double bid, double ask)
{
   double slip = LbSlipPts() * _Point;
   LbCloseAt(x, (x.dir > 0) ? bid - slip : ask + slip);
}

// 7.6.0 : écart de grille et lot d'un scénario (même calcul que CalcGridStep / CalcLot, valeurs absolues de l'optimiseur)
double LbGridStep(const LbScen &x, int n)
{
   double gmul = (x.gm > 0) ? x.gm : 1.0;
   if(x.ga <= 0 && x.gn <= 0 && x.gx <= 0 && x.tb <= 0 && x.hA == INVALID_HANDLE) return CalcGridStep(n) * gmul;
   double step = (double)PInt(P_GRID);
   if(PBool(P_DYNGRID))
   {
      double atr = LbAtr(x);                         // 7.7.0 : ATR du scénario
      if(atr > 0)
      {
         double mn = (x.gn > 0) ? x.gn : (double)PInt(P_GRIDMIN), mx = (x.gx > 0) ? x.gx : (double)PInt(P_GRIDMAX);
         step = atr * ((x.ga > 0) ? x.ga : PDbl(P_GRIDATR));
         if(step < mn) step = mn;
         if(step > mx) step = mx;
      }
      long sl = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
      if(step < (double)sl) step = (double)sl;
   }
   double gm = PDbl(P_GRIDMULT);
   int tbm = MathMax(1, x.tb > 0 ? x.tb : PInt(P_TBM));
   if(gm != 1.0) step *= MathPow(gm, n / tbm);
   return step * gmul;
}

double LbLot(const LbScen &x, int n)
{
   if(x.lm <= 0 && x.tb <= 0) return CalcLot(n);
   int tbm = MathMax(1, x.tb > 0 ? x.tb : PInt(P_TBM));    // 7.7.0 : trades avant multiplication du scénario
   return NormVolume(PDbl(P_LOT) * MathPow(x.lm > 0 ? x.lm : PDbl(P_LOTMULT), n / tbm));
}

// 7.7.0 : distance du TP du scénario (même calcul que CalcTPDist, avec ses valeurs propres)
double LbTPDist(const LbScen &x)
{
   if(x.am <= 0 && x.tn <= 0 && x.tx <= 0 && x.hA == INVALID_HANDLE) return CalcTPDist();
   if(!PBool(P_DYNTP)) return PDbl(P_TPFIXED);
   double atr = LbAtr(x);
   if(atr <= 0) return CalcTPDist();
   double d = atr * (x.am > 0 ? x.am : PDbl(P_ATRMULT));
   double mn = (x.tn > 0) ? x.tn : (double)PInt(P_TPMIN), mx = (x.tx > 0) ? x.tx : (double)PInt(P_TPMAX);
   if(d < mn) d = mn;
   if(d > mx) d = mx;
   return d;
}

// Même logique que PlaceGridBatch (ordres limites sous / au-dessus de la série)
void LbGrid(LbScen &x, double bid, double ask)
{
   if(x.nPos == 0 || x.dir == 0) return;
   int maxp = MathMin(x.mp > 0 ? x.mp : PInt(P_MAXPEND), LB_MAXPEND);   // 7.6.0 : ordres max du scénario
   if(x.nPend >= maxp) return;
   bool buy = (x.dir > 0);
   long stops = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double minDist = MathMax(stops * _Point, _Point * 5);
   double anchor = 0;
   for(int k = 0; k < x.nPend; k++)
      if(anchor == 0 || (buy ? x.qPx[k] < anchor : x.qPx[k] > anchor)) anchor = x.qPx[k];
   if(anchor == 0)
      for(int k = 0; k < x.nPos; k++)
         if(anchor == 0 || (buy ? x.pOp[k] < anchor : x.pOp[k] > anchor)) anchor = x.pOp[k];
   int n = x.nPos + x.nPend;
   while(x.nPend < maxp && n < LB_MAXPOS)
   {
      double stepPts = LbGridStep(x, n);             // 7.3.0 / 7.6.0 : écart de grille du scénario
      double lot     = LbLot(x, n);
      double price   = buy ? anchor - stepPts * _Point : anchor + stepPts * _Point;
      if(buy)
      {
         if(price >= bid - minDist) price = bid - minDist - stepPts * _Point;
         if(price >= bid - minDist) break;
      }
      else
      {
         if(price <= ask + minDist) price = ask + minDist + stepPts * _Point;
         if(price <= ask + minDist) break;
      }
      price = NormalizeDouble(price, _Digits);
      x.qPx[x.nPend] = price;
      x.qLot[x.nPend] = lot;
      x.nPend++;
      anchor = price;
      n++;
   }
}

// TP commun recalculé quand la série change (comme ManageTP)
void LbTP(LbScen &x)
{
   if(x.nPos == 0) { x.tp = 0; x.tpSig = -1; return; }
   double avg;
   if(PBool(P_WAVG) && x.sumL > 0) avg = x.sumPL / x.sumL;
   else
   {
      double sp = 0;
      for(int k = 0; k < x.nPos; k++) sp += x.pOp[k];
      avg = sp / x.nPos;
   }
   double sig = x.nPos * 1000000.0 + x.sumL * 10000.0 + avg;
   if(sig == x.tpSig) return;
   double dist = LbTPDist(x);                        // 7.7.0
   x.tp = NormalizeDouble((x.dir > 0) ? avg + dist * _Point : avg - dist * _Point, _Digits);
   x.tpSig = sig;
}

void LbOpen(LbScen &x, int dir, double bid, double ask)
{
   double slip = LbSlipPts() * _Point;
   LbClearSeries(x);
   x.dir = dir;
   LbAddPos(x, (dir > 0) ? ask + slip : bid - slip, LbLot(x, 0));
   if(x.tEntry == 0) x.tEntry = TimeGMT();
   x.nSeries++;
   LbTP(x);
   LbGrid(x, bid, ask);
   g_lbDirty = true;
}

string LbJ(string k, string v, bool q = false) { return "\"" + k + "\":" + (q ? "\"" + JsonEsc(v) + "\"" : v); }

// Fin de l'objectif virtuel : événement LABO_SERIE, puis cycle 500 / recharge
void LbEnd(LbScen &x, string res)
{
   datetime now = TimeGMT();
   double profit = x.bal - x.init;
   string d = "{" + LbJ("sc", x.key, true) + "," + LbJ("nom", LbName(x), true) + "," + LbJ("spec", LbSpec(x), true) + "," + LbJ("opts", LbOpts(x), true) + "," +
              LbJ("creneau", x.slot, true) + "," + LbJ("debut", IntegerToString((long)x.t0)) + "," + LbJ("fin", IntegerToString((long)now)) + "," +
              LbJ("res", res, true) + "," + LbJ("depart", NumS(x.init)) + "," + LbJ("cible", NumS(x.target)) + "," +
              LbJ("solde", NumS(x.bal)) + "," + LbJ("profit", NumS(profit)) + "," + LbJ("dd", NumS(x.maxDD)) + "," +
              LbJ("ml_min", NumS(x.minML)) + "," + LbJ("entree_s", IntegerToString(x.tEntry > 0 ? (long)(x.tEntry - x.t0) : -1)) + "," +
              LbJ("pos_max", IntegerToString(x.maxPos)) + "," + LbJ("series", IntegerToString(x.nSeries));
   x.active = false;
   x.cutPend = false;
   double rep = PDbl(P_SEUIL_REPRISE);
   if(PBool(P_SEUIL_ON) && x.bal + 0.005 >= PDbl(P_SEUIL_ARRET) && x.bal > rep)
   {
      x.withdrawn += x.bal - rep;
      x.bal = rep;
      x.cycles++;
   }
   else if(x.bal + 0.005 < rep && x.bal < x.init)
   {
      x.reloaded += rep - x.bal;
      x.reloads++;
      x.bal = rep;
   }
   d += "," + LbJ("cycles", IntegerToString(x.cycles)) + "," + LbJ("retraits", NumS(x.withdrawn)) + "," +
        LbJ("recharges", NumS(x.reloaded)) + "," + LbJ("solde_apres", NumS(x.bal)) + "," + LbJ("depart_labo", NumS(x.start)) + "}";
   EvTyped("LABO_SERIE", 0, "", d);
   LbClearGoal(x);
   g_lbDirty = true;
}

// Objectif virtuel du scénario s lancé au créneau slot (objectif précédent pas fini : créneau sauté)
void LbStartGoal(int s, string slot)
{
   double rep = PDbl(P_SEUIL_REPRISE), pct = PDbl(P_SL_PCT);
   {
      if(g_lb[s].active)
      {
         EvTyped("LABO_SERIE", 0, "", "{" + LbJ("sc", g_lb[s].key, true) + "," + LbJ("nom", LbName(g_lb[s]), true) + "," +
                 LbJ("spec", LbSpec(g_lb[s]), true) + "," + LbJ("creneau", slot, true) + "," +
                 LbJ("debut", IntegerToString((long)TimeGMT())) + "," + LbJ("fin", IntegerToString((long)TimeGMT())) + "," +
                 LbJ("res", "saute", true) + "," + LbJ("solde", NumS(g_lb[s].bal)) + "}");
         return;
      }
      if(g_lb[s].bal + 0.005 < rep) return;
      if(PBool(P_SEUIL_ON) && g_lb[s].bal + 0.005 >= PDbl(P_SEUIL_ARRET)) return;
      LbClearGoal(g_lb[s]);
      g_lb[s].active = true;
      g_lb[s].init = g_lb[s].bal;
      g_lb[s].target = g_lb[s].bal * (1.0 + (g_lb[s].opct > 0 ? g_lb[s].opct : pct) / 100.0);   // 7.5.0 : objectif du scénario
      g_lb[s].peak = g_lb[s].bal;
      g_lb[s].t0 = TimeGMT();
      g_lb[s].slot = slot;
   }
   g_lbDirty = true;
}

// 7.8.0 : le scénario est-il en mode continu (option ct, sinon comme le compte) ?
bool LbCont(const LbScen &x) { return x.ct >= 0 ? (x.ct == 1) : PBool(P_SL_CONT); }

// Créneau consommé (appelé par SlotTick, mêmes conditions générales que le vrai robot)
void LaboOnSlot(string slot)
{
   if(!g_lbOn || !g_lbWanted) return;   // labo en pause : séries virtuelles figées
   for(int s = 0; s < g_lbN; s++)
      if(!g_lb[s].hasSm && !LbCont(g_lb[s])) LbStartGoal(s, slot);   // 7.7.0 : un scénario à créneaux propres suit les siens
}

// 7.8.0 : mode continu des scénarios (comme ContTick) : filtres du scénario OK pendant une bougie M5, encore OK à sa
// clôture → objectif virtuel lancé
void LbContTick()
{
   if(g_state != ST_ACTIF || g_maint) return;        // mêmes conditions générales que LbOwnSlots
   datetime bar = iTime(_Symbol, PERIOD_M5, 0);
   if(bar <= 0) return;
   string w;
   bool env = f_marketOpen && TradeEnvOK(w);
   for(int s = 0; s < g_lbN; s++)
   {
      if(!LbCont(g_lb[s]) || g_lb[s].active) { g_lb[s].contArm = 0; continue; }
      bool ok = env && g_lb[s].ok;
      if(g_lb[s].contArm == 0) { if(ok) g_lb[s].contArm = bar; continue; }
      if(bar == g_lb[s].contArm) continue;
      g_lb[s].contArm = 0;
      if(ok) LbStartGoal(s, "continu");
   }
}

// 7.7.0 : créneaux propres aux scénarios (jours de semaine, fenêtre de lancement du compte, une fois par créneau)
void LbOwnSlots()
{
   bool accCont = PBool(P_SL_CONT);                  // 7.8.0 : compte en mode continu : les scénarios « ct=0 » suivent les créneaux du compte
   if((!PBool(P_SL_ON) && !accCont) || g_state != ST_ACTIF || g_maint) return;
   MqlDateTime d;
   TimeToStruct(LocalNow(), d);
   if(d.day_of_week < 1 || d.day_of_week > 5) return;
   int now = d.hour * 60 + d.min, win = SlotWindow();
   for(int s = 0; s < g_lbN; s++)
   {
      if(LbCont(g_lb[s]) || (!g_lb[s].hasSm && !accCont)) continue;
      int sl = -1;
      for(int mi = 0; mi < 4 && sl < 0; mi++)
      {
         int mk = g_lb[s].hasSm ? g_lb[s].sm[mi] : PInt(P_SL_M00 + mi);
         for(int h = 0; h < 24; h++)
         {
            int t = h * 60 + mi * 15;
            if((mk & (1 << h)) != 0 && now >= t && now < t + win) { sl = t; break; }
         }
      }
      if(sl < 0) continue;
      long key = SlotKey(d, sl);
      if(key == g_lb[s].slotKey) continue;
      g_lb[s].slotKey = key;
      LbStartGoal(s, SlotText(sl));
   }
}

// Heure de coupure (appelé par CutTick)
void LaboOnCut(string name)
{
   if(!g_lbOn || !g_lbWanted) return;
   for(int s = 0; s < g_lbN; s++)
   {
      if(!g_lb[s].active) continue;
      if(g_lb[s].nPos == 0) LbEnd(g_lb[s], "coupe");
      else g_lb[s].cutPend = true;
   }
}

// À chaque tick : remplissage de la grille, TP, objectif, 500, SL d'urgence, nouvelles séries
void LaboTick()
{
   if(!g_lbOn || !g_lbWanted || g_lbN == 0 || g_lbK <= 0) return;
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID), ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   if(bid <= 0 || ask <= 0) return;
   bool armed = !g_maint && g_state == ST_ACTIF && !g_stoppedManually && !g_vacation && f_marketOpen;
   if(armed) { string w; armed = TradeEnvOK(w); }
   int sig = 0;
   bool sigRead = false;
   for(int s = 0; s < g_lbN; s++)
   {
      if(!g_lb[s].active) continue;
      //--- remplissage des ordres limites
      if(g_lb[s].nPos > 0)
      {
         int k = 0;
         while(k < g_lb[s].nPend)
         {
            bool hit = (g_lb[s].dir > 0) ? (ask <= g_lb[s].qPx[k]) : (bid >= g_lb[s].qPx[k]);
            if(!hit) { k++; continue; }
            LbAddPos(g_lb[s], g_lb[s].qPx[k], g_lb[s].qLot[k]);
            for(int j = k + 1; j < g_lb[s].nPend; j++) { g_lb[s].qPx[j - 1] = g_lb[s].qPx[j]; g_lb[s].qLot[j - 1] = g_lb[s].qLot[j]; }
            g_lb[s].nPend--;
         }
         LbTP(g_lb[s]);
         //--- TP
         bool tpHit = (g_lb[s].tp > 0) && ((g_lb[s].dir > 0) ? (bid >= g_lb[s].tp) : (ask <= g_lb[s].tp));
         if(tpHit) { LbCloseAt(g_lb[s], g_lb[s].tp); g_lbDirty = true; }
      }
      //--- objectif, protections
      double fl = LbFloating(g_lb[s], bid, ask);
      double eq = g_lb[s].bal + fl;
      if(eq > g_lb[s].peak) g_lb[s].peak = eq;
      if(g_lb[s].peak > 0)
      {
         double dd = (g_lb[s].peak - eq) / g_lb[s].peak * 100.0;
         if(dd > g_lb[s].maxDD) g_lb[s].maxDD = dd;
      }
      if(g_lb[s].nPos > 0 && PBool(P_EMSL) && g_lb[s].init > 0 && eq <= g_lb[s].init * (1.0 - PDbl(P_EMSLPCT) / 100.0))
      {
         LbCloseMarket(g_lb[s], bid, ask);
         LbEnd(g_lb[s], "sl");
         continue;
      }
      if(g_lb[s].nPos > 0 && PBool(P_SEUIL_ON) && eq >= PDbl(P_SEUIL_ARRET))
      {
         LbCloseMarket(g_lb[s], bid, ask);
         if(g_lb[s].bal + 0.005 >= PDbl(P_SEUIL_ARRET)) { LbEnd(g_lb[s], "500"); continue; }
      }
      double amount = g_lb[s].target - g_lb[s].init;
      if(g_lb[s].nPos > 0 && PBool(P_EARLY) && fl > 0 && eq - g_lb[s].init >= amount)
         LbCloseMarket(g_lb[s], bid, ask);           // si le solde n'atteint pas la cible, la série continue (comme FinishGoal)
      if(g_lb[s].nPos == 0 && g_lb[s].bal + 0.005 >= g_lb[s].target) { LbEnd(g_lb[s], "atteint"); continue; }
      if(g_lb[s].nPos == 0 && g_lb[s].cutPend) { LbEnd(g_lb[s], "coupe"); continue; }
      //--- grille (positions ouvertes)
      if(g_lb[s].nPos > 0)
      {
         bool spreadOK = true;                       // comme OnTick : pas de nouvel ordre de grille si le spread est trop large
         if(LbOn(g_lb[s], 8))
         {
            double sa = (g_lb[s].mode[8] == LBM_ON) ? g_lb[s].adj[8] / 100.0 : 0.0;
            spreadOK = (mk_spread <= PInt(P_MAXSPREAD) * (1.0 + sa));
         }
         if(armed && spreadOK) LbGrid(g_lb[s], bid, ask);
         continue;
      }
      //--- nouvelle série
      if(!armed || g_lb[s].cutPend || !g_lb[s].ok) continue;
      if(PBool(P_SEUIL_ON) && g_lb[s].bal + 0.005 >= PDbl(P_SEUIL_ARRET)) continue;
      int sg = 0;
      if(g_lb[s].hF != INVALID_HANDLE || g_lb[s].hS != INVALID_HANDLE)   // 7.7.0 : EMA propres au scénario
      {
         double f, sl;
         if(ReadEMAsH(g_lb[s].hF != INVALID_HANDLE ? g_lb[s].hF : g_hFast, g_lb[s].hS != INVALID_HANDLE ? g_lb[s].hS : g_hSlow, BarShift(), f, sl))
            sg = (f > sl) ? 1 : ((f < sl) ? -1 : 0);
      }
      else
      {
         if(!sigRead)
         {
            double f, sl;
            sigRead = true;
            if(ReadEMAs(BarShift(), f, sl)) sig = (f > sl) ? 1 : ((f < sl) ? -1 : 0);
         }
         sg = sig;
      }
      if(sg == 0) continue;
      LbOpen(g_lb[s], sg, bid, ask);
   }
}

//+------------------------------------------------------------------+
//| Journal des blocages réels                                       |
//+------------------------------------------------------------------+
void LrReset()
{
   g_lrOn = false; g_lrT0 = 0; g_lrEntry = 0; g_lrSlot = ""; g_lrInit = 0;
   g_lrWait = 0; g_lrSig = 0; g_lrBlk = 0; g_lrOther = 0;
   for(int i = 0; i < LB_NF; i++) { g_lrSec[i] = 0; g_lrSole[i] = 0; }
}

void LrTick()
{
   if(!g_lrOn)
   {
      if(!g_goalActive) return;
      LrReset();
      g_lrOn = true;
      g_lrT0 = TimeGMT();
      g_lrSlot = g_slotGoalName;
      g_lrInit = g_goalInit;
   }
   if(!g_goalActive)
   {
      string f = "{";
      for(int i = 0; i < LB_NF; i++)
         f += (i ? "," : "") + "\"" + g_lbCode[i] + "\":[" + IntegerToString(g_lrSec[i]) + "," + IntegerToString(g_lrSole[i]) + "]";
      f += "}";
      string on = "";
      for(int i = 0; i < LB_NF; i++) on += (i ? "," : "") + (FbOn(i) ? "on" : "off");
      double bal = AccountInfoDouble(ACCOUNT_BALANCE);
      EvTyped("LABO_REEL", 0, "", "{" + LbJ("creneau", g_lrSlot, true) + "," + LbJ("debut", IntegerToString((long)g_lrT0)) + "," +
              LbJ("fin", IntegerToString((long)TimeGMT())) + "," + LbJ("atteint", BoolS(g_goalDone)) + "," +
              LbJ("depart", NumS(g_lrInit)) + "," + LbJ("solde", NumS(bal)) + "," + LbJ("profit", NumS(bal - g_lrInit)) + "," +
              LbJ("entree_s", IntegerToString(g_lrEntry > 0 ? (long)(g_lrEntry - g_lrT0) : -1)) + "," +
              LbJ("attente_s", IntegerToString(g_lrWait)) + "," + LbJ("signal_s", IntegerToString(g_lrSig)) + "," +
              LbJ("bloque_s", IntegerToString(g_lrBlk)) + "," + LbJ("autre_s", IntegerToString(g_lrOther)) + "," +
              LbJ("spec", on, true) + "," + LbJ("filtres", f) + "}");
      LrReset();
      g_lbDirty = true;
      return;
   }
   if(st_posCount > 0) { if(g_lrEntry == 0) g_lrEntry = TimeGMT(); return; }
   if(!g_enabled) return;                            // robot en pause : rien n'est mesuré
   g_lrWait++;
   int dir = (mk_fast > 0 && mk_slow > 0) ? ((mk_fast > mk_slow) ? 1 : ((mk_fast < mk_slow) ? -1 : 0)) : 0;
   if(dir == 0) return;
   g_lrSig++;
   int nb = 0, last = -1;
   for(int i = 0; i < LB_NF; i++)
      if(FbOn(i) && !FbOK(i)) { g_lrSec[i]++; nb++; last = i; }
   if(nb == 1) g_lrSole[last]++;
   if(nb > 0) g_lrBlk++;
   else if(g_blockReason != "") g_lrOther++;
}

//+------------------------------------------------------------------+
//| Persistance                                                      |
//+------------------------------------------------------------------+
string LbD(double v) { return DoubleToString(v, 8); }

void LaboSave()
{
   if(!g_lbOn) return;
   string t = "v=1\nreset=" + g_lbReset + "\nslip=";
   for(int h = 0; h < 25; h++) t += (h ? ";" : "") + LbD(g_lbSlip[h]) + ":" + IntegerToString(g_lbSlipN[h]);
   t += "\ncomm=" + LbD(g_lbComm) + ":" + IntegerToString(g_lbCommN);
   t += "\nlr=" + (g_lrOn ? "1" : "0") + "|" + IntegerToString((long)g_lrT0) + "|" + IntegerToString((long)g_lrEntry) + "|" + g_lrSlot + "|" +
        LbD(g_lrInit) + "|" + IntegerToString(g_lrWait) + "|" + IntegerToString(g_lrSig) + "|" + IntegerToString(g_lrBlk) + "|" +
        IntegerToString(g_lrOther) + "|";
   for(int i = 0; i < LB_NF; i++) t += (i ? ";" : "") + IntegerToString(g_lrSec[i]) + ":" + IntegerToString(g_lrSole[i]);
   for(int s = 0; s < g_lbN; s++)
   {
      t += "\nS=" + g_lb[s].key + "|" + LbD(g_lb[s].bal) + "|" + LbD(g_lb[s].start) + "|" + LbD(g_lb[s].withdrawn) + "|" +
           LbD(g_lb[s].reloaded) + "|" + IntegerToString(g_lb[s].cycles) + "|" + IntegerToString(g_lb[s].reloads) + "|" +
           (g_lb[s].active ? "1" : "0") + "|" + (g_lb[s].cutPend ? "1" : "0") + "|" + LbD(g_lb[s].init) + "|" + LbD(g_lb[s].target) + "|" +
           LbD(g_lb[s].peak) + "|" + LbD(g_lb[s].maxDD) + "|" + LbD(g_lb[s].minML) + "|" + IntegerToString((long)g_lb[s].t0) + "|" +
           IntegerToString((long)g_lb[s].tEntry) + "|" + g_lb[s].slot + "|" + IntegerToString(g_lb[s].maxPos) + "|" +
           IntegerToString(g_lb[s].nSeries) + "|" + IntegerToString(g_lb[s].dir) + "|";
      for(int k = 0; k < g_lb[s].nPos; k++) t += (k ? ";" : "") + LbD(g_lb[s].pOp[k]) + ":" + LbD(g_lb[s].pLot[k]);
      t += "|";
      for(int k = 0; k < g_lb[s].nPend; k++) t += (k ? ";" : "") + LbD(g_lb[s].qPx[k]) + ":" + LbD(g_lb[s].qLot[k]);
   }
   if(AFileWrite(LbFile(), t + "\n")) g_lbDirty = false;
   else IoWarn("état du labo des filtres");
}

void LbPairs(string s, double &a[], double &b[], int &n, int maxN)
{
   n = 0;
   if(s == "") return;
   string p[];
   int c = StringSplit(s, ';', p);
   for(int i = 0; i < c && n < maxN; i++)
   {
      string q[];
      if(StringSplit(p[i], ':', q) != 2) continue;
      a[n] = StringToDouble(q[0]);
      b[n] = StringToDouble(q[1]);
      n++;
   }
}

void LaboLoadState()
{
   string txt;
   if(!AFileRead(LbFile(), txt)) return;
   StringReplace(txt, "\r", "");
   string lines[];
   int nl = StringSplit(txt, '\n', lines);
   for(int li = 0; li < nl; li++)
   {
      string l = lines[li];
      if(StringFind(l, "reset=") == 0) { g_lbReset = StringSubstr(l, 6); continue; }
      if(StringFind(l, "slip=") == 0)
      {
         double a[25], b[25];
         int n;
         LbPairs(StringSubstr(l, 5), a, b, n, 25);
         for(int h = 0; h < n; h++) { g_lbSlip[h] = a[h]; g_lbSlipN[h] = (int)b[h]; }
         continue;
      }
      if(StringFind(l, "comm=") == 0)
      {
         string q[];
         if(StringSplit(StringSubstr(l, 5), ':', q) == 2) { g_lbComm = StringToDouble(q[0]); g_lbCommN = (int)StringToInteger(q[1]); }
         continue;
      }
      if(StringFind(l, "lr=") == 0)
      {
         string f[];
         if(StringSplit(StringSubstr(l, 3), '|', f) < 10) continue;
         g_lrOn = (f[0] == "1"); g_lrT0 = (datetime)StringToInteger(f[1]); g_lrEntry = (datetime)StringToInteger(f[2]);
         g_lrSlot = f[3]; g_lrInit = StringToDouble(f[4]); g_lrWait = (int)StringToInteger(f[5]); g_lrSig = (int)StringToInteger(f[6]);
         g_lrBlk = (int)StringToInteger(f[7]); g_lrOther = (int)StringToInteger(f[8]);
         double a[LB_NF], b[LB_NF];
         int n;
         LbPairs(f[9], a, b, n, LB_NF);
         for(int i = 0; i < n; i++) { g_lrSec[i] = (int)a[i]; g_lrSole[i] = (int)b[i]; }
         continue;
      }
      if(StringFind(l, "S=") != 0 || g_lbN >= LB_MAXSC) continue;
      string f[];
      if(StringSplit(StringSubstr(l, 2), '|', f) < 22) continue;
      int s = g_lbN;
      LbClearGoal(g_lb[s]);
      g_lb[s].key = f[0]; g_lb[s].name = f[0];
      LbOptsParse(g_lb[s], "");
      g_lb[s].slotKey = 0; g_lb[s].hF = INVALID_HANDLE; g_lb[s].hS = INVALID_HANDLE; g_lb[s].hA = INVALID_HANDLE;
      for(int i = 0; i < LB_NF; i++) { g_lb[s].mode[i] = LBM_SAME; g_lb[s].adj[i] = 0; }
      g_lb[s].bal = StringToDouble(f[1]); g_lb[s].start = StringToDouble(f[2]); g_lb[s].withdrawn = StringToDouble(f[3]);
      g_lb[s].reloaded = StringToDouble(f[4]); g_lb[s].cycles = (int)StringToInteger(f[5]); g_lb[s].reloads = (int)StringToInteger(f[6]);
      g_lb[s].active = (f[7] == "1"); g_lb[s].cutPend = (f[8] == "1"); g_lb[s].init = StringToDouble(f[9]); g_lb[s].target = StringToDouble(f[10]);
      g_lb[s].peak = StringToDouble(f[11]); g_lb[s].maxDD = StringToDouble(f[12]); g_lb[s].minML = StringToDouble(f[13]);
      g_lb[s].t0 = (datetime)StringToInteger(f[14]); g_lb[s].tEntry = (datetime)StringToInteger(f[15]); g_lb[s].slot = f[16];
      g_lb[s].maxPos = (int)StringToInteger(f[17]); g_lb[s].nSeries = (int)StringToInteger(f[18]); g_lb[s].dir = (int)StringToInteger(f[19]);
      int n;
      LbPairs(f[20], g_lb[s].pOp, g_lb[s].pLot, n, LB_MAXPOS);
      g_lb[s].nPos = n;
      LbPairs(f[21], g_lb[s].qPx, g_lb[s].qLot, n, LB_MAXPEND);
      g_lb[s].nPend = n;
      g_lb[s].sumL = 0; g_lb[s].sumPL = 0;
      for(int k = 0; k < g_lb[s].nPos; k++) { g_lb[s].sumL += g_lb[s].pLot[k]; g_lb[s].sumPL += g_lb[s].pOp[k] * g_lb[s].pLot[k]; }
      if(g_lb[s].nPos == 0) { g_lb[s].dir = 0; g_lb[s].nPend = 0; }
      g_lb[s].tpSig = -1;
      g_lbN++;
   }
}

//+------------------------------------------------------------------+
//| Cycle de vie                                                     |
//+------------------------------------------------------------------+
void LbUpdateK()
{
   double tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE), ts = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tv > 0 && ts > 0) g_lbK = tv / ts;
}

void LaboInit()
{
   g_lbOn = g_archiOn || (g_bt && !g_btManual);    // 7.2.0 : aussi dans le backtest du bot ; 7.12.0 : pas dans le testeur lancé à la main
   if(!g_lbOn) return;
   for(int h = 0; h < 25; h++) { g_lbSlip[h] = 0; g_lbSlipN[h] = 0; }
   LrReset();
   g_lbN = 0;
   if(!g_bt)
   {
      // 7.7.0 : en réel, plus de séries virtuelles ni de journal des blocages : slippage et commission seulement
      // (mesurés sur les vrais ordres, repris par les backtests de l'optimiseur)
      LaboLoadState();
      g_lbN = 0;
      LrReset();
      g_lbWanted = false;
      LbUpdateK();
      Log("Labo : mesure du slippage et de la commission (séries virtuelles réservées à l'optimiseur)");
      return;
   }
   LaboLoadScenarios();
   g_lbExtSlow[0] = iMA(_Symbol, PERIOD_M15, PInt(P_SLOW), 0, MODE_EMA, PRICE_CLOSE);   // 7.3.0 : extension M15 / H1 des scénarios
   g_lbExtAtr[0]  = iATR(_Symbol, PERIOD_M15, PInt(P_ATRP));
   g_lbExtSlow[1] = iMA(_Symbol, PERIOD_H1, PInt(P_SLOW), 0, MODE_EMA, PRICE_CLOSE);
   g_lbExtAtr[1]  = iATR(_Symbol, PERIOD_H1, PInt(P_ATRP));
   if(g_bt)
   {
      g_lbWanted = true;                             // le backtest joue toujours les scénarios
      g_lbComm = MathMax(0.0, BtCommissionLot); g_lbCommN = 1;
      g_lbSlip[24] = BtSlippagePts; g_lbSlipN[24] = 1;
   }
   g_lbFileMod = FileGetInteger(LB_SCEN_FILE, FILE_MODIFY_DATE, true);
   LbUpdateK();
   Log("Labo des filtres : " + IntegerToString(g_lbN) + " scénarios virtuels" + (g_lbWanted ? "" : " (en pause)"));
}

void LaboDeinit()
{
   if(!g_lbOn) return;
   LbReleaseHandles();
   if(g_bt) LbEtat();                                // backtest : état final des comptes virtuels
   else LaboSave();
}

// État des comptes virtuels pour le bot (toutes les 30 min)
void LbEtat()
{
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID), ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   string j = "{\"slip\":" + NumS(g_lbSlip[24]) + ",\"slip_n\":" + IntegerToString(g_lbSlipN[24]) + ",\"comm\":" + DoubleToString(g_lbComm, 4) +
              ",\"actif\":" + BoolS(g_lbWanted) + ",\"sc\":[";
   for(int s = 0; s < g_lbN; s++)
   {
      j += (s ? "," : "") + "{" + LbJ("sc", g_lb[s].key, true) + "," + LbJ("nom", LbName(g_lb[s]), true) + "," +
           LbJ("spec", LbSpec(g_lb[s]), true) + "," + LbJ("opts", LbOpts(g_lb[s]), true) + "," + LbJ("solde", NumS(g_lb[s].bal)) + "," +
           LbJ("equity", NumS(g_lb[s].bal + LbFloating(g_lb[s], bid, ask))) + "," + LbJ("depart_labo", NumS(g_lb[s].start)) + "," +
           LbJ("cycles", IntegerToString(g_lb[s].cycles)) + "," + LbJ("retraits", NumS(g_lb[s].withdrawn)) + "," +
           LbJ("recharges", NumS(g_lb[s].reloaded)) + "," + LbJ("actif", BoolS(g_lb[s].active)) + "," +
           LbJ("pos", IntegerToString(g_lb[s].nPos)) + "}";
   }
   EvTyped("LABO_ETAT", 0, "", j + "]}");
}

// 1 Hz (après UpdateFilters) : verdict des filtres, marge, fenêtre d'entrée, journal réel, sauvegardes
void LaboTimer1Hz()
{
   if(!g_lbOn) return;
   static ulong lastFile = 0, lastSave = 0, lastEtat = 0;
   ulong now = GetTickCount64();
   if(!g_bt)                                         // 7.7.0 : réel = mesures seulement (sauvegarde, état pour le bot)
   {
      if(now - lastFile >= 10000) { lastFile = now; LbUpdateK(); }
      if(now - lastSave >= 60000 || (g_lbDirty && now - lastSave >= 5000)) { lastSave = now; LaboSave(); }
      if(lastEtat == 0 || now - lastEtat >= 1800000) { lastEtat = now; LbEtat(); }
      return;
   }
   if(!g_lbWanted) return;
   LbOwnSlots();                                     // 7.7.0 : créneaux propres aux scénarios
   LbEvalAll();
   LbContTick();                                     // 7.8.0 : scénarios en mode continu
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID), ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double so = AccountInfoDouble(ACCOUNT_MARGIN_SO_SO);
   bool soPct = (AccountInfoInteger(ACCOUNT_MARGIN_SO_MODE) == ACCOUNT_STOPOUT_MODE_PERCENT);
   int lim = PInt(P_SL_ENTRY);
   for(int s = 0; s < g_lbN; s++)
   {
      if(!g_lb[s].active) continue;
      //--- fenêtre d'entrée après le créneau (comme SlotEntryTick)
      if(lim > 0 && g_lb[s].tEntry == 0 && g_lb[s].nPos == 0 && g_lb[s].slot != "" && !g_maint && !g_stoppedManually &&
         TimeGMT() - g_lb[s].t0 >= (datetime)lim * 60)
      {
         LbEnd(g_lb[s], "annule");
         continue;
      }
      //--- stop-out (marge du broker)
      if(g_lb[s].nPos > 0 && bid > 0 && ask > 0)
      {
         double m = 0;
         double px = (g_lb[s].dir > 0) ? ask : bid;
         if(OrderCalcMargin(g_lb[s].dir > 0 ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, _Symbol, g_lb[s].sumL, px, m) && m > 0)
         {
            double eq = g_lb[s].bal + LbFloating(g_lb[s], bid, ask);
            double ml = eq / m * 100.0;
            if(g_lb[s].minML == 0 || ml < g_lb[s].minML) g_lb[s].minML = ml;
            bool out = soPct ? (so > 0 && ml <= so) : (eq <= so);
            if(out)
            {
               LbCloseMarket(g_lb[s], bid, ask);
               LbEnd(g_lb[s], "liquide");
            }
         }
      }
   }
}
//+------------------------------------------------------------------+
