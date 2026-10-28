//+------------------------------------------------------------------+
//| SX_Archi.mqh - ScalpX v5.0                                       |
//| [ARCHI] Architecture multi-comptes : remplace le transport       |
//| Telegram par des fichiers (Common\Files), pilotés par le bot     |
//| Python central.                                                  |
//|                                                                  |
//|  - statut_<id>.json   heartbeat (5 s)                            |
//|  - cmd_<id>_<n>.json  commandes (écrites par le bot)             |
//|  - ack_<id>_<n>.json  accusés de réception                       |
//|  - events_<id>.jsonl  événements / notifications                 |
//|  - etat_<id>.json     copie de secours de l'état et du cycle     |
//|                                                                  |
//|  - machine à états : ACTIF / ARRET_500 / PAUSE (+ phases en      |
//|    mémoire : CLOTURE 500, MAINTENANCE)                           |
//|  - clôture anticipée à SeuilArret (SEUIL500)                     |
//|  - objectif automatique +N % aux créneaux horaires               |
//|                                                                  |
//| Toutes les écritures sont atomiques (.tmp puis FileMove).        |
//| Tout est désactivé dans le Strategy Tester.                      |
//+------------------------------------------------------------------+

#define ST_ACTIF   0
#define ST_ARRET   1
#define ST_PAUSE   2

//--- variables d'architecture
string   g_aid          = "";          // identifiant : login_serveur normalisé
bool     g_archiOn      = false;       // false dans le Strategy Tester
int      g_state        = ST_ACTIF;    // état persisté
int      g_cycle        = 0;           // compteur de cycles (persisté)
bool     g_phase500     = false;       // phase CLÔTURE 500 (mémoire seulement)
bool     g_maint        = false;       // verrou MAINTENANCE (mémoire seulement)
bool     g_capSent      = false;       // CAPITAL_INSUFFISANT déjà émis
long     g_evSeq        = 0;
ulong    g_phaseRetryMs = 0;
ulong    g_termCloseAt  = 0;           // fermeture du terminal demandée (ms)
int      g_hours[];                    // heures des créneaux (triées)
int      g_hoursN       = 0;
long     g_lastSlotKey  = 0;
string   g_evPend[];                   // événements non écrits (fichier verrouillé), réessayés au tick suivant
string   g_badName[];                  // commandes illisibles (compteur d'échecs)
int      g_badCnt[];

//+------------------------------------------------------------------+
//| Utilitaires texte / JSON                                         |
//+------------------------------------------------------------------+
string JsonEsc(string s)
{
   StringReplace(s, "\\", "\\\\");
   StringReplace(s, "\"", "\\\"");
   StringReplace(s, "\n", "\\n");
   StringReplace(s, "\r", "");
   StringReplace(s, "\t", " ");
   return s;
}

string BoolS(bool b) { return b ? "true" : "false"; }
string NumS(double v) { return DoubleToString(v, 2); }

