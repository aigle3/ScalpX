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
int      g_slots[];                    // créneaux d'objectif : minutes depuis minuit (triées)
int      g_slotsN       = 0;
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
int g_ioErr = 0;                       // dernière erreur d'écriture d'un fichier d'échange (journal Experts)

// Échec d'écriture d'un fichier d'échange avec le bot : noté dans le journal Experts (au plus une ligne par minute et par sorte),
// sans quoi un statut jamais écrit fait croire au bot que l'EA est muet, sans trace côté MT5.
void IoWarn(const string what)
{
   static string kinds[];
   static ulong  lastMs[];
   static int    skipped[];
   int k = -1, n = ArraySize(kinds);
   for(int i = 0; i < n; i++) if(kinds[i] == what) { k = i; break; }
   if(k < 0) { ArrayResize(kinds, n + 1); ArrayResize(lastMs, n + 1); ArrayResize(skipped, n + 1); k = n; kinds[k] = what; lastMs[k] = 0; skipped[k] = 0; }
   ulong now = GetTickCount64();
   if(lastMs[k] > 0 && now - lastMs[k] < 60000) { skipped[k]++; return; }
   Log("Écriture impossible : " + what + " (erreur " + IntegerToString(g_ioErr) + ")" +
       (skipped[k] > 0 ? " ; " + IntegerToString(skipped[k]) + " autre(s) échec(s) depuis le dernier message" : ""));
   lastMs[k] = now;
   skipped[k] = 0;
}

bool AFileWrite(const string name, const string content)
{
   if(g_btManual) return true;        // [ARCHI] 7.12.0 : testeur lancé à la main : aucun fichier écrit pour le bot
   string tmp = name + ".tmp";
   int h = FileOpen(tmp, FILE_WRITE | FILE_BIN | FILE_COMMON);
   if(h == INVALID_HANDLE) { g_ioErr = GetLastError(); return false; }
   uchar a[];
   StringToCharArray(content, a, 0, WHOLE_ARRAY, CP_UTF8);
   int n = ArraySize(a);
   if(n > 0 && a[n - 1] == 0) n--;
   if(n > 0) FileWriteArray(h, a, 0, n);
   FileClose(h);
   if(!FileMove(tmp, FILE_COMMON, name, FILE_COMMON | FILE_REWRITE))
   {
      g_ioErr = GetLastError();
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
   if(g_btManual) return true;        // [ARCHI] 7.12.0 : testeur lancé à la main : aucun fichier écrit pour le bot
   int h = FileOpen(name, FILE_READ | FILE_WRITE | FILE_BIN | FILE_COMMON | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE) { g_ioErr = GetLastError(); return false; }
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
      IoWarn("événements (fichier inaccessible, 200 en attente : le plus ancien est abandonné)");
   }
   ArrayResize(g_evPend, n + 1);
   g_evPend[n] = line;
}

// Événement typé. prio : 0 info, 1 important, 2 critique. dataJson : objet JSON facultatif.
void BtCount(const string type, const string text);   // [ARCHI] 7.2.0 (SX_Backtest.mqh)

void EvTyped(const string type, int prio, const string text, const string dataJson = "")
{
   if(!g_archiOn && !g_bt) return;
   if(g_bt) BtCount(type, text);                     // 7.2.0 : compteurs de la progression du backtest
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
   if(!g_archiOn && !g_bt) return true;              // Strategy Tester : jamais de blocage (sauf backtest du bot : comme en réel)
   if(g_maint) return false;
   if(g_state != ST_ACTIF) return false;
   if(!PBool(P_SEUIL_ON)) return true;
   if(g_phase500) return false;
   if(AccountInfoDouble(ACCOUNT_BALANCE) + 0.005 >= PDbl(P_SEUIL_ARRET)) return false;
   return true;
}

