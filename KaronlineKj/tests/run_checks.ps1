# Vérification complète : compilation des modules modifiés + scénario de fumée.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\run_checks.ps1
#
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root

$py = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
Write-Output ("PYTHON=" + $py)

& $py -m py_compile `
    (Join-Path $root 'KaronlineKj\lan_server.py') `
    (Join-Path $root 'KaronlineKj\core\central_auth.py') `
    (Join-Path $root 'KaronlineKj\ui\auth_dialog.py') `
    (Join-Path $root 'KaronlineKj\ui\main_window.py') `
    (Join-Path $root 'KaronlineKj\app.py') `
    (Join-Path $PSScriptRoot 'smoke_auth.py')
Write-Output ("COMPILE_EXIT=" + $LASTEXITCODE)

if ($LASTEXITCODE -eq 0) {
    & $py (Join-Path $PSScriptRoot 'smoke_auth.py')
    Write-Output ("SMOKE_EXIT=" + $LASTEXITCODE)
}