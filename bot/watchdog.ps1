# Surveillance du bot ScalpX (tâche « ScalpX Watchdog », toutes les 2 min, lancée par watchdog.vbs sans fenêtre).
# Redémarrage voulu (data\restarting = « horodatage pid ») : attend la fin de l'ancien bot puis relance tout de suite.
# Bot vivant : rien.
# Bot absent deux passages de suite : relance de la tâche « ScalpX Bot » (arrêt puis relance si elle est bloquée).
$ErrorActionPreference = 'SilentlyContinue'
$root  = Split-Path -Parent $MyInvocation.MyCommand.Path
$data  = Join-Path $root 'data'
$logs  = Join-Path $data 'logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$logf  = Join-Path $logs 'watchdog.log'
$state = Join-Path $data 'watchdog.state'
$task  = 'ScalpX Bot'

function Log($m) {
    if ((Test-Path $logf) -and (Get-Item $logf).Length -gt 1MB) { Move-Item $logf "$logf.1" -Force }
    Add-Content -Path $logf -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $m) -Encoding UTF8
}

# port du verrou du bot (config.toml, sinon valeur par défaut)
$port = 47653
$cfg = Join-Path $root 'config.toml'
if (Test-Path $cfg) {
    $m = Select-String -Path $cfg -Pattern '^\s*lock_port\s*=\s*(\d+)' | Select-Object -First 1
    if ($m) { $port = [int]$m.Matches[0].Groups[1].Value }
}

function Alive {
    $c = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $c.BeginConnect('127.0.0.1', $port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne(2000)) { return $false }
        $c.EndConnect($iar)
        return $true
    } catch { return $false } finally { $c.Close() }
}

$mark = Join-Path $data 'restarting'
if (Test-Path $mark) {
    # redémarrage voulu (mise à jour, bouton) : attendre la fin de l'ancien bot, puis relancer tout de suite
    $parts = "$(Get-Content $mark -TotalCount 1)".Trim() -split '\s+'
    $old = 0
    if ($parts.Count -gt 1) { $old = [int]$parts[1] }
    $age = (New-TimeSpan -Start (Get-Item $mark).LastWriteTime -End (Get-Date)).TotalSeconds
    if ($old -gt 0 -and (Get-Process -Id $old)) {
        Log ("redémarrage voulu : attente de la fin de l'ancien bot (pid {0}, annoncé il y a {1:N0} s)" -f $old, $age)
        $left = [int][Math]::Max(10, 240 - $age)
        Wait-Process -Id $old -Timeout $left
        if (Get-Process -Id $old) {
            Log "ancien bot (pid $old) toujours là après 4 min : arrêt forcé"
            Stop-Process -Id $old -Force
            Start-Sleep -Seconds 3
        }
    } elseif ((-not $old) -and $age -lt 300 -and (Alive)) {
        exit 0                                  # ancien format de marqueur (sans pid) : le bot n'est pas encore arrêté
    }
    if (Alive) { Log 'nouveau bot déjà en ligne'; Remove-Item $state -Force; exit 0 }
    for ($i = 1; $i -le 4; $i++) {
        Log "relance du bot (redémarrage voulu), essai $i"
        schtasks /run /tn $task | Out-Null
        for ($j = 0; $j -lt 10; $j++) { Start-Sleep -Seconds 5; if (Alive) { Log 'bot redémarré'; Remove-Item $state -Force; exit 0 } }
    }
    Log 'relance non confirmée : nouvel essai au prochain passage'
    exit 0
}

if (Alive) {
    if (Test-Path $state) { Remove-Item $state -Force; Log 'bot de nouveau en ligne' }
    exit 0
}

$miss = 0
if (Test-Path $state) { $miss = [int](Get-Content $state -TotalCount 1) }
$miss++
Set-Content -Path $state -Value $miss
if ($miss -lt 2) { Log 'bot absent (1er constat) : nouvelle vérification dans 2 min'; exit 0 }

$st = (Get-ScheduledTask -TaskName $task).State
if ("$st" -eq 'Running' -and $miss -ge 3) {
    Log "bot absent depuis $miss passages alors que la tâche tourne : arrêt puis relance"
    schtasks /end /tn $task | Out-Null
    Start-Sleep -Seconds 5
}
Log "bot absent ($miss passages) : relance de la tâche « $task » (état : $st)"
schtasks /run /tn $task | Out-Null
