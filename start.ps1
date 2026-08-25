Write-Host "Starting Loreweft local services..." -ForegroundColor Cyan
& (Join-Path $PSScriptRoot "restart-dev.ps1")
exit $LASTEXITCODE