string NormalizeServer(const string s)
{
   string r = "";
   int n = StringLen(s);
   for(int i = 0; i < n; i++)
   {
      ushort c = StringGetCharacter(s, i);
      bool ok = (c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z');
      r += ok ? ShortToString(c) : "_";
   }
   return r;
}

string StateName(int s)
{
   if(s == ST_ARRET) return "ARRET_500";
   if(s == ST_PAUSE) return "PAUSE";
   return "ACTIF";
}

string FileId(const string kind, const string ext) { return kind + "_" + g_aid + ext; }

int HexVal(ushort c)
{
   if(c >= '0' && c <= '9') return (int)c - '0';
   if(c >= 'a' && c <= 'f') return (int)c - 'a' + 10;
   if(c >= 'A' && c <= 'F') return (int)c - 'A' + 10;
   return 0;
}

bool AllDigits(const string s)
{
   int n = StringLen(s);
   if(n == 0) return false;
   for(int i = 0; i < n; i++)
   {
      ushort c = StringGetCharacter(s, i);
      if(c < '0' || c > '9') return false;
   }
   return true;
}

//--- lecture JSON minimale (objets plats + tableau "args")
int JSkip(const string s, int i)
{
   int n = StringLen(s);
   while(i < n)
   {
      ushort c = StringGetCharacter(s, i);
      if(c == ' ' || c == '\t' || c == '\r' || c == '\n') i++;
      else break;
   }
   return i;
}

// lit une chaîne JSON dont le guillemet ouvrant est à l'index i ; renvoie l'index suivant le guillemet fermant, ou -1
int JReadStr(const string s, int i, string &out)
{
   out = "";
   int n = StringLen(s);
   if(i >= n || StringGetCharacter(s, i) != '"') return -1;
   i++;
   while(i < n)
   {
      ushort c = StringGetCharacter(s, i);
      if(c == '"') return i + 1;
      if(c == '\\')
      {
         i++;
         if(i >= n) return -1;
         ushort e = StringGetCharacter(s, i);
         if(e == 'n') out += "\n";
         else if(e == 't') out += "\t";
         else if(e == 'r') out += "\r";
         else if(e == 'b' || e == 'f') out += " ";
         else if(e == 'u')
         {
            if(i + 4 >= n) return -1;
            int v = 0;
            for(int k = 1; k <= 4; k++) v = v * 16 + HexVal(StringGetCharacter(s, i + k));
            out += ShortToString((ushort)v);
            i += 4;
         }
         else out += ShortToString(e);
      }
      else out += ShortToString(c);
      i++;
   }
   return -1;
}

// index de la valeur associée à la clé (après le ':'), ou -1
int JFindKey(const string s, const string key)
{
   string pat = "\"" + key + "\"";
   int n = StringLen(s);
   int p = StringFind(s, pat);
   while(p >= 0)
   {
      int i = JSkip(s, p + StringLen(pat));
      if(i < n && StringGetCharacter(s, i) == ':') return JSkip(s, i + 1);
      p = StringFind(s, pat, p + 1);
   }
   return -1;
}

string JGetStr(const string s, const string key)
{
   int i = JFindKey(s, key);
   if(i < 0) return "";
   string o = "";
   if(StringGetCharacter(s, i) == '"') { JReadStr(s, i, o); return o; }
   int n = StringLen(s), st = i;
   while(i < n)
   {
      ushort c = StringGetCharacter(s, i);
      if(c == ',' || c == '}' || c == ']' || c == ' ' || c == '\r' || c == '\n') break;
      i++;
   }
   return StringSubstr(s, st, i - st);
}

double JGetNum(const string s, const string key, double def)
{
   string v = JGetStr(s, key);
   if(v == "") return def;
   return StringToDouble(v);
}

// remplit args[] ; renvoie le nombre d'éléments, ou -1 si le tableau est mal formé
int JGetArgs(const string s, string &args[])
{
   ArrayResize(args, 0);
   int i = JFindKey(s, "args");
   if(i < 0) return 0;
   int n = StringLen(s);
   if(i >= n || StringGetCharacter(s, i) != '[') return 0;
   i = JSkip(s, i + 1);
   while(i < n)
   {
      ushort c = StringGetCharacter(s, i);
      if(c == ']') return ArraySize(args);
      if(c == ',') { i = JSkip(s, i + 1); continue; }
      string v = "";
      if(c == '"')
      {
         int j = JReadStr(s, i, v);
         if(j < 0) return -1;
         i = j;
      }
      else
      {
         int st = i;
         while(i < n)
         {
            ushort d = StringGetCharacter(s, i);
            if(d == ',' || d == ']' || d == ' ' || d == '\r' || d == '\n' || d == '}') break;
            i++;
         }
         v = StringSubstr(s, st, i - st);
      }
      int k = ArraySize(args);
      ArrayResize(args, k + 1);
      args[k] = v;
      i = JSkip(s, i);
   }
   return -1;
}

bool JLooksValid(const string raw)
{
   string t = raw;
   StringTrimLeft(t); StringTrimRight(t);
   int n = StringLen(t);
   if(n < 2) return false;
   if(StringGetCharacter(t, 0) != '{' || StringGetCharacter(t, n - 1) != '}') return false;
   return (JFindKey(t, "cmd") >= 0);
}

//+------------------------------------------------------------------+
//| Fichiers (Common\Files, UTF-8 sans BOM, écriture atomique)       |
//+------------------------------------------------------------------+
bool AFileWrite(const string name, const string content)
{
   string tmp = name + ".tmp";
   int h = FileOpen(tmp, FILE_WRITE | FILE_BIN | FILE_COMMON);
   if(h == INVALID_HANDLE) return false;
   uchar a[];
   StringToCharArray(content, a, 0, WHOLE_ARRAY, CP_UTF8);
   int n = ArraySize(a);
   if(n > 0 && a[n - 1] == 0) n--;
   if(n > 0) FileWriteArray(h, a, 0, n);
   FileClose(h);
   if(!FileMove(tmp, FILE_COMMON, name, FILE_COMMON | FILE_REWRITE))
   {
      FileDelete(tmp, FILE_COMMON);
      return false;
   }
   return true;
}

bool AFileRead(const string name, string &out)
{
   out = "";
   int h = FileOpen(name, FILE_READ | FILE_BIN | FILE_COMMON | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE) return false;
   int sz = (int)FileSize(h);
   if(sz > 65536) sz = 65536;
   uchar a[];
   if(sz > 0) FileReadArray(h, a, 0, sz);
   FileClose(h);
   if(sz <= 0) return false;
   out = CharArrayToString(a, 0, sz, CP_UTF8);
   if(StringLen(out) > 0 && StringGetCharacter(out, 0) == 0xFEFF) out = StringSubstr(out, 1);
   return true;
}

bool AFileAppend(const string name, const string line)
{
   int h = FileOpen(name, FILE_READ | FILE_WRITE | FILE_BIN | FILE_COMMON | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE) return false;
   FileSeek(h, 0, SEEK_END);
   uchar a[];
   StringToCharArray(line + "\n", a, 0, WHOLE_ARRAY, CP_UTF8);
   int n = ArraySize(a);
   if(n > 0 && a[n - 1] == 0) n--;
   if(n > 0) FileWriteArray(h, a, 0, n);
   FileClose(h);
   return true;
}

//+------------------------------------------------------------------+
//| Événements (events_<id>.jsonl)                                   |
//+------------------------------------------------------------------+
void EvFlushPending()
{
   int n = ArraySize(g_evPend);
   if(n == 0) return;
   int keep = 0;
   for(int i = 0; i < n; i++)
   {
      if(!AFileAppend(FileId("events", ".jsonl"), g_evPend[i]))
      {
         g_evPend[keep] = g_evPend[i];
         keep++;
      }
   }
   ArrayResize(g_evPend, keep);
}

void EvPush(const string line)
{
   if(ArraySize(g_evPend) == 0 && AFileAppend(FileId("events", ".jsonl"), line)) return;
   int n = ArraySize(g_evPend);
   if(n >= 200)
   {
      for(int i = 1; i < n; i++) g_evPend[i - 1] = g_evPend[i];
      n--;
   }
   ArrayResize(g_evPend, n + 1);
   g_evPend[n] = line;
}

// Événement typé. prio : 0 info, 1 important, 2 critique. dataJson : objet JSON facultatif.
void EvTyped(const string type, int prio, const string text, const string dataJson = "")
{
   if(!g_archiOn) return;
   g_evSeq++;
   string l = "{\"seq\":" + IntegerToString((int)g_evSeq) +
              ",\"ts\":" + IntegerToString((long)TimeGMT()) +
              ",\"id\":\"" + g_aid + "\"" +
              ",\"type\":\"" + type + "\"" +
              ",\"prio\":" + IntegerToString(prio) +
              ",\"silencieux\":" + BoolS(prio <= 0) +
              ",\"texte\":\"" + JsonEsc(text) + "\"";
   if(dataJson != "") l += ",\"data\":" + dataJson;
   l += "}";
   EvPush(l);
}

//--- Messages de l'EA vers le bot : événement NOTIF (prio : 0 info, 1 important, 2 critique)
void TgQueueP(string text, int prio = 1) { EvTyped("NOTIF", prio, text); }
void TgQueue(string text)                { TgQueueP(text, 1); }

//+------------------------------------------------------------------+
//| Persistance de l'état                                            |
//+------------------------------------------------------------------+
void StateSave()
{
   GlobalVariableSet(GVN("st500"), (double)g_state);
   GlobalVariableSet(GVN("cyc"), (double)g_cycle);
   GlobalVariablesFlush();
   AFileWrite(FileId("etat", ".json"),
              "{\"etat\":\"" + StateName(g_state) + "\",\"cycle\":" + IntegerToString(g_cycle) +
              ",\"ts\":" + IntegerToString((long)TimeGMT()) + "}");
}

void StateLoad()
{
   if(GlobalVariableCheck(GVN("st500")))
   {
      g_state = (int)GlobalVariableGet(GVN("st500"));
      g_cycle = (int)GlobalVariableGet(GVN("cyc"));
   }
   else
   {
      string raw;
      if(AFileRead(FileId("etat", ".json"), raw))
      {
         string st = JGetStr(raw, "etat");
         g_state = (st == "ARRET_500") ? ST_ARRET : (st == "PAUSE" ? ST_PAUSE : ST_ACTIF);
         g_cycle = (int)JGetNum(raw, "cycle", 0);
      }
   }
   if(g_state < ST_ACTIF || g_state > ST_PAUSE) g_state = ST_ACTIF;
   if(g_cycle < 0) g_cycle = 0;
   g_capSent = (GlobalVariableCheck(GVN("capsent")) && GlobalVariableGet(GVN("capsent")) > 0);
}

void SetState(int ns, bool emit = true, const string why = "")
{
   if(ns == g_state) return;
   int old = g_state;
   g_state = ns;
   StateSave();
   if(emit)
      EvTyped("ETAT", 1, "🔁 ÉTAT : " + StateName(old) + " → " + StateName(ns) + (why != "" ? "\n" + why : ""),
              "{\"ancien\":\"" + StateName(old) + "\",\"nouveau\":\"" + StateName(ns) + "\",\"cycle\":" + IntegerToString(g_cycle) + "}");
}

//+------------------------------------------------------------------+
//| PeutTrader() : à appeler juste avant toute NOUVELLE ouverture    |
//+------------------------------------------------------------------+
bool PeutTrader()
{
   if(!g_archiOn) return true;                       // Strategy Tester : jamais de blocage
   if(g_maint) return false;
   if(g_state != ST_ACTIF) return false;
   if(!ActiverSeuil500) return true;
   if(g_phase500) return false;
   if(AccountInfoDouble(ACCOUNT_BALANCE) + 0.005 >= SeuilArret) return false;
   return true;
}

//+------------------------------------------------------------------+
//| Créneaux d'objectif automatique                                  |
//+------------------------------------------------------------------+
void SlotsParse()
{
   ArrayResize(g_hours, 0);
   g_hoursN = 0;
   string parts[];
   int n = StringSplit(ObjectifAutoHeures, ',', parts);
   for(int i = 0; i < n; i++)
   {
      string p = parts[i];
      StringTrimLeft(p); StringTrimRight(p);
      if(!AllDigits(p)) continue;
      int h = (int)StringToInteger(p);
      if(h < 0 || h > 23) continue;
      bool dup = false;
      for(int k = 0; k < g_hoursN; k++) if(g_hours[k] == h) dup = true;
      if(dup) continue;
      ArrayResize(g_hours, g_hoursN + 1);
      g_hours[g_hoursN] = h;
      g_hoursN++;
   }
   for(int a = 0; a < g_hoursN - 1; a++)               // tri croissant
      for(int b = a + 1; b < g_hoursN; b++)
         if(g_hours[b] < g_hours[a]) { int t = g_hours[a]; g_hours[a] = g_hours[b]; g_hours[b] = t; }
}

// prochain créneau (heure du fuseau de l'EA), lundi à vendredi ; 0 si aucun
datetime NextSlot()
{
   if(g_hoursN == 0) return 0;
   datetime now = LocalNow();
   for(int day = 0; day < 8; day++)
   {
      MqlDateTime b;
      TimeToStruct(now + (datetime)(day * 86400), b);
      if(b.day_of_week < 1 || b.day_of_week > 5) continue;
      for(int k = 0; k < g_hoursN; k++)
      {
         b.hour = g_hours[k]; b.min = 0; b.sec = 0;
         datetime t = StructToTime(b);
         if(t > now) return t;
      }
   }
   return 0;
}

string NextSlotText()
{
   datetime t = NextSlot();
   if(t == 0) return "—";
   return TimeToString(t, TIME_DATE | TIME_MINUTES);
}

void SlotTick()
{
   if(!ObjectifAutoCreneaux || g_hoursN == 0) return;
   if(g_state != ST_ACTIF || g_maint || g_phase500) return;
   MqlDateTime d;
   TimeToStruct(LocalNow(), d);
   if(d.day_of_week < 1 || d.day_of_week > 5) return;
   int win = (ObjectifAutoFenetreMin < 1) ? 1 : ObjectifAutoFenetreMin;
   if(d.min >= win) return;
   bool isSlot = false;
   for(int k = 0; k < g_hoursN; k++) if(g_hours[k] == d.hour) isSlot = true;
   if(!isSlot) return;
   long key = (((long)d.year * 100 + d.mon) * 100 + d.day) * 100 + d.hour;
   if(key == g_lastSlotKey) return;
   g_lastSlotKey = key;                                    // le créneau est consommé, jamais rattrapé
   GlobalVariableSet(GVN("slotkey"), (double)key);

   string slot = StringFormat("%02d:00", d.hour);
   if(g_goalActive)
   {
      EvTyped("CRENEAU_SAUTE", 0, "⏭ Créneau " + slot + " sauté : l'objectif précédent n'est pas terminé.",
              "{\"creneau\":\"" + slot + "\",\"raison\":\"objectif_en_cours\"}");
      return;
   }
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   if(bal + 0.005 < SeuilReprise)
   {
      EvTyped("CRENEAU_SAUTE", 1, "⏭ Créneau " + slot + " sauté : solde " + NumS(bal) + " < " + NumS(SeuilReprise) + " " + Cur() + ".",
              "{\"creneau\":\"" + slot + "\",\"raison\":\"solde_insuffisant\"}");
      return;
   }
   if(ActiverSeuil500 && bal + 0.005 >= SeuilArret) return;
   if(!g_configOK)
   {
      EvTyped("CRENEAU_SAUTE", 1, "⏭ Créneau " + slot + " sauté : paramètres non validés (commande VALIDER).",
              "{\"creneau\":\"" + slot + "\",\"raison\":\"parametres_non_valides\"}");
      return;
   }
   string msg;
   if(!GoalSetFromArg("+" + DoubleToString(ObjectifAutoPct, 2) + "%", msg))
   {
      EvTyped("CRENEAU_SAUTE", 1, "⏭ Créneau " + slot + " : objectif impossible (" + msg + ").",
              "{\"creneau\":\"" + slot + "\",\"raison\":\"objectif_refuse\"}");
      return;
   }
   g_enabled = true;
   g_stoppedManually = false;
   GlobalVariableDel(GVN("stopped"));
   g_cooldownUntilMs = 0;
   g_failCount = 0;
   g_nextTradeMs = 0;
   GlobalVariablesFlush();
   EvTyped("OBJECTIF_AUTO", 1, "🎯 Créneau " + slot + " : objectif +" + DoubleToString(ObjectifAutoPct, 2) + " % lancé.\n" + msg,
           "{\"creneau\":\"" + slot + "\",\"cible\":" + NumS(g_goalTarget) + ",\"depart\":" + NumS(g_goalInit) + "}");
}

//+------------------------------------------------------------------+
//| [SEUIL500] Clôture anticipée à SeuilArret                        |
//+------------------------------------------------------------------+
void CapitalFlag(bool on)
{
   g_capSent = on;
   GlobalVariableSet(GVN("capsent"), on ? 1.0 : 0.0);
}

// Passage en ARRET_500 : l'objectif en cours est terminé, plus aucune ouverture
void ToArret500(const string reason, bool emit)
{
   RefreshState();
   if(g_goalActive) { g_goalActive = false; g_goalDone = false; GoalClear(); }
   g_enabled = false;
   if(st_pendCount > 0) CancelPendingFast();
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   SetState(ST_ARRET, false);
   if(emit)
      EvTyped("ARRET_500", 2,
              "🏁 " + NumS(SeuilArret) + " ATTEINTS — ARRÊT\n━━━━━━━━━━━━━━━━━━\n💰 Solde : " + NumS(bal) + " " + Cur() +
              "\n💸 À retirer : " + NumS(bal - SeuilReprise) + " " + Cur() + " (pour laisser " + NumS(SeuilReprise) + ")\n" + reason,
              "{\"solde\":" + NumS(bal) + ",\"a_retirer\":" + NumS(bal - SeuilReprise) + ",\"cycle\":" + IntegerToString(g_cycle) + "}");
}

// Solde final < seuil après la clôture : on continue jusqu'au seuil (objectif « balance cible »)
void ContinueTo500(double bal)
{
   if(g_goalActive && g_goalTarget >= SeuilArret) { g_enabled = true; return; }
   string msg;
   if(GoalSetFromArg("=" + DoubleToString(SeuilArret, 2), msg))
   {
      g_enabled = true;
      g_stoppedManually = false;
      GlobalVariableDel(GVN("stopped"));
      g_cooldownUntilMs = 0;
      g_failCount = 0;
      g_nextTradeMs = 0;
      GlobalVariablesFlush();
   }
   else Log("Reprise vers " + F2(SeuilArret) + " impossible : " + msg);
}

bool BalanceReached500(double bal)
{
   int dg = (int)AccountInfoInteger(ACCOUNT_CURRENCY_DIGITS);
   return bal + 0.5 * MathPow(10.0, -dg) >= SeuilArret;
}

// Séquence de clôture : même mécanisme que le 1 % (CloseAllFast), puis vérification du solde final.
void Close500Sequence()
{
   double bal0 = AccountInfoDouble(ACCOUNT_BALANCE);
   double fl0 = st_floating;
   if(!g_phase500)
   {
      g_phase500 = true;
      EvTyped("CLOTURE_500_LANCEE", 2,
              "🔒 CLÔTURE ANTICIPÉE À " + NumS(SeuilArret) + " LANCÉE\n━━━━━━━━━━━━━━━━━━\n💰 Solde : " + NumS(bal0) +
              " | flottant EA : " + Sgn(fl0) + " = " + NumS(bal0 + fl0) + " " + Cur(),
              "{\"solde\":" + NumS(bal0) + ",\"flottant\":" + NumS(fl0) + ",\"total\":" + NumS(bal0 + fl0) + "}");
   }
   CloseAllFast("seuil 500 : clôture anticipée");
   RefreshState();
   if(st_posCount > 0)
   {
      g_phaseRetryMs = GetTickCount64() + 10000;          // la phase reste active : nouvelle tentative plus tard
      return;
   }
   //--- 0 position : attente de la mise à jour du solde, puis vérification
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   ulong tw = GetTickCount64();
   while(!BalanceReached500(bal) && GetTickCount64() - tw < 2500)
   {
      Sleep(100);
      bal = AccountInfoDouble(ACCOUNT_BALANCE);
   }
   // [SEUIL500] début : glissement simulé, compilé SEULEMENT si SX_TEST_GLISSEMENT est défini (désactivé par défaut)
   #ifdef SX_TEST_GLISSEMENT
   bal -= SX_TEST_GLISSEMENT;
   #endif
   // [SEUIL500] fin
   g_phase500 = false;
   if(BalanceReached500(bal))
   {
      ToArret500("", false);
      EvTyped("CLOTURE_500_OK", 2,
              "🏁 CLÔTURE À " + NumS(SeuilArret) + " TERMINÉE\n━━━━━━━━━━━━━━━━━━\n💰 Solde final : " + NumS(bal) + " " + Cur() +
              " (écart " + Sgn(bal - SeuilArret) + ")\n💸 À retirer : " + NumS(bal - SeuilReprise) + " " + Cur() + " (pour laisser " + NumS(SeuilReprise) + ")",
              "{\"solde\":" + NumS(bal) + ",\"ecart\":" + NumS(bal - SeuilArret) + ",\"a_retirer\":" + NumS(bal - SeuilReprise) + ",\"cycle\":" + IntegerToString(g_cycle) + "}");
      return;
   }
   EvTyped("CLOTURE_500_SOUS_SEUIL", 2,
           "↩️ CLÔTURE TERMINÉE SOUS " + NumS(SeuilArret) + "\n━━━━━━━━━━━━━━━━━━\n💰 Solde final : " + NumS(bal) + " " + Cur() +
           "\n📉 Il manque : " + NumS(SeuilArret - bal) + " " + Cur() + "\n▶️ Le trading reprend jusqu'à atteindre " + NumS(SeuilArret) + ".",
           "{\"solde\":" + NumS(bal) + ",\"manque\":" + NumS(SeuilArret - bal) + "}");
   ContinueTo500(bal);
}

// Appelé à chaque tick, juste après GoalTick() (même endroit et même fréquence que le 1 %)
void Seuil500Tick()
{
   if(!g_archiOn || !ActiverSeuil500) return;
   if(g_closing) return;
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);

   if(g_phase500)                                          // clôture restée incomplète : on réessaie
   {
      if(GetTickCount64() >= g_phaseRetryMs) Close500Sequence();
      return;
   }

   if(g_state == ST_ACTIF)
   {
      if(st_posCount > 0)
      {
         if(bal + st_floating + 0.005 >= SeuilArret) Close500Sequence();
      }
      else if(BalanceReached500(bal))
         ToArret500("", true);                             // clôture normale qui dépasse le seuil, ou plantage pendant la clôture
      return;
   }

   if(g_state == ST_ARRET)
   {
      if(st_posCount > 0) return;
      if(st_pendCount > 0)
      {
         static ulong lastCancel = 0;
         ulong now = GetTickCount64();
         if(now - lastCancel > 1000) { lastCancel = now; CancelPendingFast(); }
      }
      if(bal + 0.005 >= SeuilArret) return;                // en attente de retrait
      if(bal + 0.005 >= SeuilReprise)
      {
         g_cycle++;
         CapitalFlag(false);
         SetState(ST_ACTIF, false);
         EvTyped("REPRISE_RETRAIT", 2,
                 "🟢 REPRISE APRÈS RETRAIT\n━━━━━━━━━━━━━━━━━━\n💰 Solde : " + NumS(bal) + " " + Cur() + " | cycle " + IntegerToString(g_cycle) +
                 "\n🕐 Prochain créneau : " + NextSlotText(),
                 "{\"solde\":" + NumS(bal) + ",\"cycle\":" + IntegerToString(g_cycle) + "}");
         return;
      }
      if(!g_capSent)
      {
         CapitalFlag(true);
         EvTyped("CAPITAL_INSUFFISANT", 2,
                 "⚠️ CAPITAL INSUFFISANT\n━━━━━━━━━━━━━━━━━━\n💰 Solde : " + NumS(bal) + " " + Cur() + " < " + NumS(SeuilReprise) +
                 " : le robot reste à l'arrêt (ARRET_500).",
                 "{\"solde\":" + NumS(bal) + "}");
      }
   }
}

