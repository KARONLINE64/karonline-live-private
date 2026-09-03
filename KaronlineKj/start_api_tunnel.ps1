$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$cloudflared = "$env:LOCALAPPDATA\cloudflared\cloudflared.exe"
$tunnelName = "karonlinelive-lan"

if (-not (Test-Path $cloudflared)) {
    Write-Host "cloudflared introuvable a $cloudflared" -ForegroundColor Red
    Write-Host "Telechargez-le depuis: https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    exit 1
}

function Test-HttpOk($url) {
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
        return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500)
    } catch {
        return $false
    }
}

if (-not (Test-HttpOk "http://localhost:8765/catalogue")) {
    Write-Host "Demarrage du serveur API local KaronlineLive (port 8765)..." -ForegroundColor Cyan
    # Preferer le python du venv du projet si present, sinon le PATH systeme.
    $venvPython = Join-Path $here ".venv\Scripts\python.exe"
    $pythonExe = "python"
    if (Test-Path $venvPython) { $pythonExe = $venvPython }
    $libraryPath = Join-Path $here "SERVER"
    Start-Process $pythonExe -ArgumentList "lan_server.py --port 8765 --library `"$libraryPath`"" -WorkingDirectory $here -WindowStyle Hidden
} else {
    Write-Host "Serveur API local deja actif." -ForegroundColor Green
}

$existingTunnel = Get-CimInstance Win32_Process -Filter "name = 'cloudflared.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*tunnel run $tunnelName*" }

if (-not $existingTunnel) {
    Write-Host "Demarrage du tunnel Cloudflare $tunnelName..." -ForegroundColor Cyan
    Start-Process $cloudflared -ArgumentList "tunnel run $tunnelName" -WindowStyle Hidden
} else {
    Write-Host "Tunnel Cloudflare deja actif." -ForegroundColor Green
}

Write-Host "Verification de l'API publique..." -ForegroundColor Cyan
try {
    $response = Invoke-WebRequest -Uri "https://api.karonlinelive.com/catalogue" -UseBasicParsing -TimeoutSec 20
    Write-Host "API publique OK: HTTP $($response.StatusCode)" -ForegroundColor Green
} catch {
    Write-Host "API publique non disponible: $($_.Exception.Message)" -ForegroundColor Yellow
    exit 2
}
