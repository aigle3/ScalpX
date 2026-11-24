//+------------------------------------------------------------------+
//|                                                   ScalpX_v5.mq5  |
//| ScalpX v5.0 — architecture multi-comptes (pilotage par fichiers)|
//| Basé sur ScalpX v4.5 : logique de trading INCHANGÉE.            |
//|                                                                  |
//| Fichiers à placer dans le MÊME dossier que ce fichier :          |
//|   SX_Core.mqh      paramètres (registre), persistance, presets   |
//|   SX_Trade.mqh     état, indicateurs, lots/grille/TP, exécution  |
//|   SX_Filters.mqh   filtres (ATR, Prix/EMA, Volume, Heure, M1/M15)|
//|   SX_Goal.mqh      objectif, risque, session, dépôts/retraits    |
//|   SX_Archi.mqh     [ARCHI] fichiers, commandes, états, 500, créneaux|
//|   SX_Commands.mqh  textes et actions des commandes du bot        |
//|   SX_FilterBoard.mqh  tableau des filtres (dont News) + alerte   |
//|   SX_Journal.mqh   rapport du jour, journal des filtres          |
//|   SX_Smart.mqh     alertes, watchdog, news, pause, vacances      |
//|   (8.0.0 : aucun affichage sur le graphique, tout par Telegram)  |
//+------------------------------------------------------------------+
#property copyright   "ScalpX"
#property version     "8.01"
#property description "ScalpX v6.1.0 — EMA 10/20 + grille ATR + TP commun + filtres multi-timeframe, pilotage par bot central (fichiers)"

// [SEUIL500] début : TEST UNIQUEMENT (test B-3 de la checklist). Décommenter pour simuler un glissement de 2.0 (devise du compte)
// sur le solde final de la clôture à 500 → événement CLOTURE_500_SOUS_SEUIL. Laisser commenté en production.
//#define SX_TEST_GLISSEMENT 2.0
// [SEUIL500] fin

//+------------------------------------------------------------------+
//| PARAMÈTRES (valeurs de départ).                                  |
//| Tout est modifiable ensuite depuis le bot central (Telegram) ;   |
//| les réglages modifiés sont sauvegardés (voir UseSavedParams).    |
//+------------------------------------------------------------------+
input group "══ Signal & timeframe ══"
input ENUM_TIMEFRAMES TradingTimeframe = PERIOD_M5;   // Timeframe du robot (EMA, ATR, filtres) — indépendant du graphique
input int    SignalBar = 0;                           // Bougie utilisée : 0 = en cours (v3.3), 1 = clôturée (signal stable)
input int    FastEMA = 10;                            // Période EMA rapide
input int    SlowEMA = 20;                            // Période EMA lente
input int    ATRPeriod = 14;                          // Période ATR

input group "══ Lots & grille ══"
input double LotSize = 0.01;                          // Taille du lot
input int    TradesBeforeMultiply = 6;                // Éléments avant multiplication
input double LotMultiplier = 1.4;                     // Multiplicateur de lot
input int    GridStep = 150;                          // Distance de grille (points)
input double GridStepMultiplier = 1.0;                // Multiplicateur de distance de grille
input bool   UseDynamicGrid = true;                   // Grille dynamique (ATR) : remplace la distance fixe
input double GridATRMultiplier = 0.5;                 // Grille dynamique : distance = ATR x ce coefficient
input int    GridMinPoints = 100;                     // Grille dynamique : distance minimum (points)
input int    GridMaxPoints = 500;                     // Grille dynamique : distance maximum (points)
input int    MaxPendingOrders = 20;                   // Ordres limites en attente max
input int    MaxSpread = 50;                          // Spread max (points) pour les nouvelles entrées
input bool   UseWeightedAverage = true;               // Prix moyen pondéré par les lots

input group "══ Take profit ══"
input bool   UseDynamicTP = true;                     // TP dynamique (ATR)
input double ATRMultiplier = 1.5;                     // Multiplicateur ATR
input double AvgTakeProfit = 300.0;                   // TP fixe (points) si TP dynamique désactivé
input int    TPMinPoints = 10;                        // TP dynamique : minimum (points)
input int    TPMaxPoints = 100;                       // TP dynamique : maximum (100 = comportement réel de la v3.3)

