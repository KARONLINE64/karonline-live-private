# Redemarre l'API centrale KaronlineLive (port 8765) avec le code actuel,
# puis verifie la signature de la NOUVELLE version sur l'URL publique.
#
# Usage :
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\restart_api_centrale.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\restart_api_centrale.ps1 -SkipPublicProbe

param(
    [switch]$SkipPublicProbe
)

$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

Write-Host "=== Restart API centrale KaronlineLive ===" -ForegroundColor Cyan

# 1) Arreter tout ancien processus lan_server.py sur le port 8765.
$candidates = Get-CimInstance Win32_Process -Filter "name = 'python.exe' or name = 'pythonw.exe'" `
    | Where-Object { $_.CommandLine -like "*lan_server.py*" }
foreach ($proc in $candidates) {
    Write-Host ("Arret de l'ancien processus PID {0} ({1})" -f $proc.ProcessId, $proc.CommandLine) -ForegroundColor Yellow
    try { Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop } catch { Write-Host $_.Exception.Message }
}
if (-not $candidates) { Write-Host "Aucun ancien processus lan_server.py en cours." }

Start-Sleep -Milliseconds 800

# 2) Relancer le serveur avec le python du venv (sinon python systeme).
$venvPython = Join-Path $here ".venv\Scripts\python.exe"
$pythonExe = "python"
if (Test-Path $venvPython) { $pythonExe = $venvPython }
Write-Host ("Lancement : {0} lan_server.py --port 8765" -f $pythonExe)
Start-Process $pythonExe -ArgumentList "lan_server.py --port 8765" -WorkingDirectory $here -WindowStyle Hidden

# 3) Attendre que le catalogue local reponde.
function Test-HttpOk($url) {
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 4
        return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500)
    } catch { return $false }
}

$localOk = $false
for ($i = 1; $i -le 15; $i++) {
    if (Test-HttpOk "http://localhost:8765/catalogue") { $localOk = $true; break }
    Start-Sleep -Seconds 1
}
if ($localOk) {
    Write-Host "[OK] Serveur local 8765 actif avec le nouveau code." -ForegroundColor Green
} else {
    Write-Host "[ECHEC] Le serveur local ne repond pas apres 15 s." -ForegroundColor Red
    exit 1
}

# 4) Verifier le tunnel Cloudflare (le relancer s'il manque).
$tunnelScript = Join-Path $here "start_api_tunnel.ps1"
$cloudflared = "$env:LOCALAPPDATA\cloudflared\cloudflared.exe"
$running = Get-CimInstance Win32_Process -Filter "name = 'cloudflared.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*karonlinelive-lan*" }
if (-not $running) {
    if (Test-Path $cloudflared) {
        Write-Host "Relance du tunnel karonlinelive-lan..." -ForegroundColor Cyan
        Start-Process $cloudflared -ArgumentList "tunnel run karonlinelive-lan" -WindowStyle Hidden
    } else {
        Write-Host "[ATTENTION] cloudflared introuvable : tunnel non relance." -ForegroundColor Yellow
    }
} else {
    Write-Host "Tunnel karonlinelive-lan deja actif."
}

if ($SkipPublicProbe) {
    Write-Host "Probe publique ignoree (-SkipPublicProbe)." -ForegroundColor DarkGray
    exit 0
}

# 5) Signature de la nouvelle version : GET public /auth/me sans jeton
#    doit renvoyer 401 avec {"error":"TOKEN INVALID"}.
Write-Host "Verification publique https://api.karonlinelive.com/auth/me ..." -ForegroundColor Cyan
try {
    Invoke-RestMethod -Uri "https://api.karonlinelive.com/auth/me" -TimeoutSec 20 |
        Out-Null
    Write-Host "[INATTENDU] 200 sur /auth/me sans jeton ?" -ForegroundColor Yellow
} catch {
    $resp = $_.Exception.Response
    if ($resp -and [int]$resp.StatusCode -eq 401) {
        Write-Host "[OK] Nouvelle version EN LIGNE (401 TOKEN INVALID)." -ForegroundColor Green
    } elseif (($_ | Out-String) -match "530|502|503") {
        Write-Host "[EN ATTENTE] Origin injoignable (530) - attends 30-60 s (propagation tunnel/demarrage), puis relance cette sonde." -ForegroundColor Yellow
    } else {
        Write-Host ("[ECHEC] Reponse : {0}" -f $_.Exception.Message) -ForegroundColor Red
        Write-Host "L'ancien processus tournait-il encore ? Verifie manuellement :" -ForegroundColor Red
        Write-Host "  curl.exe -i https://api.karonlinelive.com/auth/me"
    }
}