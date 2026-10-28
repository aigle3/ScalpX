# ScalpX - installation et mise à jour sur le VPS.
# Relançable sans risque : ne touche jamais à .env, config.toml (s'ils existent), data\, ni aux comptes déjà créés.
$ErrorActionPreference = 'Stop'
$Pkg    = Split-Path -Parent $MyInvocation.MyCommand.Path
$BotDir = 'C:\ScalpX\bot'
$MtDir  = 'C:\MT5'
$Base   = Join-Path $MtDir 'BASE'
$Utf8   = New-Object System.Text.UTF8Encoding($false)

function Step($t) { Write-Host ''; Write-Host "=== $t" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "  [OK] $t" -ForegroundColor Green }
function Warn($t) { Write-Host "  [!]  $t" -ForegroundColor Yellow }
function Stop-Install($t) {
    Write-Host ''; Write-Host "  [ERREUR] $t" -ForegroundColor Red; Write-Host ''
    Read-Host 'Appuie sur Entrée pour fermer'
    exit 1
}
function Ask($q, $def = '') {
    $s = ''
    if ($def) { $s = " [$def]" }
    $r = Read-Host "  $q$s"
    if ([string]::IsNullOrWhiteSpace($r)) { return $def }
    return $r.Trim()
}

Write-Host ''
Write-Host '  ScalpX - installation du pilotage multicompte' -ForegroundColor White
Write-Host "  Paquet : $Pkg"

# ---------------------------------------------------------------- 1. Python
Step '1/6  Python 3.11 ou plus récent'
function Get-PyVersion($exe, $pre) {
    try {
        $o = & $exe @pre -c "import sys; print(sys.version_info[0]*100+sys.version_info[1])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $o) { return [int]$o }
    } catch { }
    return 0
}
function Find-Python {
    $cands = New-Object System.Collections.ArrayList
    [void]$cands.Add(@('py', @('-3')))
    [void]$cands.Add(@('python', @()))
    foreach ($d in @("$env:LOCALAPPDATA\Programs\Python", 'C:\Program Files')) {
        if (Test-Path $d) {
            Get-ChildItem $d -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue | Sort-Object Name -Descending | ForEach-Object {
                $e = Join-Path $_.FullName 'python.exe'
                if (Test-Path $e) { [void]$cands.Add(@($e, @())) }
            }
        }
    }
    foreach ($c in $cands) {
        if ((Get-PyVersion $c[0] $c[1]) -ge 311) { return ,$c }
    }
    return $null
}
$py = Find-Python
if (-not $py) {
    Warn 'Python 3.11+ introuvable : installation automatique (winget)...'
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Stop-Install 'winget est absent. Installe Python 3.12 depuis python.org (coche "Add python.exe to PATH"), puis relance INSTALL.bat.'
    }
    & winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
    $py = Find-Python
    if (-not $py) { Stop-Install 'Python n''a pas pu être installé. Installe-le depuis python.org puis relance INSTALL.bat.' }
}
Ok "Python trouvé : $($py[0]) $($py[1] -join ' ')"

