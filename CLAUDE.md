# ScalpX — robot de trading multicompte (MT5 + bot Telegram central)

Dépôt de publication : chaque commit sur `main` est téléchargé par le bot du VPS (toutes les minutes),
qui propose « ⬆ Installer » dans Telegram. Ne publier que du code testé.

## Structure
- `bot/` : bot central Python 3.11+ (python-telegram-bot v21, SQLite). Tests : `cd bot && python -m pytest -q` (stub telegram dans tests/stubs).
- `ea/MQL5/Experts/` : EA MQL5 `ScalpX_v5.mq5` + `SX_*.mqh` ; `ScalpX_v5.ex5` compilé (première installation seulement).
- `ea/MQL5/Presets/scalpx_v5.set` : préréglages (UTF-16 LE avec BOM).
- `ea/version.txt` : version publiée de l'EA.
- `INSTALL.bat`, `install.ps1`, `LISEZMOI.txt` : installation sur le VPS (Windows Server 2016, PowerShell 5.1).
- `docs/CHANGELOG.md` : historique des modifications de l'EA.

## Règles
- Ne JAMAIS modifier la logique de trading de l'EA sans demande explicite. Ajouts marqués `// [ARCHI] début/fin`.
- Encodages à conserver : .mq5/.mqh en UTF-8 (BOM si présent) + CRLF ; .set en UTF-16 LE + BOM ; .ps1 en UTF-8 BOM + CRLF. `.gitattributes` = `* -text` (aucune conversion).
- VERSIONS (obligatoire à CHAQUE commit, même minime) : 3 niveaux majeur.mineur.correctif, indépendants pour le bot et l'EA.
  N'incrémenter que le composant modifié. Correctif/petit changement → 3e chiffre ; nouvelle fonction → 2e (3e à 0) ;
  changement majeur / de comportement de trading / incompatible → 1er (2e et 3e à 0).
  - Bot : `bot/version.txt` (affichée dans Telegram).
  - EA : `ea/version.txt` ET `#define SX_VERSION "x.y.z"` (SX_Core.mqh) ET `#property version "x.yy"` (majeur.mineur sur 2 chiffres, ex. 5.03) dans ScalpX_v5.mq5 ;
    entrée dans `docs/CHANGELOG.md`. Le VPS recompile lui-même (le .ex5 du dépôt n'est pas recopié lors des mises à jour).
  - Le message de commit commence par les versions touchées, ex. « Bot 1.2.0 : … » ou « EA 5.3.1 : … ».
- Le bot ne recopie jamais `.env`, `config.toml`, `data\`. Toute nouvelle option de config doit avoir une valeur par défaut dans `core/config.py`.
- Mettre à jour `docs/CHANGELOG.md` et les tests à chaque changement ; messages de commit en français, courts (affichés dans Telegram).