input group "══ Filtres ══"
input bool   UseVolatilityFilter = true;              // Filtre volatilité (ATR)
input double MinATRForVolatility = 250.0;             // ATR minimum (points)
input double MaxATRForVolatility = 800.0;             // ATR maximum (points)
input bool   UsePriceEMAFilter = true;                // Filtre Prix / EMA
input bool   VolumeFilterEnabledAtStart = true;       // Filtre de volume (activé par défaut)
input bool   NewsPauseEnabledAtStart = true;          // [ARCHI] Pause auto sur les news (activée par défaut)
input double VolumeMinRatio = 0.7;                    // Volume min (x moyenne)
input double VolumeMaxRatio = 2.5;                    // Volume max (x moyenne)
input int    VolumeBars = 20;                         // Bougies pour la moyenne de volume
// [ARCHI] début : 6.6.0 filtres d'entrée (désactivés par défaut : comportement inchangé tant qu'on ne les active pas)
input bool   UseExtensionFilter = false;              // Filtre d'extension : pas d'entrée si le prix est trop loin de l'EMA lente
input double ExtensionMaxATR = 2.0;                   // Extension maximum : distance prix – EMA lente, en x ATR
input bool   UseFreshSignalFilter = false;            // Filtre signal récent : entrée seulement peu après le croisement des EMA
input int    FreshSignalMaxBars = 12;                 // Signal récent : bougies maximum depuis le croisement des EMA
// [ARCHI] fin

input group "══ Filtres multi-timeframe (v4.1) ══"
input bool   UseLowerTFFilter = true;                 // Filtre EMA timeframe inférieur (réaction rapide)
input ENUM_TIMEFRAMES LowerTimeframe = PERIOD_M1;     // Timeframe inférieur
input int    LowerFastEMA = 10;                       // Inférieur : EMA rapide
input int    LowerSlowEMA = 20;                       // Inférieur : EMA lente
input int    LowerSignalBar = 0;                      // Inférieur : 0 = bougie en cours, 1 = clôturée
input bool   UseUpperTFFilter = true;                 // Filtre EMA timeframe supérieur (tendance de fond)
input ENUM_TIMEFRAMES UpperTimeframe = PERIOD_M15;    // Timeframe supérieur
input int    UpperFastEMA = 10;                       // Supérieur : EMA rapide
input int    UpperSlowEMA = 20;                       // Supérieur : EMA lente
input int    UpperSignalBar = 1;                      // Supérieur : 0 = bougie en cours, 1 = clôturée
input bool   UseUpperCandleFilter = true;             // Filtre direction de la bougie précédente (timeframe supérieur)
input double UpperCandleMinBody = 0.3;                // Corps minimum de cette bougie (x ATR du timeframe supérieur)
input bool   TradingBlockEnabledAtStart = false;      // Filtre horaire (désactivé par défaut)
input int    TradingBlockStartHour = 13;              // Début du blocage (h)
input int    TradingBlockEndHour = 19;                // Fin du blocage (h)
input bool   TimeBlockUseServer = false;              // true = heure serveur, false = heure du fuseau (Réglages > Horaire)

input group "══ Risque ══"
input double DrawdownThreshold = 30.0;                // Seuil de drawdown (%)
input bool   AutoStopOnDrawdown = false;              // Pause auto sur drawdown
input bool   UseEmergencyStopLoss = false;            // Stop loss d'urgence (sur l'EQUITY)
input double EmergencyStopLossPct = 10.0;             // SL d'urgence (% de la balance initiale de l'objectif)
input bool   UseSessionLossLimit = false;             // Limite de perte de session
input double MaxSessionLossPct = 15.0;                // Perte de session max (%)

input group "══ Objectifs & exécution ══"
input bool   UseEarlyGoalClose = true;                // Clôture anticipée de l'objectif
input double GoalMaxAmount = 50.0;                    // Plafond d'un objectif exprimé en montant
input double ProfitNotifyAmount = 0.0;                // Alerte profit cumulé (0 = off)
input int    Deviation = 50;                          // Déviation max des ordres (points)
input int    MaxRetries = 2;                          // Nouvelles tentatives sur requote/timeout
input long   MagicNumber = 123456;                    // Magic Number