//+------------------------------------------------------------------+
//| Créneaux d'objectif automatique                                  |
//| Réglage = 4 masques de 24 bits (P_SL_M00..P_SL_M45, un par       |
//| minute 00/15/30/45) : le bit h = un créneau à l'heure h.         |
//+------------------------------------------------------------------+
void SlotsParse()
{
   ArrayResize(g_slots, 0);
   g_slotsN = 0;
   for(int h = 0; h < 24; h++)                         // ordre croissant : heure puis minutes
      for(int mi = 0; mi < 4; mi++)
         if((PInt(P_SL_M00 + mi) & (1 << h)) != 0)
         {
            ArrayResize(g_slots, g_slotsN + 1);
            g_slots[g_slotsN] = h * 60 + mi * 15;
            g_slotsN++;
         }
}

string SlotText(const int slot) { return StringFormat("%02d:%02d", slot / 60, slot % 60); }

int SlotWindow() { return PInt(P_SL_WIN); }          // 5 à 15 min : jamais de chevauchement (créneaux espacés de 15 min minimum)

// clé d'un créneau du jour : un créneau n'est lancé qu'une seule fois
long SlotKey(const MqlDateTime &d, const int slot)
{
   return ((((long)d.year * 100 + d.mon) * 100 + d.day) * 100 + slot / 60) * 100 + slot % 60;
}

// créneau dont la fenêtre est ouverte maintenant (lundi à vendredi) ; -1 si aucun
int OpenSlot(const MqlDateTime &d)
{
   if(d.day_of_week < 1 || d.day_of_week > 5) return -1;
   int now = d.hour * 60 + d.min, win = SlotWindow();
   for(int k = 0; k < g_slotsN; k++)
      if(now >= g_slots[k] && now < g_slots[k] + win) return g_slots[k];
   return -1;
}

// Masques modifiés (bot, preset) : relit les créneaux. Un créneau ajouté PENDANT sa fenêtre n'est pas lancé
// aussitôt : il est compté comme consommé pour aujourd'hui (pas de lancement surprise à l'ajout).
void SlotsRefresh()
{
   MqlDateTime d;
   TimeToStruct(LocalNow(), d);
   int before = OpenSlot(d);
   SlotsParse();
   int after = OpenSlot(d);
   if(after >= 0 && after != before)
   {
      g_lastSlotKey = SlotKey(d, after);
      GlobalVariableSet(GVN("slotkey"), (double)g_lastSlotKey);
   }
}

// prochain créneau (heure du fuseau de l'EA), lundi à vendredi ; 0 si aucun
datetime NextSlot()
{
   if(g_slotsN == 0) return 0;
   datetime now = LocalNow();
   for(int day = 0; day < 8; day++)
   {
      MqlDateTime b;
      TimeToStruct(now + (datetime)(day * 86400), b);
      if(b.day_of_week < 1 || b.day_of_week > 5) continue;
      for(int k = 0; k < g_slotsN; k++)
      {
         b.hour = g_slots[k] / 60; b.min = g_slots[k] % 60; b.sec = 0;
         datetime t = StructToTime(b);
         if(t > now) return t;
      }
   }
   return 0;
}

string NextSlotText()
{
   if(PBool(P_SL_CONT)) return "mode continu (filtres OK + clôture M5)";   // [ARCHI] 7.8.0
   datetime t = NextSlot();
   if(t == 0) return "—";
   return TimeToString(t, TIME_DATE | TIME_MINUTES);
}

// [ARCHI] début : 6.6.0 fenêtre d'entrée après le créneau : objectif du créneau sans série ouverte après N min → annulé
datetime g_slotGoalAt = 0;           // lancement (GMT) de l'objectif du créneau, tant qu'aucune série ne s'est ouverte (0 = rien à surveiller)
string   g_slotGoalName = "";

void SlotEntrySet(datetime t, string slot = "")
{
   g_slotGoalAt = t;
   g_slotGoalName = slot;
   if(t > 0) GlobalVariableSet(GVN("slentry"), (double)t);
   else      GlobalVariableDel(GVN("slentry"));
}