//+------------------------------------------------------------------+
//| Statut / heartbeat                                               |
//+------------------------------------------------------------------+
int CountEaCharts()
{
   string me = MQLInfoString(MQL_PROGRAM_NAME);
   int n = 0;
   long c = ChartFirst();
   while(c >= 0)
   {
      if(ChartGetString(c, CHART_EXPERT_NAME) == me) n++;
      c = ChartNext(c);
   }
   return n;
}

string IsoNow()
{
   string t = TimeToString(TimeGMT(), TIME_DATE | TIME_SECONDS);
   StringReplace(t, ".", "-");
   StringReplace(t, " ", "T");
   return t + "Z";
}

void StatutWrite()
{
   if(!g_archiOn) return;
   RefreshState();
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   double eq  = AccountInfoDouble(ACCOUNT_EQUITY);
   bool conn = (TerminalInfoInteger(TERMINAL_CONNECTED) != 0);
   bool algoT = (TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) != 0);
   bool algoE = (MQLInfoInteger(MQL_TRADE_ALLOWED) != 0);
   double dayPl = (g_dayStartBal > 0) ? (eq - g_dayStartBal) : 0.0;
   datetime ns = NextSlot();
   string j = "{";
   j += "\"id\":\"" + g_aid + "\"";
   j += ",\"login\":" + IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN));
   j += ",\"serveur\":\"" + JsonEsc(AccountInfoString(ACCOUNT_SERVER)) + "\"";
   j += ",\"broker\":\"" + JsonEsc(AccountInfoString(ACCOUNT_COMPANY)) + "\"";
   j += ",\"devise\":\"" + JsonEsc(Cur()) + "\"";
   j += ",\"solde\":" + NumS(bal);
   j += ",\"equity\":" + NumS(eq);
   j += ",\"marge\":" + NumS(AccountInfoDouble(ACCOUNT_MARGIN));
   j += ",\"marge_libre\":" + NumS(AccountInfoDouble(ACCOUNT_MARGIN_FREE));
   j += ",\"niveau_marge\":" + NumS(AccountInfoDouble(ACCOUNT_MARGIN_LEVEL));
   j += ",\"pl_latent\":" + NumS(AccountInfoDouble(ACCOUNT_PROFIT));
   j += ",\"solde_plus_flottant_ea\":" + NumS(bal + st_floating);
   j += ",\"flottant_ea\":" + NumS(st_floating);
   j += ",\"positions\":" + IntegerToString(st_posCount);
   j += ",\"ordres\":" + IntegerToString(st_pendCount);
   j += ",\"pl_jour\":" + NumS(dayPl);
   j += ",\"etat\":\"" + StateName(g_state) + "\"";
   j += ",\"phase_cloture500\":" + BoolS(g_phase500);
   j += ",\"maintenance\":" + BoolS(g_maint);
   j += ",\"cycle\":" + IntegerToString(g_cycle);
   j += ",\"connecte\":" + BoolS(conn);
   j += ",\"algo_terminal\":" + BoolS(algoT);
   j += ",\"algo_ea\":" + BoolS(algoE);
   j += ",\"symbole\":\"" + JsonEsc(_Symbol) + "\"";
   j += ",\"version\":\"" + SX_VERSION + "\"";
   j += ",\"nb_graphiques_ea\":" + IntegerToString(CountEaCharts());
   j += ",\"ea_demarre\":" + BoolS(g_enabled);
   j += ",\"objectif_actif\":" + BoolS(g_goalActive);
   j += ",\"objectif_depart\":" + NumS(g_goalActive ? g_goalInit : 0.0);
   j += ",\"objectif_cible\":" + NumS(g_goalActive ? g_goalTarget : 0.0);
   j += ",\"objectif_progression\":" + NumS(GoalProgressPct());
   j += ",\"seuil_arret\":" + NumS(SeuilArret);
   j += ",\"seuil_reprise\":" + NumS(SeuilReprise);
   j += ",\"seuil500_actif\":" + BoolS(ActiverSeuil500);
   j += ",\"prochain_creneau\":\"" + (ns > 0 ? TimeToString(ns, TIME_DATE | TIME_MINUTES) : "") + "\"";
   j += ",\"raison_blocage\":\"" + JsonEsc(g_blockReason) + "\"";
   j += ",\"trading_autorise\":" + BoolS(g_blockReason == "");
   j += ",\"pause_jusqua\":\"" + (g_pauseUntil > 0 ? TimeToString(g_pauseUntil, TIME_DATE | TIME_MINUTES) : "") + "\"";
   j += ",\"vacances\":" + BoolS(g_vacation);
   j += ",\"reprise_auto\":\"" + (g_resumeAt > 0 ? TimeToString(ZoneFromServer(g_resumeAt), TIME_DATE | TIME_MINUTES) : "") + "\"";
   j += ",\"signal\":\"" + (mk_fast > mk_slow ? "achat" : (mk_fast < mk_slow ? "vente" : "")) + "\"";
   j += ",\"filtres\":[";
   for(int k = 0; k < FB_N; k++)
   {
      int i = FbOrder(k);
      j += (k > 0 ? "," : "") + "{\"nom\":\"" + JsonEsc(FbName(i)) + "\",\"actif\":" + BoolS(FbOn(i)) + ",\"ok\":" + BoolS(FbOK(i)) +
           ",\"info\":\"" + JsonEsc(FbInfo(i)) + "\",\"raison\":\"" + JsonEsc(FbWhy(i)) + "\"}";
   }
   j += "]";
   j += ",\"news_filtre\":" + BoolS(PBool(P_NEWS_BLOCK));
   j += ",\"news_bloque\":" + BoolS(g_newsBlock);
   j += ",\"news_raison\":\"" + JsonEsc(g_newsWhy) + "\"";
   j += ",\"news\":" + NewsJson(3);
   j += ",\"magic\":" + IntegerToString((long)MagicNumber);
   j += ",\"ts\":" + IntegerToString((long)TimeGMT());
   j += ",\"ts_iso\":\"" + IsoNow() + "\"";
   j += "}";
   AFileWrite(FileId("statut", ".json"), j);
}

