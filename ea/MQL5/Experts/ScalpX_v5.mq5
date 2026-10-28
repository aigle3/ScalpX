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
//|   SX_Chart.mqh     style, EMA, ATR, niveaux sur le graphique     |
//|   SX_Panel.mqh     panneau d'informations dynamique              |
//+------------------------------------------------------------------+
#property copyright   "ScalpX"
#property version     "5.06"
#property description "ScalpX v5.6.1 — EMA 10/20 + grille ATR + TP commun + filtres multi-timeframe, pilotage par bot central (fichiers)"

// [SEUIL500] début : TEST UNIQUEMENT (test B-3 de la checklist). Décommenter pour simuler un glissement de 2.0 (devise du compte)
// sur le solde final de la clôture à 500 → événement CLOTURE_500_SOUS_SEUIL. Laisser commenté en production.
//#define SX_TEST_GLISSEMENT 2.0
// [SEUIL500] fin

//+------------------------------------------------------------------+
//| PARAMÈTRES (valeurs de départ).                                  |
//| Tout est modifiable ensuite depuis le bot central ou le panneau ;|
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
input int    AutoResumeMinutes = 20;                  // Reprise auto après retrait/rattachement (0 = off)
input long   MagicNumber = 123456;                    // Magic Number

input group "══ Notifications (v5 : envoyées au bot central) ══"
input bool   NotifyFilters = true;                    // Alertes de filtres
input bool   NotifyProgress = true;                   // Alertes de progression (25/50/75 %)
input bool   NotifyErrors = true;                     // Alertes d'erreurs d'ordres

input group "══ Graphique ══"
input bool   ChartModernStyle = true;                 // Thème moderne (sans grille ni volumes)
input bool   ChartShowEMA = true;                     // Tracer les EMA
input bool   ChartShowLevels = true;                  // Lignes prix moyen / TP / grille
input bool   ChartShowPanel = true;                   // Panneau d'informations

input group "══ Divers ══"
input bool   UseSavedParams = true;                   // Reprendre les réglages modifiés depuis le bot
input bool   VerboseLogs = false;                     // Journal détaillé

// [ARCHI] début
input group "══ Architecture multi-comptes (v5) ══"
input bool   ActiverSeuil500 = true;                  // Clôture anticipée à SeuilArret (solde + flottant de l'EA)
input double SeuilArret = 500.0;                      // Seuil d'arrêt (devise du compte)
input double SeuilReprise = 125.0;                    // Solde minimum pour trader / reprendre après retrait (devise du compte)
input bool   ObjectifAutoCreneaux = true;             // Objectif automatique aux créneaux horaires
input double ObjectifAutoPct = 1.0;                   // Objectif automatique : gain en % du solde au créneau
input string ObjectifAutoHeures = "3,6,9,12,17,20";   // Créneaux (heures du fuseau réglé dans l'EA), lundi à vendredi
input int    ObjectifAutoFenetreMin = 5;              // Fenêtre de lancement après l'heure pile (min) ; au-delà : créneau perdu
input bool   FermerAutresGraphiques = true;              // Ferme les autres graphiques du terminal au démarrage (un compte = un graphique)
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
#include "SX_Chart.mqh"
#include "SX_Panel.mqh"