void SlotEntryTick()
{
   if(g_slotGoalAt == 0) return;
   if(!g_goalActive || st_posCount > 0) { SlotEntrySet(0); return; }   // série ouverte (ou objectif terminé) : plus rien à surveiller
   int lim = PInt(P_SL_ENTRY);
   if(lim <= 0 || g_maint || g_phase500 || !g_enabled) return;       // robot mis en pause à la main : on n'annule pas son objectif
   if(TimeGMT() - g_slotGoalAt < (datetime)lim * 60) return;
   string slot = g_slotGoalName;
   string why = g_blockReason;
   SlotEntrySet(0);
   if(st_pendCount > 0) CancelPendingFast();
   GoalCancel();
   EvTyped("OBJECTIF_ANNULE", 1, "⌛ " + (slot == "continu" ? "Mode continu : objectif" : slot != "" ? "Créneau " + slot + " : objectif" : "Objectif du créneau") + " annulé : aucune série ouverte en " +
           IntegerToString(lim) + " min" + (why != "" ? " (" + why + ")" : "") + ".\n🕐 Prochain créneau : " + NextSlotText(),
           "{\"creneau\":\"" + slot + "\",\"minutes\":" + IntegerToString(lim) + ",\"raison\":\"" + JsonEsc(why) + "\"}");
}
// [ARCHI] fin

// [ARCHI] début : 7.0.0 coupure de l'objectif (heures = 4 masques ct_m00..ct_m45, tous les jours, fuseau de l'EA)
#define CUT_WINDOW_MIN 15            // une coupure reste applicable 15 min (redémarrage du terminal juste après l'heure)
long g_lastCutKey = 0;

int OpenCut(const MqlDateTime &d)
{
   if(!PBool(P_CT_ON)) return -1;
   int now = d.hour * 60 + d.min;
   for(int mi = 0; mi < 4; mi++)
   {
      int mk = PInt(P_CT_M00 + mi);
      for(int h = 0; h < 24; h++)
         if((mk & (1 << h)) != 0 && now >= h * 60 + mi * 15 && now < h * 60 + mi * 15 + CUT_WINDOW_MIN) return h * 60 + mi * 15;
   }
   return -1;
}

string CutsText()
{
   string s = "";
   for(int h = 0; h < 24; h++)
      for(int mi = 0; mi < 4; mi++)
         if((PInt(P_CT_M00 + mi) & (1 << h)) != 0) s += (s != "" ? ", " : "") + SlotText(h * 60 + mi * 15);
   return s;
}

void CutPendingSet(bool on, string name = "")
{
   g_cutPending = on;
   g_cutName = name;
   if(on) GlobalVariableSet(GVN("cutpend"), 1); else GlobalVariableDel(GVN("cutpend"));
}

void CutCancelGoal(string name, string what)
{
   if(st_pendCount > 0) CancelPendingFast();
   SlotEntrySet(0);
   GoalCancel();
   EvTyped("OBJECTIF_COUPE", 1, "⛔ Coupure " + name + " : " + what + ".\n🕐 Prochain créneau : " + NextSlotText(),
           "{\"coupure\":\"" + name + "\"}");
}

void CutTick()
{
   //--- série en cours au moment de la coupure : l'objectif est annulé dès qu'elle est fermée (TP)
   if(g_cutPending)
   {
      if(!g_goalActive) CutPendingSet(false);                   // objectif atteint (ou arrêté) entre-temps : plus rien à faire
      else if(st_posCount == 0)
      {
         string nm = g_cutName;
         CutPendingSet(false);
         CutCancelGoal(nm, "série fermée, objectif annulé ; le robot attend le prochain créneau");
      }
   }
   MqlDateTime d;
   TimeToStruct(LocalNow(), d);
   int cut = OpenCut(d);
   if(cut < 0) return;
   long key = SlotKey(d, cut);
   if(key == g_lastCutKey) return;
   g_lastCutKey = key;                                           // une coupure n'agit qu'une fois
   GlobalVariableSet(GVN("cutkey"), (double)key);
   LaboOnCut(SlotText(cut));                                     // [ARCHI] 7.1.0 : coupure des objectifs virtuels du labo
   if(!g_goalActive || g_phase500) return;
   string name = SlotText(cut);
   if(st_posCount == 0)
      CutCancelGoal(name, "objectif annulé (aucune série ouverte)");
   else if(!g_cutPending)
   {
      CutPendingSet(true, name);
      EvTyped("COUPURE", 1, "⛔ Coupure " + name + " : plus de nouvelle série. La série en cours (" + IntegerToString(st_posCount) +
              " position(s)) continue jusqu'à son TP, puis l'objectif sera annulé.", "{\"coupure\":\"" + name + "\"}");
   }
}
// [ARCHI] fin