//+------------------------------------------------------------------+
//| Commandes                                                        |
//+------------------------------------------------------------------+
bool OkFromText(const string t) { return StringFind(t, "❌") < 0; }

string JoinArgs(string &a[], int from)
{
   string r = "";
   for(int i = from; i < ArraySize(a); i++) r += (r == "" ? "" : " ") + a[i];
   return r;
}

// PAUSE [durée] : sans argument, pause jusqu'à REPRISE ; « 2h », « 45m », « 14:30 » = reprise automatique (SmartTick : PauseTick)
bool CmdPause(string &msg, const string arg = "")
{
   datetime until = 0;
   if(arg != "")
   {
      until = ParsePauseArg(arg);
      if(until == 0) { msg = "❌ Durée non reconnue (exemples : 2h, 45m, 14:30)."; return false; }
   }
   bool wasPause = (g_state == ST_PAUSE);
   ActPause();
   RefreshState();
   int c = CancelPendingFast();                             // plus aucun remplissage de la grille pendant la pause
   if(!wasPause) SetState(ST_PAUSE, true, until > 0 ? "Pause programmée" : "Commande PAUSE");
   g_pauseUntil = until;
   if(until > 0) GlobalVariableSet(GVN("pzuntil"), (double)until);
   else GlobalVariableDel(GVN("pzuntil"));
   msg = "⏸ PAUSE : positions " + IntegerToString(st_posCount) + ", ordres en attente annulés : " + IntegerToString(c) + " (la grille sera reposée à la reprise).";
   if(until > 0)
      msg += "\n⏰ Reprise automatique à " + TimeToString(until, (until - LocalNow() > 20 * 3600) ? TIME_DATE | TIME_MINUTES : TIME_MINUTES) +
             " (dans " + FmtDur((double)(until - LocalNow())) + ").";
   return true;
}

