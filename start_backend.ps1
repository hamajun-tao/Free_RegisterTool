param(
    [string]$EnvName = "any-auto-register",
    [string]$BindHost = "0.0.0.0",
    [int]$Port = 8000,
    [switch]$RestartExisting = $true
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$conda = Get-Command conda -ErrorAction SilentlyContinue
if (-not $conda) {
    Write-Error "未找到 conda 命令。请先安装 Miniconda/Anaconda，并确保 conda 可在终端中使用。"
    exit 1
}

Write-Host "INFO Project Dir: $root"
Write-Host "INFO Using conda env: $EnvName"
$displayHost = if ($BindHost -eq "0.0.0.0") { "localhost" } else { $BindHost }
Write-Host "INFO Start backend: http://$displayHost`:$Port"
Write-Host "INFO Press Ctrl+C to stop"

if ($RestartExisting) {
    Write-Host "INFO Stop old backend/solver process before start"
    & "$root\stop_backend.ps1" -BackendPort $Port -SolverPort 8889 -FullStop 0
}

$pythonExe = (conda run --no-capture-output -n $EnvName python -c "import sys; print(sys.executable)").Trim()
if (-not (Test-Path $pythonExe)) {
    Write-Error "Failed to parse conda env python path."
    exit 1
}

$env:HOST = $BindHost
$env:PORT = [string]$Port

Write-Host "INFO Python: $pythonExe"
& $pythonExe main.py