input group "══ Notifications (v5 : envoyées au bot central) ══"
input bool   NotifyFilters = false;                   // Alertes de filtres
input bool   NotifyProgress = true;                   // Alertes de progression (25/50/75 %)
input bool   NotifyErrors = true;                     // Alertes d'erreurs d'ordres

input group "══ Divers ══"
input bool   UseSavedParams = true;                   // Reprendre les réglages modifiés depuis le bot
input bool   VerboseLogs = false;                     // Journal détaillé

// [ARCHI] début
input group "══ Architecture multi-comptes (v5) ══"
input bool   ActiverSeuil500 = true;                  // Clôture anticipée à SeuilArret (solde + flottant de l'EA)
input double SeuilArret = 500.0;                      // Seuil d'arrêt (devise du compte)
input double SeuilReprise = 125.0;                    // Solde minimum pour trader / reprendre après retrait (devise du compte)
input bool   ObjectifAutoCreneaux = true;             // Objectif automatique aux créneaux horaires (valeur de départ : modifiable ensuite depuis le bot)
input double ObjectifAutoPct = 1.0;                   // Objectif automatique : gain en % du solde au créneau
input string ObjectifAutoHeures = "3,6,9,12,17,20";   // Créneaux de départ : « H » ou « H:MM » (MM = 00, 15, 30 ou 45), séparés par des virgules ; lundi à vendredi, heures du fuseau réglé dans l'EA
input int    ObjectifAutoFenetreMin = 10;             // Fenêtre de lancement après l'heure du créneau (5, 10 ou 15 min) ; au-delà : créneau perdu
input int    ObjectifAutoEntreeMin = 0;               // 6.6.0 : fenêtre d'entrée (min) : sans série ouverte dans ce délai après le créneau, l'objectif est annulé (0 = désactivé)
input bool   ObjectifContinu = false;                // 7.8.0 : mode continu : objectif lancé dès que tous les filtres sont OK à la clôture d'une bougie M5 (sans créneaux)
input bool   ObjectifCoupureActive = true;            // 7.0.0 : coupure de l'objectif aux heures ci-dessous
input string ObjectifCoupureHeures = "13:45,21";      // 7.0.0 : heures de coupure (« H » ou « H:MM », MM = 00/15/30/45) : sans position, l'objectif est annulé ; avec une grille, elle finit au TP puis l'objectif est annulé
input bool   FermerAutresGraphiques = true;              // Ferme les autres graphiques du terminal au démarrage (un compte = un graphique)
// [ARCHI] fin

// [ARCHI] début : 7.2.0 backtest piloté par le bot (ignorés hors du testeur de stratégie)
input group "══ Backtest (bot) ══"
input string BacktestId = "";                         // Identifiant du backtest (rempli par le bot) ; vide = testeur lancé à la main
input bool   BtPresetBot = true;                      // Testeur à la main : réglages du preset ⭐ du bot (Common\Files\scalpx_preset.cfg) ; false = paramètres du test
input int    BtGmtHiver = -99;                        // Décalage GMT du serveur en hiver (h) ; -99 = celui du calendrier exporté
input bool   BtHeureEteUS = true;                     // Le serveur suit l'heure d'été américaine (+1 h)
input double BtCommissionLot = 0.0;                   // Labo : commission par lot et par côté (mesurée en réel)
input double BtSlippagePts = 0.0;                     // Labo : slippage moyen en points (mesuré en réel)
// [ARCHI] fin

// [ARCHI] début : 7.3.0 réglages des filtres d'extension et de signal récent (valeurs par défaut = comportement inchangé)
input group "══ Filtres : extension et signal récent ══"
input int    FreshSignalIgnoreBars = 0;               // Signal récent : recroisements des EMA de N bougies ou moins ignorés (0 = aucun : comportement d'avant)
input int    ExtensionTimeframe = 0;                  // Extension mesurée sur : 0 = TF du robot, 1 = M1, 2 = M5, 3 = M15, 4 = M30, 5 = H1, 6 = H4
// [ARCHI] fin