void LaboOnSlot(string slot);   // [ARCHI] 7.1.0 : labo des filtres (défini dans SX_Labo.mqh)
void LaboOnCut(string name);

void SlotTick()
{
   if(PBool(P_SL_CONT)) return;                            // [ARCHI] 7.8.0 : mode continu, pas de créneaux
   if(!PBool(P_SL_ON) || g_slotsN == 0) return;
   if(g_state != ST_ACTIF || g_maint || g_phase500) return;
   MqlDateTime d;
   TimeToStruct(LocalNow(), d);
   int sl = OpenSlot(d);
   if(sl < 0) return;
   long key = SlotKey(d, sl);
   if(key == g_lastSlotKey) return;
   g_lastSlotKey = key;                                    // le créneau est consommé, jamais rattrapé
   GlobalVariableSet(GVN("slotkey"), (double)key);

   string slot = SlotText(sl);
   LaboOnSlot(slot);                                      // [ARCHI] 7.1.0 : objectifs virtuels du labo (même créneau)
   if(g_goalActive)
   {
      EvTyped("CRENEAU_SAUTE", 0, "⏭ Créneau " + slot + " sauté : l'objectif précédent n'est pas terminé.",
              "{\"creneau\":\"" + slot + "\",\"raison\":\"objectif_en_cours\"}");
      return;
   }
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   if(bal + 0.005 < PDbl(P_SEUIL_REPRISE))
   {
      EvTyped("CRENEAU_SAUTE", 1, "⏭ Créneau " + slot + " sauté : solde " + NumS(bal) + " < " + NumS(PDbl(P_SEUIL_REPRISE)) + " " + Cur() + ".",
              "{\"creneau\":\"" + slot + "\",\"raison\":\"solde_insuffisant\"}");
      return;
   }
   if(PBool(P_SEUIL_ON) && bal + 0.005 >= PDbl(P_SEUIL_ARRET)) return;
   if(!g_configOK)
   {
      EvTyped("CRENEAU_SAUTE", 1, "⏭ Créneau " + slot + " sauté : paramètres non validés (commande VALIDER).",
              "{\"creneau\":\"" + slot + "\",\"raison\":\"parametres_non_valides\"}");
      return;
   }
   string msg;
   if(!GoalSetFromArg("+" + DoubleToString(PDbl(P_SL_PCT), 2) + "%", msg))
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
   SlotEntrySet(TimeGMT(), slot);     // [ARCHI] 6.6.0 : fenêtre d'entrée surveillée à partir de maintenant
   GlobalVariablesFlush();
   EvTyped("OBJECTIF_AUTO", 1, "🎯 Créneau " + slot + " : objectif +" + DoubleToString(PDbl(P_SL_PCT), 2) + " % lancé.\n" + msg,
           "{\"creneau\":\"" + slot + "\",\"cible\":" + NumS(g_goalTarget) + ",\"depart\":" + NumS(g_goalInit) + "}");
}

// [ARCHI] début : 7.8.0 mode continu : sans créneaux, objectif lancé dès que tous les filtres activés sont OK
// à la clôture d'une bougie M5. Filtres OK pendant la bougie → armé ; nouvelle bougie M5 : filtres encore OK → lancement,
// sinon on attend qu'ils redeviennent OK (puis la clôture suivante).
bool FiltersAllOK();                // défini dans SX_FilterBoard.mqh
datetime g_contArm   = 0;           // ouverture (serveur) de la bougie M5 pendant laquelle les filtres étaient OK (0 = pas armé)
datetime g_contErrAt = 0;           // dernier message d'objectif refusé (au plus un par heure)

