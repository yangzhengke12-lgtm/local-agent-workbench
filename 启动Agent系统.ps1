[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ManagerDir = Join-Path $Root "AI-Agent管理系统"

# Find Python
$Python = $null
$VenvPath = Join-Path $Root "venv\Scripts\python.exe"
$VenvPath2 = Join-Path $Root ".venv\Scripts\python.exe"

if (Test-Path $VenvPath) {
    $Python = $VenvPath
    Write-Host "[*] Using venv" -ForegroundColor Green
} elseif (Test-Path $VenvPath2) {
    $Python = $VenvPath2
    Write-Host "[*] Using .venv" -ForegroundColor Green
} else {
    Write-Host "[ERROR] No virtual environment found!" -ForegroundColor Red
    Write-Host "  Checked: $VenvPath" -ForegroundColor Red
    Write-Host "  Checked: $VenvPath2" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Set-Location $ManagerDir

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  AI-Agent Management System v4" -ForegroundColor Cyan
Write-Host "  Starting..." -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

& $Python manager.py

Write-Host ""
Read-Host "Press Enter to exit"
