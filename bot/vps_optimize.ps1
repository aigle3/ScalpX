# ScalpX : optimisation du VPS (Bot 3.10.0), lancée par le bot : 🛠 Système → ⚡ Optimiser le VPS.
# Idempotent : relancé, il ne refait que ce qui manque. Une ligne JSON par réglage : {"k": clé, "s": état, "d": détail}
# (Bot 3.10.1 : la fonction s'appelait « R », alias intégré de PowerShell pour Invoke-History : le script s'arrêtait)
# états : ok (fait), deja (déjà fait), absent (rien à faire), admin (droits administrateur requis), echec
param([string]$Paths = "", [string]$Procs = "", [string]$BotTask = "ScalpX Bot")
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch {}

function Out-Result([string]$k, [string]$s, [string]$d = '') {
    Write-Output (([pscustomobject]@{ k = $k; s = $s; d = $d }) | ConvertTo-Json -Compress)
}

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
Out-Result 'admin' $(if ($admin) { 'ok' } else { 'non' })

# 1) Antivirus Defender : MT5 et le bot échangent des fichiers sans arrêt (statuts, commandes, journaux) : plus scannés
try {
    if (-not (Get-Command Add-MpPreference -ErrorAction SilentlyContinue)) { Out-Result 'defender' 'absent' }
    elseif (-not $admin) { Out-Result 'defender' 'admin' }
    else {
        $pref = Get-MpPreference
        $addP = @($Paths.Split(';') | Where-Object { $_ -and (@($pref.ExclusionPath) -notcontains $_) })
        $addX = @($Procs.Split(';') | Where-Object { $_ -and (@($pref.ExclusionProcess) -notcontains $_) })
        if ($addP.Count -gt 0) { Add-MpPreference -ExclusionPath $addP }
        if ($addX.Count -gt 0) { Add-MpPreference -ExclusionProcess $addX }
        $cpu = $false
        if ($pref.ScanAvgCPULoadFactor -ne 20) { Set-MpPreference -ScanAvgCPULoadFactor 20; $cpu = $true }   # analyses planifiées : 20 % de CPU max
        if ($addP.Count -gt 0 -or $addX.Count -gt 0 -or $cpu) {
            Out-Result 'defender' 'ok' ("{0} dossier(s), {1} programme(s) ajoutés ; analyses limitées à 20 % de CPU" -f $addP.Count, $addX.Count)
        } else { Out-Result 'defender' 'deja' }
    }
} catch { Out-Result 'defender' 'echec' $_.Exception.Message }

# 2) Services inutiles sur un VPS de trading : arrêtés et désactivés
function Disable-Services([string]$k, [string[]]$names) {
    try {
        $found = @($names | ForEach-Object { Get-Service -Name $_ -ErrorAction SilentlyContinue } | Where-Object { $_ })
        if ($found.Count -eq 0) { Out-Result $k 'absent'; return }
        $todo = @($found | Where-Object {
            $m = (Get-WmiObject Win32_Service -Filter ("Name='{0}'" -f $_.Name)).StartMode
            $_.Status -ne 'Stopped' -or $m -ne 'Disabled' })
        if ($todo.Count -eq 0) { Out-Result $k 'deja'; return }
        if (-not $admin) { Out-Result $k 'admin'; return }
        foreach ($s in $todo) {
            Set-Service -Name $s.Name -StartupType Disabled
            if ($s.Status -ne 'Stopped') { Stop-Service -Name $s.Name -Force -ErrorAction SilentlyContinue }
        }
        Out-Result $k 'ok' (($todo | ForEach-Object { $_.Name }) -join ', ')
    } catch { Out-Result $k 'echec' $_.Exception.Message }
}
Disable-Services 'wsearch' @('WSearch')
Disable-Services 'diagtrack' @('DiagTrack')
Disable-Services 'dmwappush' @('dmwappushservice')
Disable-Services 'maps' @('MapsBroker')
Disable-Services 'geo' @('lfsvc')
Disable-Services 'xbox' @('XblAuthManager', 'XblGameSave')

# 3) Gestionnaire de serveur : plus ouvert à chaque connexion (et fermé s'il tourne)
try {
    $chg = $false
    $key = 'HKCU:\Software\Microsoft\ServerManager'
    if (-not (Test-Path $key)) { New-Item -Path $key -Force | Out-Null }
    if ((Get-ItemProperty -Path $key -ErrorAction SilentlyContinue).DoNotOpenServerManagerAtLogon -ne 1) {
        Set-ItemProperty -Path $key -Name DoNotOpenServerManagerAtLogon -Value 1 -Type DWord; $chg = $true
    }
    $t = Get-ScheduledTask -TaskPath '\Microsoft\Windows\Server Manager\' -TaskName 'ServerManager' -ErrorAction SilentlyContinue
    if ($t -and $t.State -ne 'Disabled' -and $admin) { $t | Disable-ScheduledTask | Out-Null; $chg = $true }
    $p = @(Get-Process -Name 'ServerManager' -ErrorAction SilentlyContinue)
    if ($p.Count -gt 0) { $p | Stop-Process -Force -ErrorAction SilentlyContinue; $chg = $true }
    Out-Result 'servermanager' $(if ($chg) { 'ok' } else { 'deja' })
} catch { Out-Result 'servermanager' 'echec' $_.Exception.Message }

# 4) Alimentation « Performances élevées » (pas de bridage du processeur)
try {
    $hp = '8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c'
    $cur = (powercfg /getactivescheme) -join ' '
    if ($cur -match $hp) { Out-Result 'power' 'deja' }
    else {
        powercfg /setactive $hp | Out-Null
        if ((powercfg /getactivescheme) -join ' ' -match $hp) { Out-Result 'power' 'ok' }
        elseif (-not $admin) { Out-Result 'power' 'admin' }
        else { Out-Result 'power' 'echec' 'mode introuvable' }
    }
} catch { Out-Result 'power' 'echec' $_.Exception.Message }

# 5) Effets visuels : « ajuster afin d'obtenir les meilleures performances » (session RDP plus légère)
try {
    $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects'
    if (-not (Test-Path $key)) { New-Item -Path $key -Force | Out-Null }
    if ((Get-ItemProperty -Path $key -ErrorAction SilentlyContinue).VisualFXSetting -eq 2) { Out-Result 'visual' 'deja' }
    else { Set-ItemProperty -Path $key -Name VisualFXSetting -Value 2 -Type DWord; Out-Result 'visual' 'ok' }
} catch { Out-Result 'visual' 'echec' $_.Exception.Message }

# 6) Tâche du bot en « privilèges les plus élevés » : le bot tourne en administrateur après son prochain redémarrage
try {
    $t = Get-ScheduledTask -TaskName $BotTask -ErrorAction SilentlyContinue
    if (-not $t) { Out-Result 'task' 'absent' }
    elseif ($t.Principal.RunLevel -eq 'Highest') { Out-Result 'task' 'deja' }
    elseif (-not $admin) { Out-Result 'task' 'admin' }
    else {
        $pr = New-ScheduledTaskPrincipal -UserId $t.Principal.UserId -LogonType Interactive -RunLevel Highest
        Set-ScheduledTask -TaskName $BotTask -Principal $pr | Out-Null
        Out-Result 'task' 'ok' 'pris en compte au prochain redémarrage du bot'
    }
} catch { Out-Result 'task' 'echec' $_.Exception.Message }