bool ContArmed() { return PBool(P_SL_CONT) && g_contArm > 0; }

void ContTick()
{
   if(!PBool(P_SL_CONT)) { g_contArm = 0; return; }
   if(g_state != ST_ACTIF || g_maint || g_phase500 || g_goalActive || g_stoppedManually || g_vacation || !g_configOK)
   {
      g_contArm = 0;
      return;
   }
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   if(bal + 0.005 < PDbl(P_SEUIL_REPRISE) || (PBool(P_SEUIL_ON) && bal + 0.005 >= PDbl(P_SEUIL_ARRET))) { g_contArm = 0; return; }
   datetime bar = iTime(_Symbol, PERIOD_M5, 0);
   if(bar <= 0) return;
   string w;
   bool ok = f_marketOpen && FiltersAllOK() && TradeEnvOK(w);
   if(g_contArm == 0) { if(ok) g_contArm = bar; return; }
   if(bar == g_contArm) return;                            // bougie M5 en cours : on attend sa clôture
   g_contArm = 0;
   if(!ok) return;                                         // un filtre bloque à la clôture : on attend qu'ils redeviennent OK
   string msg;
   if(!GoalSetFromArg("+" + DoubleToString(PDbl(P_SL_PCT), 2) + "%", msg))
   {
      if(TimeGMT() - g_contErrAt >= 3600)
      {
         g_contErrAt = TimeGMT();
         EvTyped("CRENEAU_SAUTE", 1, "⏭ Mode continu : objectif impossible (" + msg + ").",
                 "{\"creneau\":\"continu\",\"raison\":\"objectif_refuse\"}");
      }
      return;
   }
   g_enabled = true;
   g_stoppedManually = false;
   GlobalVariableDel(GVN("stopped"));
   g_cooldownUntilMs = 0;
   g_failCount = 0;
   g_nextTradeMs = 0;
   SlotEntrySet(TimeGMT(), "continu");                     // fenêtre d'entrée : même règle qu'un créneau
   GlobalVariablesFlush();
   EvTyped("OBJECTIF_AUTO", 1, "🎯 Mode continu : tous les filtres OK à la clôture M5 de " + TimeToString(LocalNow(), TIME_MINUTES) +
           " : objectif +" + DoubleToString(PDbl(P_SL_PCT), 2) + " % lancé.\n" + msg,
           "{\"creneau\":\"continu\",\"cible\":" + NumS(g_goalTarget) + ",\"depart\":" + NumS(g_goalInit) + "}");
}
// [ARCHI] fin

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
              "🏁 " + NumS(PDbl(P_SEUIL_ARRET)) + " ATTEINTS — ARRÊT\n━━━━━━━━━━━━━━━━━━\n💰 Solde : " + NumS(bal) + " " + Cur() +
              "\n💸 À retirer : " + NumS(bal - PDbl(P_SEUIL_REPRISE)) + " " + Cur() + " (pour laisser " + NumS(PDbl(P_SEUIL_REPRISE)) + ")\n" + reason,
              "{\"solde\":" + NumS(bal) + ",\"a_retirer\":" + NumS(bal - PDbl(P_SEUIL_REPRISE)) + ",\"cycle\":" + IntegerToString(g_cycle) + "}");
}

// Solde final < seuil après la clôture : on continue jusqu'au seuil (objectif « balance cible »)
void ContinueTo500(double bal)
{
   if(g_goalActive && g_goalTarget >= PDbl(P_SEUIL_ARRET)) { g_enabled = true; return; }
   string msg;
   if(GoalSetFromArg("=" + DoubleToString(PDbl(P_SEUIL_ARRET), 2), msg))
   {
      g_enabled = true;
      g_stoppedManually = false;
      GlobalVariableDel(GVN("stopped"));
      g_cooldownUntilMs = 0;
      g_failCount = 0;
      g_nextTradeMs = 0;
      GlobalVariablesFlush();
   }
   else Log("Reprise vers " + F2(PDbl(P_SEUIL_ARRET)) + " impossible : " + msg);
}