# ---------------------------------------------------------------- 2. BASE MT5
Step "2/6  MetaTrader 5 : dossier BASE ($Base)"
if (-not (Test-Path (Join-Path $Base 'terminal64.exe'))) {
    Warn 'BASE absent : je le crée à partir d''une installation MT5 de ton broker.'
    $found = @()
    foreach ($root in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if ($root -and (Test-Path $root)) {
            Get-ChildItem $root -Directory -ErrorAction SilentlyContinue | ForEach-Object {
                if (Test-Path (Join-Path $_.FullName 'terminal64.exe')) { $found += $_.FullName }
            }
        }
    }
    $src = $null
    if ($found.Count -eq 1) {
        $src = $found[0]
        if ((Ask "Copier l'installation MT5 trouvée : $src ? (o/n)" 'o') -notmatch '^[oOyY]') { $src = $null }
    } elseif ($found.Count -gt 1) {
        $i = 0
        foreach ($f in $found) { $i++; Write-Host "   $i) $f" }
        $n = Ask 'Numéro de l''installation à utiliser'
        if ($n -match '^\d+$' -and [int]$n -ge 1 -and [int]$n -le $found.Count) { $src = $found[[int]$n - 1] }
    }
    if (-not $src) { $src = Ask 'Dossier d''installation MT5 de ton broker (celui qui contient terminal64.exe)' }
    if (-not $src -or -not (Test-Path (Join-Path $src 'terminal64.exe'))) {
        Stop-Install 'Aucune installation MT5 trouvée. Installe MetaTrader 5 (celui de ton broker) sur ce VPS, ne te connecte à aucun compte, puis relance INSTALL.bat.'
    }
    New-Item -ItemType Directory -Force -Path $MtDir | Out-Null
    Write-Host '  Copie de MT5 (quelques minutes)...'
    & robocopy $src $Base /E /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { Stop-Install "Copie de MT5 impossible (code robocopy $LASTEXITCODE)." }
    Write-Host '  Premier démarrage de MT5 en mode portable (création de l''arborescence)...'
    $p = Start-Process -FilePath (Join-Path $Base 'terminal64.exe') -ArgumentList '/portable' -PassThru
    $t0 = Get-Date
    while (((Get-Date) - $t0).TotalSeconds -lt 120) {
        if ((Test-Path (Join-Path $Base 'MQL5\Experts')) -and (Test-Path (Join-Path $Base 'Profiles\Charts'))) { break }
        Start-Sleep -Seconds 2
    }
    Start-Sleep -Seconds 10
    try { [void]$p.CloseMainWindow() } catch { }
    Start-Sleep -Seconds 5
    try { if (-not $p.HasExited) { $p.Kill() } } catch { }
    Start-Sleep -Seconds 3
    Ok 'BASE créé.'
} else {
    Ok 'BASE déjà présent (conservé).'
}
if (Test-Path (Join-Path $Base 'Config\accounts.dat')) {
    Warn 'BASE contient déjà un compte mémorisé (accounts.dat). BASE doit rester SANS compte : supprime Config\accounts.dat dans BASE.'
}

Step '3/6  EA ScalpX dans BASE'
$expDir = Join-Path $Base 'MQL5\Experts'
$preDir = Join-Path $Base 'MQL5\Presets'
New-Item -ItemType Directory -Force -Path $expDir, $preDir | Out-Null
if (-not (Test-Path (Join-Path $expDir 'ScalpX_v5.ex5'))) {
    Copy-Item (Join-Path $Pkg 'ea\MQL5\Experts\*') $expDir -Force
    Copy-Item (Join-Path $Pkg 'ea\MQL5\Presets\*') $preDir -Force
    Copy-Item (Join-Path $Pkg 'ea\version.txt') (Join-Path $Base 'version.txt') -Force
    $ver = (Get-Content (Join-Path $Base 'version.txt') -Raw).Trim()
    Ok "EA installé (ScalpX_v5.ex5 compilé, sources, .set) - version $ver"
} else {
    Ok 'EA déjà présent dans BASE : conservé (les mises à jour de l''EA passent par le bot, bouton Installer dans Telegram).'
}
$bad = @(Get-ChildItem (Join-Path $Base 'Profiles\Charts') -Recurse -Filter *.chr -ErrorAction SilentlyContinue |
         Where-Object { (Get-Content $_.FullName -Raw -ErrorAction SilentlyContinue) -match '(?i)<expert>' })
if ($bad.Count -gt 0) { Warn "$($bad.Count) graphique(s) de BASE portent un EA : retire-le (sinon EA en double dans chaque compte)." }

