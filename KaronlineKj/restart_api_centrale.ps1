# Redemarre l'API centrale KaronlineLive (port 8765) avec le code actuel,
# attend le tunnel Cloudflare (~35 s max) et verifie publiquement la signature
# de la NOUVELLE version (401 TOKEN INVALID sur GET /auth/me).
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

function Test-HttpOk($url) {
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 4
        return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500)
    } catch { return $false }
}

# 1) Arreter tout ancien processus lan_server.py sur le port 8765.
$candidates = Get-CimInstance Win32_Process -Filter "name = 'python.exe' or name = 'pythonw.exe'" `
    | Where-Object { $_.CommandLine -like "*lan_server.py*" }
foreach ($proc in $candidates) {
    Write-Host ("Arret de l'ancien processus PID {0}" -f $proc.ProcessId) -ForegroundColor Yellow
    try { Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop } catch { Write-Host $_.Exception.Message }
}
if (-not $candidates) { Write-Host "Aucun ancien processus lan_server.py en cours." }

Start-Sleep -Milliseconds 800

# 2) Relancer le serveur avec le python du venv (sinon python systeme).
$venvPython = Join-Path $here ".venv\Scripts\python.exe"
$pythonExe = "python"
if (Test-Path $venvPython) { $pythonExe = $venvPython }
Write-Host ("Lancement serveur : {0} lan_server.py --port 8765" -f $pythonExe)
Start-Process $pythonExe -ArgumentList "lan_server.py --port 8765" -WorkingDirectory $here -WindowStyle Hidden

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

# 3) Verifier le tunnel Cloudflare (le relancer s'il manque, avec capture logs).
$cloudflared = "$env:LOCALAPPDATA\cloudflared\cloudflared.exe"
$tunnelOut = Join-Path $env:TEMP "kl_tunnel.out.log"
$tunnelErr = Join-Path $env:TEMP "kl_tunnel.err.log"

$running = Get-CimInstance Win32_Process -Filter "name = 'cloudflared.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*karonlinelive-lan*" }
if (-not $running) {
    if (Test-Path $cloudflared) {
        Write-Host "Relance du tunnel karonlinelive-lan..." -ForegroundColor Cyan
        Remove-Item $tunnelOut, $tunnelErr -ErrorAction SilentlyContinue
        Start-Process $cloudflared -ArgumentList "tunnel", "run", "karonlinelive-lan" `
            -WindowStyle Hidden -RedirectStandardOutput $tunnelOut -RedirectStandardError $tunnelErr
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

# 4) Sonde publique avec patience : jusqu'a 12 x 3 s pour absorber le delai
#    d'etablissement du tunnel apres un demarrage a froid.
Write-Host "Sonde publique https://api.karonlinelive.com/auth/me ..." -ForegroundColor Cyan
$publicOk = $false
for ($attempt = 1; $attempt -le 12; $attempt++) {
    try {
        Invoke-RestMethod -Uri "https://api.karonlinelive.com/auth/me" -TimeoutSec 8 | Out-Null
        Write-Host "[INATTENDU] 200 sur /auth/me sans jeton ?" -ForegroundColor Yellow
        break
    } catch {
        $resp = $_.Exception.Response
        $status = if ($resp) { [int]$resp.StatusCode } else { 0 }
        if ($status -eq 401) {
            $publicOk = $true
            break
        }
    }
    Write-Output ("   {0}/12 - origin pas encore routable, nouvelle tentative dans 3 s..." -f $attempt)
    Start-Sleep -Seconds 3
}

if ($publicOk) {
    Write-Host "[OK] Nouvelle version EN LIGNE (401 TOKEN INVALID)." -ForegroundColor Green
} else {
    Write-Host "[ECHEC] Toujours injoignable apres ~35 s. Diagnostic :" -ForegroundColor Red
    foreach ($lf in @($tunnelErr, $tunnelOut)) {
        if (Test-Path $lf) {
            Write-Host ("--- " + $lf + " ---")
            Get-Content $lf -Tail 15 | ForEach-Object { Write-Host ("   | " + $_) }
        }
    }
    Write-Host ""
    Write-Host "Remedies possibles :" -ForegroundColor Cyan
    Write-Host " * login/credentials : executer 'cloudflared tunnel login' puis relancer ce script"
    Write-Host " * tunnel inconnu    : verifier 'cloudflared tunnel list'"
    Write-Host " * route DNS absente : dashboard Cloudflare > DNS > CNAME api -> <ID>.cfargotunnel.com (proxifie)"
}
exit 0