bool BalanceReached500(double bal)
{
   int dg = (int)AccountInfoInteger(ACCOUNT_CURRENCY_DIGITS);
   return bal + 0.5 * MathPow(10.0, -dg) >= PDbl(P_SEUIL_ARRET);
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
              "🔒 CLÔTURE ANTICIPÉE À " + NumS(PDbl(P_SEUIL_ARRET)) + " LANCÉE\n━━━━━━━━━━━━━━━━━━\n💰 Solde : " + NumS(bal0) +
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
              "🏁 CLÔTURE À " + NumS(PDbl(P_SEUIL_ARRET)) + " TERMINÉE\n━━━━━━━━━━━━━━━━━━\n💰 Solde final : " + NumS(bal) + " " + Cur() +
              " (écart " + Sgn(bal - PDbl(P_SEUIL_ARRET)) + ")\n💸 À retirer : " + NumS(bal - PDbl(P_SEUIL_REPRISE)) + " " + Cur() + " (pour laisser " + NumS(PDbl(P_SEUIL_REPRISE)) + ")",
              "{\"solde\":" + NumS(bal) + ",\"ecart\":" + NumS(bal - PDbl(P_SEUIL_ARRET)) + ",\"a_retirer\":" + NumS(bal - PDbl(P_SEUIL_REPRISE)) + ",\"cycle\":" + IntegerToString(g_cycle) + "}");
      return;
   }
   EvTyped("CLOTURE_500_SOUS_SEUIL", 2,
           "↩️ CLÔTURE TERMINÉE SOUS " + NumS(PDbl(P_SEUIL_ARRET)) + "\n━━━━━━━━━━━━━━━━━━\n💰 Solde final : " + NumS(bal) + " " + Cur() +
           "\n📉 Il manque : " + NumS(PDbl(P_SEUIL_ARRET) - bal) + " " + Cur() + "\n▶️ Le trading reprend jusqu'à atteindre " + NumS(PDbl(P_SEUIL_ARRET)) + ".",
           "{\"solde\":" + NumS(bal) + ",\"manque\":" + NumS(PDbl(P_SEUIL_ARRET) - bal) + "}");
   ContinueTo500(bal);
}

// [ARCHI] début 7.8.5 : reprise après retrait (solde entre le seuil de reprise et le seuil d'arrêt)
void RepriseApresRetrait(double bal)
{
   g_cycle++;
   CapitalFlag(false);
   SetState(ST_ACTIF, false);
   EvTyped("REPRISE_RETRAIT", 2,
           "🟢 REPRISE APRÈS RETRAIT\n━━━━━━━━━━━━━━━━━━\n💰 Solde : " + NumS(bal) + " " + Cur() + " | cycle " + IntegerToString(g_cycle) +
           "\n🕐 Prochain créneau : " + NextSlotText(),
           "{\"solde\":" + NumS(bal) + ",\"cycle\":" + IntegerToString(g_cycle) + "}");
}

// Appelée chaque seconde par le timer : le week-end (marché fermé) aucun tick n'arrive, Seuil500Tick ne tourne pas.
void RepriseRetraitTimer()
{
   if(g_state != ST_ARRET || g_closing || g_phase500 || !PBool(P_SEUIL_ON)) return;
   RefreshState();
   if(st_posCount + st_pendCount > 0) return;                    // Seuil500Tick s'en charge (fermeture / annulation)
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   if(bal + 0.005 >= PDbl(P_SEUIL_ARRET) || bal + 0.005 < PDbl(P_SEUIL_REPRISE)) return;
   RepriseApresRetrait(bal);
}
// [ARCHI] fin