# ---------------------------------------------------------------- 4. Bot
Step "4/6  Bot Python ($BotDir)"
$sched = Get-ScheduledTask -TaskName 'ScalpX Bot' -ErrorAction SilentlyContinue
if ($sched) { Stop-ScheduledTask -TaskName 'ScalpX Bot' -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2 }
New-Item -ItemType Directory -Force -Path $BotDir | Out-Null
$xf = @('.env')
if (Test-Path (Join-Path $BotDir 'config.toml')) { $xf += 'config.toml' }
& robocopy (Join-Path $Pkg 'bot') $BotDir /E /XD .venv data __pycache__ .pytest_cache /XF $xf /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { Stop-Install "Copie du bot impossible (code robocopy $LASTEXITCODE)." }
Ok 'Fichiers du bot copiés (.env, config.toml et data\ conservés s''ils existaient).'

$venvPy = Join-Path $BotDir '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    Write-Host '  Création de l''environnement Python...'
    & $py[0] @($py[1]) -m venv (Join-Path $BotDir '.venv')
    if ($LASTEXITCODE -ne 0) { Stop-Install 'Création de l''environnement Python impossible.' }
}
Write-Host '  Installation des bibliothèques (1 à 3 minutes)...'
& $venvPy -m pip install --disable-pip-version-check -q -r (Join-Path $BotDir 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Stop-Install 'Installation des bibliothèques impossible (internet ?).' }
Ok 'Bibliothèques installées.'