bool CmdReprise(string &msg)
{
   if(g_state == ST_ARRET)
   {
      msg = "Compte en ARRET_500 : la reprise est automatique après le retrait (solde entre " + NumS(SeuilReprise) + " et " + NumS(SeuilArret) + ").";
      return false;
   }
   g_stoppedManually = false;
   GlobalVariableDel(GVN("stopped"));
   g_pauseUntil = 0;
   GlobalVariableDel(GVN("pzuntil"));
   SetState(ST_ACTIF, true, "Commande REPRISE");
   if(g_goalActive) return ActStart(msg);
   msg = "▶️ État ACTIF. Aucun objectif en cours : prochain créneau " + NextSlotText() + ".";
   return true;
}

bool CmdVacances(const string arg, string &msg)
{
   string a = arg;
   StringToLower(a);
   if(a == "on" || a == "off") VacationSet(a == "on");
   msg = g_vacation ? "🏖 MODE VACANCES ACTIF\n━━━━━━━━━━━━━━━━━━\n• Aucune nouvelle série ne démarre\n• La grille n'est plus étendue\n" +
                      "• Les positions ouvertes restent gérées jusqu'à leur TP\n• Plafond de risque : tout est fermé si la perte flottante dépasse " +
                      F1(PDbl(P_VAC_RISK)) + " % de la balance"
                    : "🏠 Mode vacances désactivé : fonctionnement normal.";
   return true;
}

