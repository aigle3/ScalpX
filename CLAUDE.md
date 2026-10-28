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
- Modification de l'EA : changer `ea/version.txt` ET `#define SX_VERSION` (SX_Core.mqh) ET `#property version` (format X.YY) ; le VPS recompile lui-même (le .ex5 du dépôt n'est pas recopié lors des mises à jour).
- Le bot ne recopie jamais `.env`, `config.toml`, `data\`. Toute nouvelle option de config doit avoir une valeur par défaut dans `core/config.py`.
- Mettre à jour `docs/CHANGELOG.md` et les tests à chaque changement ; messages de commit en français, courts (affichés dans Telegram).