//+------------------------------------------------------------------+
//| MODULES                                                          |
//+------------------------------------------------------------------+
#include "SX_Core.mqh"
#include "SX_Trade.mqh"
#include "SX_Filters.mqh"
#include "SX_Goal.mqh"
#include "SX_Archi.mqh"
#include "SX_Commands.mqh"
#include "SX_FilterBoard.mqh"
#include "SX_Journal.mqh"
#include "SX_Smart.mqh"
#include "SX_Labo.mqh"
#include "SX_Backtest.mqh"             // [ARCHI] 7.2.0 : backtest piloté par le bot                 // [ARCHI] 7.1.0 : labo des filtres (séries virtuelles, aucun ordre réel)

//+------------------------------------------------------------------+
//| Initialisation                                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   BtPreInit();                       // [ARCHI] 7.2.0 : backtest du bot ? (horloge simulée, identifiant bt_<id>)
   Log("Initialisation ScalpX " + SX_VERSION + " sur " + _Symbol);
   g_lastTickMs = GetTickCount64();   // [ARCHI] 8.1.1 : départ du chrono « aucun tick » (avant : jamais d'alerte si aucun tick depuis le démarrage)
   InitParams();
   if(UseSavedParams && !g_btManual)  // [ARCHI] 7.12.0 : testeur lancé à la main : uniquement les paramètres du test (.set)
   {
      int n = LoadParamsFrom(CfgName());
      if(n > 0) Log("Réglages restaurés depuis le fichier (" + IntegerToString(n) + " paramètres)");
   }
   // cohérence minimale (fichier édité à la main)
   if(g_pVal[P_FAST] >= g_pVal[P_SLOW]) { g_pVal[P_FAST] = g_pDef[P_FAST]; g_pVal[P_SLOW] = g_pDef[P_SLOW]; }
   if(!SymbolSelect(_Symbol, true)) Log("Avertissement : symbole non sélectionnable dans la Market Watch");

   if(!InitIndicators()) return INIT_FAILED;
   InitFilling();

   // état persistant
   g_stoppedManually = (GlobalVariableCheck(GVN("stopped")) && GlobalVariableGet(GVN("stopped")) > 0);
   GoalLoad();
   g_configOK = (GlobalVariableCheck(GVN("cfgok")) && GlobalVariableGet(GVN("cfgok")) > 0);
   if(!g_goalActive) g_configOK = false;
   // [ARCHI] début
   ArchiPreInit();                    // paramètres validés par défaut (robot autonome)
   BtInit();                          // 7.2.0 : backtest du bot : réglages du compte, créneaux, calendrier des news
   // [ARCHI] fin
   SessionInit();
   RefreshState();
   UpdateFilters();
   JournalInit();

   // [ARCHI] début : le robot tournait avant le rechargement / redémarrage → il reprend TOUT DE SUITE
   // (6.0.0 : plus de délai de reprise ; les filtres décident ensuite des entrées comme d'habitude)
   bool resumed = false;
   if(GlobalVariableCheck(GVN("run")) && GlobalVariableGet(GVN("run")) > 0 && !g_stoppedManually && g_goalActive && g_configOK)
   {
      g_enabled = true;
      resumed = true;
      Log("Robot relancé alors qu'il tournait : reprise immédiate");
   }
   // [ARCHI] fin
   GlobalVariableDel(GVN("run"));
   SmartInit();                       // mode vacances et pause programmée restaurés après la reprise automatique

   if(ChartPeriod(0) != g_sigTF)
      Log("Info : le graphique est en " + EnumToString(ChartPeriod(0)) + " mais le robot travaille en " + TfLabel() + " (c'est volontaire, indépendant).");

   // [ARCHI] début
   ArchiInit();                       // état, créneaux, premier statut, événement DEMARRAGE_EA
   LaboInit();                        // 7.1.0 : labo des filtres (état virtuel restauré)
   if(resumed && g_enabled)           // information silencieuse : le robot a repris sans attendre
      EvTyped("REPRISE_IMMEDIATE", 0, "▶️ Terminal relancé : le robot tournait, il a repris tout de suite." +
              "\n🎯 Objectif : " + Sgn(GoalAmount()) + " " + Cur() + " | progression " + F0(GoalProgressPct()) + " %");
   // [ARCHI] fin

   ChartSetInteger(0, CHART_SHOW, false);   // [ARCHI] 8.0.0 : graphique masqué (tout passe par Telegram : aucun dessin)
   if(!g_bt) EventSetMillisecondTimer(250);   // [ARCHI] 7.2.0 : backtest du bot : tâches de la seconde lancées par OnTick (temps simulé)
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Arrêt                                                            |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   bool reload = (reason == REASON_PARAMETERS || reason == REASON_CHARTCHANGE ||
                  reason == REASON_RECOMPILE  || reason == REASON_TEMPLATE || reason == REASON_ACCOUNT);
   GlobalVariableSet(GVN("run"), (g_enabled && g_goalActive) ? 1 : 0);
   GlobalVariableSet(GVN("rsn"), reload ? 1 : 0);
   EventKillTimer();
   SaveParams();

   // [ARCHI] début
   LaboDeinit();                      // 7.1.0 : sauvegarde des comptes virtuels du labo
   BtDeinit(reason);                  // 7.2.0 : résultat final du backtest
   ArchiDeinit(reason);
   // [ARCHI] fin
   if(!reload) ChartSetInteger(0, CHART_SHOW, true);   // [ARCHI] 8.0.0 : EA retiré, graphique rendu
   ReleaseIndicators();
}

