<#
.SYNOPSIS
  Start the RESTS host application from its virtual environment.

  Extra arguments are forwarded, e.g.:
    run.ps1 --self-check
    run.ps1 --self-check --probe-uart
    run.ps1 --no-connect --backend passthrough
    run.ps1 --asr-device cpu
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$AppArgs
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$venvPy = Join-Path $here ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPy)) {
    throw "Virtual environment not found. Run first: powershell -ExecutionPolicy Bypass -File `"$here\setup.ps1`""
}

Push-Location $here
try {
    & $venvPy -m rests_host @AppArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
