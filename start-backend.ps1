Write-Host "Starting Loreweft backend..." -ForegroundColor Cyan
& (Join-Path $PSScriptRoot "restart-dev.ps1") -BackendOnly
exit $LASTEXITCODE