//+------------------------------------------------------------------+
//| Boucle de trading : uniquement des tâches rapides et locales.    |
//| (commandes, filtres, graphique = OnTimer)                         |
//+------------------------------------------------------------------+
void OnTick()
{
   g_lastTickMs = GetTickCount64();
   if(g_bt) BtTick();                 // [ARCHI] 7.2.0 : backtest du bot : une fois par seconde simulée
   if(g_closing) return;
   RefreshState();
   mk_spread = (double)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   ulong now = GetTickCount64();
   // [ARCHI] début : 7.1.0 labo des filtres : séries VIRTUELLES (aucun ordre, aucun effet sur le trading réel)
   LaboTick();
   // [ARCHI] fin

   //--- 1) Objectif et protections (très légers)
   GoalTick();
   if(g_closing) return;
   // [SEUIL500] début
   Seuil500Tick();                    // clôture anticipée à SeuilArret (même endroit et même fréquence que l'objectif)
   if(g_closing) return;
   // [SEUIL500] fin
   RiskTick();
   if(g_closing) return;
   VacationTick();                    // plafond de risque du mode vacances
   if(g_closing) return;
   RefreshState();
   JournalTick();
   DayDDTrack();

   //--- 2) Sorties : TP commun (actif même en pause)
   if(st_posCount > 0)
   {
      ManageTP();
      if(CheckTPExit()) return;
   }

   //--- 3) Nettoyage après objectif terminé
   static ulong lastClean = 0;
   if(g_goalDone && !g_goalActive && (st_posCount + st_pendCount) > 0 && now >= g_cooldownUntilMs && now - lastClean > 5000)
   {
      lastClean = now;
      CloseAllFast("nettoyage après objectif");
      return;
   }

   //--- 4) Entrées et grille : seulement si le robot est actif
   if(!g_enabled || !g_goalActive || !g_configOK) return;
   if(now < g_cooldownUntilMs) return;
   if(!f_marketOpen) return;
   string w;
   if(!TradeEnvOK(w)) return;
   if(mk_spread > PInt(P_MAXSPREAD)) return;     // le spread ne bloque QUE les nouveaux ordres (les sorties restent actives)

   if(st_posCount > 0)
   {
      // [ARCHI] début
      if(!PeutTrader()) return;       // pas de nouveaux ordres de grille (la gestion des positions ouvertes est faite plus haut)
      // [ARCHI] fin
      if(!g_vacation && st_pendCount < PInt(P_MAXPEND) && now >= g_nextGridMs) PlaceGridBatch();
      return;
   }

   // aucune position : les ordres orphelins sont supprimés
   if(st_pendCount > 0)
   {
      static ulong lastCancel = 0;
      if(now - lastCancel > 1000) { lastCancel = now; CancelPendingFast(); }
      return;
   }

   //--- 5) Nouvelle série
   string why;
   if(!EntryAllowed(why)) return;
   int sig = Signal();
   if(sig == 0) return;
   if(!MtfEntryOK(sig, why)) return;     // filtres multi-timeframe revérifiés avec la direction exacte du signal
   // [ARCHI] début
   if(!PeutTrader()) return;
   // [ARCHI] fin
   if(SendMarketEntry(sig > 0 ? ORDER_TYPE_BUY : ORDER_TYPE_SELL))
   {
      RefreshState();
      ManageTP();
      PlaceGridBatch();
   }
}

