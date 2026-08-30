$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$startScript = Join-Path $here "start_api_tunnel.ps1"
$taskName = "KaronlineLive API Tunnel"

if (-not (Test-Path $startScript)) {
    Write-Host "Script introuvable: $startScript" -ForegroundColor Red
    exit 1
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$startScript`""

$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

Write-Host "Tache planifiee installee: $taskName" -ForegroundColor Green
Write-Host "Elle demarrera l'API locale et le tunnel Cloudflare a chaque ouverture de session Windows."
Write-Host "Pour tester maintenant:"
Write-Host "Start-ScheduledTask -TaskName '$taskName'"
