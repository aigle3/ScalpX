# ScalpX — robot de trading multicompte (MT5 + bot Telegram central)

Dépôt de publication. Chaque push sur `main` lance les tests GitHub (`.github/workflows/tests.yml`) ; s'ils passent,
la branche `stable` avance et le bot du VPS (qui ne lit QUE `stable`) propose « ⬆ Installer » dans Telegram.
- TRAVAILLER SUR LA BRANCHE `dev` (commits et pushes intermédiaires) : rien n'y est proposé au VPS.
- Ne fusionner dans `main` (puis pousser) qu'UNE FOIS la demande entièrement terminée et testée : une seule mise à jour proposée.
- Lancer les tests en local avant chaque push ; vérifier ensuite le résultat (`gh run list -R aigle3/ScalpX`).
- Filet de sécurité côté bot : un nouveau commit de `stable` n'est proposé qu'après 2 min sans autre publication ;
  plusieurs commits d'affilée sont proposés UN PAR UN, dans l'ordre (chaque commit doit donc être installable seul).

## Structure
- `bot/` : bot central Python 3.11+ (python-telegram-bot v21, SQLite). Tests : `cd bot && python -m pytest -q` (stub telegram dans tests/stubs).
- `ea/MQL5/Experts/` : EA MQL5 `ScalpX_v5.mq5` + `SX_*.mqh` ; `ScalpX_v5.ex5` compilé (première installation seulement).
- `ea/MQL5/Presets/scalpx_v5.set` : préréglages (UTF-16 LE avec BOM).
- `ea/version.txt` : version publiée de l'EA.
- `linux/` : installation sur le VPS Ubuntu (Wine + Xvfb + services systemd) : `install.sh` (LF, pas de CRLF), `LISEZMOI.md`.
  Le VPS est sous Ubuntu uniquement (plus de Windows) : MT5, MetaEditor et le testeur tournent sous Wine.
  Dépôt PUBLIC : installation en une ligne `curl -fsSL https://raw.githubusercontent.com/aigle3/ScalpX/stable/linux/installer.sh | sudo bash`,
  mises à jour sans jeton. JAMAIS de secret, de numéro de compte réel ni de donnée personnelle dans le dépôt (code, tests, docs).
  Sauvegardes : dépôt PRIVÉ propre à chaque utilisateur (créé par le bot), chiffrées par son mot de passe.
- `README.md` : présentation publique (guides, schémas Mermaid) ; à tenir à jour.
- `docs/CHANGELOG.md` : historique des modifications de l'EA.

## Règles
- Ne JAMAIS modifier la logique de trading de l'EA sans demande explicite. Ajouts marqués `// [ARCHI] début/fin`.
- Encodages à conserver : .mq5/.mqh en UTF-8 (BOM si présent) + CRLF ; .set en UTF-16 LE + BOM . `.gitattributes` = `* -text` (aucune conversion).
- VERSIONS (obligatoire à CHAQUE commit, même minime) : 3 niveaux majeur.mineur.correctif, indépendants pour le bot et l'EA.
  N'incrémenter que le composant modifié. Correctif/petit changement → 3e chiffre ; nouvelle fonction → 2e (3e à 0) ;
  changement majeur / de comportement de trading / incompatible → 1er (2e et 3e à 0).
  - Bot : `bot/version.txt` (affichée dans Telegram).
  - EA : `ea/version.txt` ET `#define SX_VERSION "x.y.z"` (SX_Core.mqh) ET `#property version "x.yy"` (majeur.mineur sur 2 chiffres, ex. 5.03) dans ScalpX_v5.mq5 ;
    entrée dans `docs/CHANGELOG.md`. Le VPS recompile lui-même (le .ex5 du dépôt n'est pas recopié lors des mises à jour).
  - Le message de commit commence par les versions touchées, ex. « Bot 1.2.0 : … » ou « EA 5.3.1 : … ».
- Le bot ne recopie jamais `.env`, `config.toml`, `data/`. Toute nouvelle option de config doit avoir une valeur par défaut dans `core/config.py`.
- Les fichiers de `linux/` (hors .md) changés : le bot propose 🔔 🔧 Installation système (droits administrateur) ; le dire en publiant.
- Mettre à jour `docs/CHANGELOG.md` et les tests à chaque changement ; messages de commit en français, courts (affichés dans Telegram).