# ---------------------------------------------------------------- 5. Telegram + config
Step '5/6  Telegram et configuration'
$envFile = Join-Path $BotDir '.env'
if (-not (Test-Path $envFile)) {
    Write-Host '  Dans Telegram : @BotFather -> /newbot -> copie le TOKEN. Ton chat_id : écris à @userinfobot.'
    Write-Host '  Utilise un NOUVEAU token (l''ancien a fuité : /revoke dans @BotFather).' -ForegroundColor Yellow
    $tok = ''
    for ($i = 0; $i -lt 3; $i++) {
        $tok = Ask 'TELEGRAM_TOKEN'
        if ($tok -match '^\d{6,}:[A-Za-z0-9_-]{30,}$') { break }
        Warn 'Format inattendu (attendu : 123456789:AAxxxxxxxx...).'
    }
    $chat = ''
    for ($i = 0; $i -lt 3; $i++) {
        $chat = Ask 'Ton chat_id (nombre)'
        if ($chat -match '^-?\d{5,}(,-?\d{5,})*$') { break }
        Warn 'Format inattendu (attendu : un nombre, ex 123456789).'
    }
    [System.IO.File]::WriteAllText($envFile, "TELEGRAM_TOKEN=$tok`r`nALLOWED_CHAT_IDS=$chat`r`n", $Utf8)
    Ok '.env écrit.'
} else {
    Ok '.env existant conservé.'
}
$cfgFile = Join-Path $BotDir 'config.toml'
$srv = Ask 'FACULTATIF - serveur(s) MT5 à pré-enregistrer, séparés par des virgules. Entrée = passer (saisi au premier compte, puis mémorisé)'
if ($srv) {
    $list = @($srv -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($list.Count -gt 0) {
        $q = ($list | ForEach-Object { '"' + $_ + '"' }) -join ', '
        $t = [System.IO.File]::ReadAllText($cfgFile)
        $t = $t -replace '(?m)^default_server\s*=.*$', ('default_server = "' + $list[0] + '"')
        $t = $t -replace '(?m)^servers\s*=.*$', ('servers = [' + $q + ']')
        [System.IO.File]::WriteAllText($cfgFile, $t, $Utf8)
        Ok "Serveurs : $($list -join ', ')  (tu pourras en ajouter d'autres depuis Telegram : bouton Autre serveur)"
    }
}

# ---- mises à jour depuis GitHub (dépôt privé, jeton en lecture seule)
$repo = 'aigle3/ScalpX'
$envTxt = [System.IO.File]::ReadAllText($envFile)
if ($envTxt -notmatch '(?m)^GITHUB_TOKEN=\S+') {
    Write-Host '  Jeton GitHub en lecture seule (github_pat_...) pour recevoir les mises à jour.'
    $gt = ''
    for ($i = 0; $i -lt 3; $i++) {
        $gt = Ask 'GITHUB_TOKEN (Entrée = plus tard)'
        if (-not $gt -or $gt -match '^(github_pat_|ghp_)[A-Za-z0-9_]{20,}$') { break }
        Warn 'Format inattendu (attendu : github_pat_...).'
    }
    if ($gt) {
        [System.IO.File]::WriteAllText($envFile, $envTxt.TrimEnd() + "`r`nGITHUB_TOKEN=$gt`r`n", $Utf8)
        Ok 'Jeton GitHub enregistré dans .env.'
    } else {
        Warn 'Sans jeton, pas de mises à jour : ajoute GITHUB_TOKEN=... dans C:\ScalpX\bot\.env puis redémarre le bot.'
    }
} else {
    Ok 'Jeton GitHub déjà présent dans .env.'
}
# ---- surveillance externe (facultatif) : healthchecks.io prévient si le VPS ou le bot s'arrête
$envTxt = [System.IO.File]::ReadAllText($envFile)
if ($envTxt -notmatch '(?m)^HEALTHCHECK_URL=\S+') {
    Write-Host '  FACULTATIF - surveillance : crée un check gratuit sur healthchecks.io (période 1 min, délai de grâce 5 min)'
    Write-Host '  et colle son URL de ping (https://hc-ping.com/...). Tu seras prévenu si le VPS ou le bot s''arrête.'
    $hc = Ask 'HEALTHCHECK_URL (Entrée = passer)'
    if ($hc -match '^https://\S+$') {
        [System.IO.File]::WriteAllText($envFile, $envTxt.TrimEnd() + "`r`nHEALTHCHECK_URL=$hc`r`n", $Utf8)
        Ok 'Surveillance externe enregistrée dans .env.'
    }
}
$t = [System.IO.File]::ReadAllText($cfgFile)
$t = [regex]::Replace($t, '(?ms)^\[update\].*?(?=^\[|\z)', '')
$t = $t.TrimEnd() + "`r`n`r`n[update]`r`ngithub  = `"$repo`"            # mises à jour proposées dans Telegram`r`nbranch  = `"main`"`r`nsource  = `"C:\\ScalpX\\release`"`r`ncheck_s = 60`r`n"
[System.IO.File]::WriteAllText($cfgFile, $t, $Utf8)
Ok "Mises à jour automatiques depuis GitHub : $repo"

# ---------------------------------------------------------------- 6. Vérification + démarrage auto
Step '6/6  Vérification et démarrage automatique'
New-Item -ItemType Directory -Force -Path (Join-Path $env:APPDATA 'MetaQuotes\Terminal\Common\Files') | Out-Null
Push-Location $BotDir
& $venvPy main.py --check
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { Warn 'La vérification signale des erreurs (voir [ERREUR] ci-dessus). Corrige-les puis relance INSTALL.bat.' } else { Ok 'Vérification réussie.' }

$user = "$env:USERDOMAIN\$env:USERNAME"
$action    = New-ScheduledTaskAction -Execute (Join-Path $BotDir '.venv\Scripts\pythonw.exe') -Argument 'main.py' -WorkingDirectory $BotDir
$trigger   = New-ScheduledTaskTrigger -AtLogOn -User $user
$trigger.Delay = 'PT30S'
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
             -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'ScalpX Bot' -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Ok 'Tâche planifiée "ScalpX Bot" créée (démarre à l''ouverture de session, relancée si elle s''arrête).'

if ($code -eq 0) {
    if ((Ask 'Démarrer le bot maintenant ? (o/n)' 'o') -match '^[oOyY]') {
        Start-ScheduledTask -TaskName 'ScalpX Bot'
        Ok 'Bot démarré : ouvre Telegram et envoie un message à ton bot.'
    }
}
Write-Host ''
Write-Host '  Terminé. Rappel : la session Windows du VPS doit rester ouverte (les terminaux MT5 ont besoin du bureau).' -ForegroundColor White
Write-Host '  Mises à jour : rien à copier. À chaque nouvelle version publiée sur GitHub, le bot propose « Installer » dans Telegram.'
Write-Host ''
Read-Host 'Appuie sur Entrée pour fermer'
