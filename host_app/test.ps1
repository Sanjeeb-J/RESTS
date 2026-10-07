<#
.SYNOPSIS
  Run the automated tests for the RESTS host application.
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$venvPy = Join-Path $here ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPy)) {
    throw "Virtual environment not found. Run first: powershell -ExecutionPolicy Bypass -File `"$here\setup.ps1`""
}

Push-Location $here
try {
    & $venvPy -m pytest tests -q @PytestArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