//+------------------------------------------------------------------+
//| Initialisation                                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   Log("Initialisation ScalpX " + SX_VERSION + " sur " + _Symbol);
   InitParams();
   if(UseSavedParams)
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
   // [ARCHI] fin
   SessionInit();
   RefreshState();
   UpdateFilters();
   JournalInit();

   // reprise automatique si le robot tournait avant le rechargement / retrait
   if(GlobalVariableCheck(GVN("run")) && GlobalVariableGet(GVN("run")) > 0 && !g_stoppedManually && g_goalActive && g_configOK)
   {
      bool reload = (GlobalVariableGet(GVN("rsn")) > 0);
      if(reload) { g_enabled = true; Log("Rechargement détecté : reprise immédiate du robot"); }
      else if(PInt(P_AUTORESUME) > 0)
      {
         g_resumeAt = TimeCurrent() + PInt(P_AUTORESUME) * 60;
         g_resumeFrom = LocalNow();
         Log("Robot rattaché : reprise automatique dans " + IntegerToString(PInt(P_AUTORESUME)) + " min");
      }
   }
   GlobalVariableDel(GVN("run"));
   SmartInit();                       // mode vacances et pause programmée restaurés après la reprise automatique

   if(ChartPeriod(0) != g_sigTF)
      Log("Info : le graphique est en " + EnumToString(ChartPeriod(0)) + " mais le robot travaille en " + TfLabel() + " (c'est volontaire, indépendant).");

   // [ARCHI] début
   ArchiInit();                       // état, créneaux, premier statut, événement DEMARRAGE_EA
   if(g_resumeAt > 0)                 // le robot tournait : annonce de la reprise automatique (annulable par ⏸ Pause)
      EvTyped("REPRISE_PROGRAMMEE", 1, "🔌 TERMINAL RELANCÉ — REPRISE AUTOMATIQUE À " + Hh(ZoneFromServer(g_resumeAt)) +
              "\n━━━━━━━━━━━━━━━━━━\nLe robot tournait quand le terminal (ou l'EA) a été relancé à " + Hh(g_resumeFrom) + "." +
              "\n🎯 Objectif en cours : " + Sgn(GoalAmount()) + " " + Cur() + " | progression " + F0(GoalProgressPct()) + " %" +
              "\n⏳ Il reprend tout seul dans " + IntegerToString(PInt(P_AUTORESUME)) + " min (réglage « Reprise auto après retrait »)." +
              "\n⏸ Pause pour l'en empêcher.", "{\"a\":\"" + TimeToString(ZoneFromServer(g_resumeAt), TIME_DATE | TIME_MINUTES) + "\"}");
   // [ARCHI] fin

   ChartRefreshAll(true);
   PanelUpdate();
   EventSetMillisecondTimer(250);
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
   ArchiDeinit(reason);
   // [ARCHI] fin
   ChartShutdown(!reload);
   ReleaseIndicators();
}

//+------------------------------------------------------------------+
//| Boucle de trading : uniquement des tâches rapides et locales.    |
//| (commandes, filtres, graphique = OnTimer)                         |
//+------------------------------------------------------------------+
void OnTick()
{
   g_lastTickMs = GetTickCount64();
   if(g_closing) return;
   RefreshState();
   mk_spread = (double)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   ulong now = GetTickCount64();

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
      ChartDrawEMAs(false);

      if(g_resumeAt > 0)
      {
         if(g_enabled || g_stoppedManually || !g_goalActive) g_resumeAt = 0;
         else if(TimeCurrent() >= g_resumeAt)
         {
            g_resumeAt = 0;
            g_enabled = true;
            TgQueue("▶️ REPRISE AUTOMATIQUE\n━━━━━━━━━━━━━━━━━━\nLe terminal (ou l'EA) avait été relancé à " + Hh(g_resumeFrom) +
                    " alors que le robot tournait : le délai de " + IntegerToString(PInt(P_AUTORESUME)) + " min est écoulé, il reprend son travail." +
                    "\n🎯 Objectif : " + Sgn(GoalAmount()) + " " + Cur() + " | progression " + F0(GoalProgressPct()) + " %");
         }
      }
      ShotTick();                        // captures demandées par le bot
      if(!PBool(P_CH_PANEL)) ChartRedraw(0);
   }

   //--- Graphique (4 Hz, seulement si changement)
   static ulong lastLv = 0;
   if(now - lastLv >= 500) { lastLv = now; ChartDrawLevels(); }
   PanelUpdate();

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
      return;
   }
   if(trans.type != TRADE_TRANSACTION_REQUEST) return;

   uint rc = result.retcode;
   if(rc == TRADE_RETCODE_DONE || rc == TRADE_RETCODE_PLACED || rc == TRADE_RETCODE_DONE_PARTIAL) return;
   if(request.symbol != _Symbol || request.magic != MagicNumber) return;

   if(request.action == TRADE_ACTION_PENDING)
   {
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
      Log("Ordre au marché refusé : " + TradeErrText(rc));
}

//+------------------------------------------------------------------+
//| Clics sur le panneau                                             |
//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   if(id == CHARTEVENT_OBJECT_CLICK) PanelOnClick(sparam);
}
//+------------------------------------------------------------------+