bool CmdSet(string &args[], string &msg)
{
   if(ArraySize(args) < 2) { msg = "❌ Format : SET clé valeur"; return false; }
   int id = FindParamByKey(args[0]);
   if(id < 0) { msg = "❌ Clé inconnue : " + args[0]; return false; }
   double v;
   if(!ParseValue(id, JoinArgs(args, 1), v)) { msg = "❌ Valeur non reconnue pour " + g_pName[id]; return false; }
   string err;
   if(!SetParam(id, v, err)) { msg = "❌ " + err; return false; }
   msg = "⚙️ " + g_pName[id] + "\n✅ Appliqué : " + PValStr(id);
   return true;
}

bool CmdExecute(string cmd, string &args[], string &msg)
{
   msg = "";
   StringToUpper(cmd);
   string a0 = (ArraySize(args) > 0) ? args[0] : "";
   string rest = JoinArgs(args, 0);

   if(cmd == "PING")  { msg = "pong"; return true; }
   if(cmd == "PAUSE") return CmdPause(msg, rest);
   if(cmd == "REPRISE" || cmd == "DEMARRER") return CmdReprise(msg);
   if(cmd == "STATUT")
   {
      StatutWrite();
      msg = StatusText();
      return true;
   }
   if(cmd == "MAINTENANCE_ON")
   {
      g_maint = true;
      int c = CancelPendingFast();
      msg = "⚙️ MAINTENANCE activée : plus aucune ouverture. Ordres en attente annulés : " + IntegerToString(c) + ".";
      EvTyped("MAINTENANCE", 1, msg, "{\"actif\":true}");
      return true;
   }
   if(cmd == "MAINTENANCE_OFF")
   {
      g_maint = false;
      msg = "⚙️ MAINTENANCE levée.";
      EvTyped("MAINTENANCE", 1, msg, "{\"actif\":false}");
      return true;
   }
   if(cmd == "FERMER_TERMINAL")
   {
      if(g_phase500 || g_closing) { msg = "Clôture en cours : fermeture du terminal refusée."; return false; }
      g_termCloseAt = GetTickCount64() + 2000;
      msg = "🔌 Fermeture du terminal dans 2 s (les positions restent ouvertes chez le broker).";
      return true;
   }
   if(cmd == "VALIDER")
   {
      g_configOK = true;
      GlobalVariableSet(GVN("cfgok"), 1);
      msg = "✅ Paramètres validés.";
      return true;
   }
   if(cmd == "FERMER_TOUT")
   {
      msg = ActCloseAll();
      g_stoppedManually = true;
      GlobalVariableSet(GVN("stopped"), 1);
      SetState(ST_PAUSE, true, "Commande FERMER_TOUT");
      return OkFromText(msg);
   }
   if(cmd == "ANNULER_OBJECTIF")
   {
      GoalCancel();
      msg = "✅ Objectif annulé.";
      return true;
   }
   if(cmd == "OBJECTIF")
   {
      if(rest == "") { msg = GoalText(); return true; }
      bool ok = GoalSetFromArg(rest, msg);
      msg = (ok ? "✅ " : "❌ ") + msg;
      return ok;
   }
   if(cmd == "SET") return CmdSet(args, msg);
   if(cmd == "PARAMS")  { msg = ParamListText(); return true; }
   if(cmd == "RISQUE")  { msg = GroupText(G_RISK); return true; }
   if(cmd == "FILTRES") { UpdateFilters(); msg = FilterBoardText(); return true; }
   if(cmd == "NEWS")    { NewsScan(); msg = NewsText(); return true; }
   if(cmd == "POSITIONS") { msg = PositionsText(); return true; }
   if(cmd == "RAPPORT" || cmd == "STATS") { msg = TodayReportText("📅 RAPPORT DU JOUR"); return true; }
   if(cmd == "JOURNAL") { msg = FilterJournalText(); return true; }
   if(cmd == "PRESETS") { msg = PresetsText(); return true; }
   if(cmd == "VACANCES") return CmdVacances(a0, msg);
   if(cmd == "CAPTURE") { g_shotReq = "1"; msg = "📸 Capture en route…"; return true; }
   if(cmd == "COURBE")  { g_curveReq = "1"; msg = "📈 Courbe en route…"; return true; }
   if(cmd == "PRESET_SAVE")
   {
      bool ok = PresetSave(a0);
      msg = ok ? "✅ Preset « " + SanitizeName(a0) + " » enregistré." : "❌ Nom invalide (lettres, chiffres, - et _ uniquement).";
      return ok;
   }
   if(cmd == "PRESET_LOAD" || cmd == "PRESET_DELETE")
   {
      PresetList();
      string nm = SanitizeName(a0);
      for(int i = 0; i < g_psCount; i++)
      {
         if(g_psNames[i] != nm) continue;
         if(cmd == "PRESET_DELETE") { PresetDelete(nm); msg = "🗑 Preset « " + nm + " » supprimé."; return true; }
         if(PresetLoad(nm))
         {
            g_configOK = false;
            GlobalVariableDel(GVN("cfgok"));
            msg = "✅ Preset « " + nm + " » chargé. ⚠️ Valide les paramètres (VALIDER) avant la reprise.";
            return true;
         }
         msg = "❌ Chargement impossible.";
         return false;
      }
      msg = "❌ Preset introuvable.";
      return false;
   }
   if(cmd == "RESET_PARAMS")
   {
      ResetParamsToDefaults();
      RebuildIndicators();
      g_tpSig = -1;
      ChartRefreshAll(true);
      g_configOK = false;
      GlobalVariableDel(GVN("cfgok"));
      msg = "✅ Valeurs par défaut rétablies. ⚠️ Valide les paramètres (VALIDER) avant la reprise.";
      return true;
   }
   msg = "Commande inconnue : " + cmd;
   return false;
}