//+------------------------------------------------------------------+
//| Tâches périodiques (250 ms)                                      |
//+------------------------------------------------------------------+
void OnTimer()
{
   ulong now = GetTickCount64();

   //--- 1 Hz : état, filtres, raison de blocage, rapports, reprise auto
   static ulong last1s = 0;
   if(now - last1s >= 1000)
   {
      last1s = now;
      RefreshState();
      UpdateFilters();
      string why;
      g_blockReason = EntryAllowed(why) ? "" : why;
      FilterAlertTick();
      JournalTick1Hz();
      SmartTick1Hz();
      LaboTimer1Hz();                    // [ARCHI] 7.1.0 : labo des filtres (mesure seulement)
      BtLiveTick();                      // [ARCHI] 7.2.0 : calendrier des news exporté pour les backtests (1 fois/jour)
      ShotTick();                        // courbe d'equity demandée par le bot
   }

   // [ARCHI] début
   ArchiTimer();                         // commandes (2 s), statut (5 s), créneaux, événements
   // [ARCHI] fin
}

//+------------------------------------------------------------------+
//| Transactions : statistiques instantanées, dépôts/retraits,       |
//| suivi des erreurs des ordres asynchrones                         |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD)
   {
      OnDealAdded(trans.deal);
      LaboOnDeal(trans.deal);            // [ARCHI] 7.1.0 : commission mesurée pour le labo
      BtOnDeal(trans.deal);              // [ARCHI] 7.6.0 : stop-out du broker compté dans le backtest
      return;
   }
   if(trans.type != TRADE_TRANSACTION_REQUEST) return;

   uint rc = result.retcode;
   if(rc == TRADE_RETCODE_DONE || rc == TRADE_RETCODE_PLACED || rc == TRADE_RETCODE_DONE_PARTIAL)
   {
      // [ARCHI] début : un ordre accepté met fin à un blocage « marché fermé / trading désactivé »
      if(request.symbol == _Symbol && request.magic == MagicNumber) BrokerBlockClear();
      LaboOnRequest(request, result);   // 7.1.0 : slippage réel mesuré pour le labo
      // [ARCHI] fin
      return;
   }
   if(request.symbol != _Symbol || request.magic != MagicNumber) return;

   if(request.action == TRADE_ACTION_PENDING)
   {
      // [ARCHI] début : marché fermé / trading désactivé : essais espacés, un seul message par épisode (pas de rafale)
      if(IsBlockingRc(rc))
      {
         RegisterBrokerBlock("ordre de grille", rc);
         return;
      }
      // [ARCHI] fin
      if(rc == TRADE_RETCODE_INVALID_FILL) g_fillPend = NextFilling(g_fillPend);   // auto-correction
      g_nextGridMs = GetTickCount64() + 3000;
      Log("Ordre de grille refusé : " + TradeErrText(rc));
      static ulong lastNote = 0;
      if(PBool(P_N_ERRORS) && GetTickCount64() - lastNote > 60000 && rc != TRADE_RETCODE_INVALID_FILL)
      {
         lastNote = GetTickCount64();
         TgQueue("⚠️ Ordre de grille refusé\n━━━━━━━━━━━━━━━━━━\n❌ " + TradeErrText(rc) +
                 "\n💵 Marge libre : " + F2(AccountInfoDouble(ACCOUNT_MARGIN_FREE)) + " " + Cur());
      }
   }
   else if(request.action == TRADE_ACTION_SLTP)
      Dbg("Pose du TP refusée : " + TradeErrText(rc) + " (la surveillance du robot prend le relais)");
   else if(request.action == TRADE_ACTION_DEAL)
   {
      Log("Ordre au marché refusé : " + TradeErrText(rc));
      if(IsBlockingRc(rc)) NoteBrokerBlockRc(rc);   // [ARCHI] retenu pour les fermetures (CloseBlockReason)
   }
}

//+------------------------------------------------------------------+