// Appelé à chaque tick, juste après GoalTick() (même endroit et même fréquence que le 1 %)
void Seuil500Tick()
{
   if((!g_archiOn && !g_bt) || !PBool(P_SEUIL_ON)) return;   // 7.2.0 : actif aussi dans le backtest du bot
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
         if(bal + st_floating + 0.005 >= PDbl(P_SEUIL_ARRET)) Close500Sequence();
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
      if(bal + 0.005 >= PDbl(P_SEUIL_ARRET)) return;                // en attente de retrait
      if(bal + 0.005 >= PDbl(P_SEUIL_REPRISE)) { RepriseApresRetrait(bal); return; }
      if(!g_capSent)
      {
         CapitalFlag(true);
         EvTyped("CAPITAL_INSUFFISANT", 2,
                 "⚠️ CAPITAL INSUFFISANT\n━━━━━━━━━━━━━━━━━━\n💰 Solde : " + NumS(bal) + " " + Cur() + " < " + NumS(PDbl(P_SEUIL_REPRISE)) +
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
   j += ",\"pl_jour_realise\":" + NumS(g_dayStartBal > 0 ? bal - g_dayStartBal : 0.0);   // trades fermés du jour, sans le latent
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
   j += ",\"seuil_arret\":" + NumS(PDbl(P_SEUIL_ARRET));
   j += ",\"seuil_reprise\":" + NumS(PDbl(P_SEUIL_REPRISE));
   j += ",\"seuil500_actif\":" + BoolS(PBool(P_SEUIL_ON));
   j += ",\"prochain_creneau\":\"" + (ns > 0 ? TimeToString(ns, TIME_DATE | TIME_MINUTES) : "") + "\"";
   j += ",\"mode_continu\":" + BoolS(PBool(P_SL_CONT));                                   // [ARCHI] 7.8.0
   j += ",\"continu_arme\":" + BoolS(ContArmed());
   j += ",\"coupures\":\"" + (PBool(P_CT_ON) ? CutsText() : "") + "\"";                      // [ARCHI] 7.0.0
   j += ",\"coupure_en_cours\":\"" + (g_cutPending ? g_cutName : "") + "\"";
   j += ",\"raison_blocage\":\"" + JsonEsc(g_blockReason) + "\"";
   j += ",\"trading_autorise\":" + BoolS(g_blockReason == "");
   j += ",\"pause_jusqua\":\"" + (g_pauseUntil > 0 ? TimeToString(g_pauseUntil, TIME_DATE | TIME_MINUTES) : "") + "\"";
   j += ",\"vacances\":" + BoolS(g_vacation);
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
   j += ",\"levier\":" + IntegerToString((long)AccountInfoInteger(ACCOUNT_LEVERAGE));   // 7.2.0 : repris par le backtest
   j += ",\"ts\":" + IntegerToString((long)TimeGMT());
   j += ",\"ts_iso\":\"" + IsoNow() + "\"";
   j += "}";
   if(!AFileWrite(FileId("statut", ".json"), j)) IoWarn("statut");
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
      msg = "Compte en ARRET_500 : la reprise est automatique après le retrait (solde entre " + NumS(PDbl(P_SEUIL_REPRISE)) + " et " + NumS(PDbl(P_SEUIL_ARRET)) + ").";
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
   if(!AFileWrite(FileId("ack", "") + "_" + IntegerToString(n) + ".json", j)) IoWarn("réponse à la commande " + cmd);
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

// [ARCHI] début : 7.11.0 : Market Watch réduit au symbole du robot. Chaque symbole affiché reçoit ses cotations en continu
// (calcul et réseau inutiles, multipliés par le nombre de terminaux). Un symbole encore utilisé par MT5 (position, conversion
// de devise, graphique) refuse d'être masqué : il reste, sans erreur. Réel seulement (jamais dans le testeur).
// 7.11.1 : plus fait au démarrage (OnInit) mais 60 s après, par petits lots (5 symboles par seconde) depuis le timer :
// le démarrage de l'EA et son premier statut ne peuvent plus être retardés par un terminal encore occupé à se charger.
ulong g_mwStartMs = 0;                // 0 = pas encore programmé
bool  g_mwDone    = false;
int   g_mwIdx     = -2;               // prochain symbole à essayer (on descend : masquer ne décale pas les précédents)
int   g_mwHidden  = 0;
void TrimMarketWatchTick()
{
   if(g_mwDone || MQLInfoInteger(MQL_TESTER)) return;
   ulong now = GetTickCount64();
   if(g_mwStartMs == 0) { g_mwStartMs = now + 60000; return; }
   if(now < g_mwStartMs) return;
   if(g_mwIdx == -2) g_mwIdx = SymbolsTotal(true) - 1;
   for(int k = 0; k < 5 && g_mwIdx >= 0; k++, g_mwIdx--)   // 5 par seconde ; chaque symbole essayé une seule fois
   {
      string s = SymbolName(g_mwIdx, true);
      if(s == "" || s == _Symbol) continue;
      if(SymbolSelect(s, false)) g_mwHidden++;
   }
   ResetLastError();
   if(g_mwIdx < 0)
   {
      g_mwDone = true;
      if(g_mwHidden > 0) Log("Market Watch : " + IntegerToString(g_mwHidden) + " symbole(s) masqué(s), seul " + _Symbol + " reste (moins de CPU)");
   }
}
// [ARCHI] fin

void ArchiInit()
{
   if(!g_archiOn) return;
   StateLoad();
   StateSave();
   // 6.2.0 : fenêtre de lancement portée de 5 à 10 min. Une seule fois par compte : l'ancienne valeur par défaut (5) passe à 10 ;
   // toute autre valeur, ou un réglage fait ensuite depuis le bot, est conservé.
   if(!GlobalVariableCheck(GVN("mig_fenetre10")))
   {
      GlobalVariableSet(GVN("mig_fenetre10"), 1);
      if(PInt(P_SL_WIN) == 5) { g_pVal[P_SL_WIN] = 10; SaveParams(); Log("Fenêtre de lancement des créneaux portée de 5 à 10 min"); }
      GlobalVariablesFlush();
   }
   SlotsParse();
   g_slotGoalAt = GlobalVariableCheck(GVN("slentry")) ? (datetime)GlobalVariableGet(GVN("slentry")) : 0;   // [ARCHI] 6.6.0
   g_lastCutKey = GlobalVariableCheck(GVN("cutkey")) ? (long)GlobalVariableGet(GVN("cutkey")) : 0;          // [ARCHI] 7.0.0
   g_cutPending = GlobalVariableCheck(GVN("cutpend")) && g_goalActive;
   if(!g_cutPending) GlobalVariableDel(GVN("cutpend"));
   g_lastSlotKey = GlobalVariableCheck(GVN("slotkey")) ? (long)GlobalVariableGet(GVN("slotkey")) : 0;
   if(g_lastSlotKey > 0 && g_lastSlotKey < 10000000000)   // clé d'avant 6.2.0 (AAAAMMJJHH) → AAAAMMJJHHMM : créneau déjà consommé gardé
   {
      g_lastSlotKey *= 100;
      GlobalVariableSet(GVN("slotkey"), (double)g_lastSlotKey);
   }
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
   if(now - lastCmd >= 1000)  { lastCmd = now; CmdPoll(); }   // 1 s (avant 2 s) : commandes du bot prises en compte plus vite
   if(now - lastStat >= 5000) { lastStat = now; StatutWrite(); }
   if(now - lastSec >= 1000)
   {
      lastSec = now;
      EvFlushPending();
      CloseRetryTick();                  // fermeture voulue restée bloquée (marché fermé / trading désactivé) : nouvel essai
      SlotTick();
      ContTick();                        // [ARCHI] 7.8.0 : mode continu
      SlotEntryTick();                   // [ARCHI] 6.6.0 : fenêtre d'entrée après le créneau
      CutTick();                         // [ARCHI] 7.0.0 : coupure de l'objectif
      RepriseRetraitTimer();             // [ARCHI] 7.8.5 : reprise après retrait détectée aussi sans tick (marché fermé le week-end)
      TrimMarketWatchTick();             // [ARCHI] 7.11.1 : Market Watch réduit, 60 s après le démarrage, par petits lots
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