void AckWrite(long n, const string cmd, bool ok, const string msg)
{
   string j = "{\"id\":\"" + g_aid + "\",\"n\":" + IntegerToString(n) +
              ",\"cmd\":\"" + JsonEsc(cmd) + "\",\"ok\":" + BoolS(ok) +
              ",\"message\":\"" + JsonEsc(msg) + "\",\"ts\":" + IntegerToString((long)TimeGMT()) + "}";
   AFileWrite(FileId("ack", "") + "_" + IntegerToString(n) + ".json", j);
}

void BadMark(const string name, bool bad)
{
   int n = ArraySize(g_badName);
   for(int i = 0; i < n; i++)
      if(g_badName[i] == name)
      {
         if(bad) g_badCnt[i]++;
         else { g_badName[i] = ""; g_badCnt[i] = 0; }
         return;
      }
   if(bad)
   {
      ArrayResize(g_badName, n + 1);
      ArrayResize(g_badCnt, n + 1);
      g_badName[n] = name;
      g_badCnt[n] = 1;
   }
}

int BadCount(const string name)
{
   for(int i = 0; i < ArraySize(g_badName); i++) if(g_badName[i] == name) return g_badCnt[i];
   return 0;
}

void CmdPoll()
{
   string prefix = "cmd_" + g_aid + "_";
   int pl = StringLen(prefix);
   string fn;
   long hnd = FileFindFirst(prefix + "*.json", fn, FILE_COMMON);
   if(hnd == INVALID_HANDLE) return;
   string names[];
   long nums[];
   int cnt = 0;
   do
   {
      int L = StringLen(fn);
      if(L <= pl + 5) continue;
      if(StringSubstr(fn, L - 5) != ".json") continue;
      string mid = StringSubstr(fn, pl, L - pl - 5);
      if(!AllDigits(mid)) continue;
      ArrayResize(names, cnt + 1);
      ArrayResize(nums, cnt + 1);
      names[cnt] = fn;
      nums[cnt] = StringToInteger(mid);
      cnt++;
   }
   while(FileFindNext(hnd, fn));
   FileFindClose(hnd);
   //--- tri par numéro croissant
   for(int a = 0; a < cnt - 1; a++)
      for(int b = a + 1; b < cnt; b++)
         if(nums[b] < nums[a])
         {
            long tn = nums[a]; nums[a] = nums[b]; nums[b] = tn;
            string ts = names[a]; names[a] = names[b]; names[b] = ts;
         }
   int done = 0;
   for(int i = 0; i < cnt && done < 20; i++)
   {
      string raw;
      if(!AFileRead(names[i], raw)) continue;                      // verrouillé ou vide : on réessaie plus tard
      if(!JLooksValid(raw))
      {
         BadMark(names[i], true);
         if(BadCount(names[i]) >= 3)
         {
            Log("Commande illisible ignorée et supprimée : " + names[i]);
            FileDelete(names[i], FILE_COMMON);
            BadMark(names[i], false);
         }
         continue;
      }
      string args[];
      if(JGetArgs(raw, args) < 0) ArrayResize(args, 0);
      string cmd = JGetStr(raw, "cmd");
      string rid = JGetStr(raw, "id");
      double ts = JGetNum(raw, "ts", 0);
      FileDelete(names[i], FILE_COMMON);                           // supprimée AVANT l'exécution : jamais rejouée
      BadMark(names[i], false);
      done++;
      if(rid != "" && rid != g_aid) continue;                      // destinée à une autre instance
      if(cmd == "") continue;
      if(ts > 0 && (double)TimeGMT() - ts > 300.0)                 // plus de 5 min : ignorée
      {
         AckWrite(nums[i], cmd, false, "Commande expirée (plus de 5 min), ignorée.");
         continue;
      }
      string msg;
      bool ok = CmdExecute(cmd, args, msg);
      AckWrite(nums[i], cmd, ok, msg);
   }
}

//+------------------------------------------------------------------+
//| Init / timer / deinit                                            |
//+------------------------------------------------------------------+
// Avant la reprise automatique de OnInit : paramètres considérés comme validés par défaut (robot autonome)
void ArchiPreInit()
{
   g_archiOn = !MQLInfoInteger(MQL_TESTER);
   if(!g_archiOn) return;
   g_aid = IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN)) + "_" + NormalizeServer(AccountInfoString(ACCOUNT_SERVER));
   g_configOK = true;
   GlobalVariableSet(GVN("cfgok"), 1);
}

ulong g_closeOthersAt   = 0;
int   g_closeOthersLeft = 0;

// Un compte = un graphique : ferme tous les autres graphiques du terminal (profil par défaut de MT5, doublons de l'EA)
void CloseOtherCharts()
{
   if(!FermerAutresGraphiques || MQLInfoInteger(MQL_TESTER)) return;
   long me = ChartID();
   long ids[];
   int n = 0;
   long c = ChartFirst();
   while(c >= 0)
   {
      if(c != me) { ArrayResize(ids, n + 1); ids[n] = c; n++; }
      c = ChartNext(c);
   }
   int closed = 0;
   for(int i = 0; i < n; i++)
      if(ChartClose(ids[i])) closed++;
   if(closed > 0) Log("Autres graphiques fermés : " + IntegerToString(closed) + " (un compte = un graphique)");
}

void ArchiInit()
{
   if(!g_archiOn) return;
   StateLoad();
   StateSave();
   SlotsParse();
   g_lastSlotKey = GlobalVariableCheck(GVN("slotkey")) ? (long)GlobalVariableGet(GVN("slotkey")) : 0;
   // un redémarrage pendant une phase de clôture n'est pas persisté : la condition est simplement réévaluée au premier tick
   if(g_state == ST_PAUSE) { g_stoppedManually = true; GlobalVariableSet(GVN("stopped"), 1); g_enabled = false; }
   string cur = AccountInfoString(ACCOUNT_CURRENCY);
   EvTyped("DEMARRAGE_EA", 1, "🤖 ScalpX " + SX_VERSION + " démarré sur " + _Symbol + " (" + g_aid + ")\nÉtat " + StateName(g_state) + " · cycle " + IntegerToString(g_cycle) +
           " · devise " + cur,
           "{\"version\":\"" + SX_VERSION + "\",\"etat\":\"" + StateName(g_state) + "\",\"cycle\":" + IntegerToString(g_cycle) + ",\"devise\":\"" + JsonEsc(cur) + "\"}");
   StatutWrite();
   CloseOtherCharts();                               // les graphiques du profil peuvent arriver après l'EA : 3 passes
   g_closeOthersAt = GetTickCount64() + 3000;
   g_closeOthersLeft = 3;
}

void ArchiDeinit(const int reason)
{
   if(!g_archiOn) return;
   EvTyped("ARRET_EA", 1, "🔌 ScalpX arrêté (raison " + IntegerToString(reason) + ")", "{\"raison\":" + IntegerToString(reason) + "}");
   EvFlushPending();
   GlobalVariablesFlush();
}

// Appelé à chaque OnTimer (250 ms) : planifie lui-même ses tâches
void ArchiTimer()
{
   if(!g_archiOn) return;
   static ulong lastCmd = 0, lastStat = 0, lastSec = 0, lastFlush = 0;
   ulong now = GetTickCount64();
   if(g_closeOthersLeft > 0 && now >= g_closeOthersAt) { g_closeOthersLeft--; g_closeOthersAt = now + 4000; CloseOtherCharts(); }

   if(g_termCloseAt > 0 && now >= g_termCloseAt)
   {
      g_termCloseAt = 0;
      StatutWrite();
      TerminalClose(0);
      return;
   }
   if(now - lastCmd >= 2000)  { lastCmd = now; CmdPoll(); }
   if(now - lastStat >= 5000) { lastStat = now; StatutWrite(); }
   if(now - lastSec >= 1000)
   {
      lastSec = now;
      EvFlushPending();
      SlotTick();
      //--- cohérence avec le panneau / l'ancien code : arrêt manuel <-> PAUSE
      if(g_state == ST_ACTIF && g_stoppedManually) SetState(ST_PAUSE, true, "Arrêt manuel détecté");
      else if(g_state == ST_PAUSE && !g_stoppedManually && g_enabled) SetState(ST_ACTIF, true, "Reprise manuelle détectée");
      //--- maintenance : aucun ordre en attente ne doit se remplir
      if(g_maint && st_pendCount > 0) CancelPendingFast();
      //--- reprise après plantage : mémorise « le robot tournait » (relu par OnInit après un arrêt brutal)
      static int lastRun = -1;
      int run = (g_enabled && g_goalActive) ? 1 : 0;
      if(run != lastRun)
      {
         lastRun = run;
         GlobalVariableSet(GVN("run"), (double)run);
         GlobalVariablesFlush();
      }
   }
   if(now - lastFlush >= 30000) { lastFlush = now; GlobalVariablesFlush(); }
}
//+------------------------------------------------------------------+
