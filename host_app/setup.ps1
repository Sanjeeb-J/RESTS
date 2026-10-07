<#
.SYNOPSIS
  Create an isolated Python 3.11 virtual environment for the RESTS host
  application and install its dependencies.

.DESCRIPTION
  Default (full) install:
      powershell -ExecutionPolicy Bypass -File host_app\setup.ps1

  Tests only (no torch / transformers / faster-whisper):
      powershell -ExecutionPolicy Bypass -File host_app\setup.ps1 -DevOnly

  The multi-gigabyte models are NOT downloaded here; run
  host_app\download_models.py afterwards (see README.md).
#>
[CmdletBinding()]
param(
    [switch]$DevOnly,      # install only the test dependencies
    [switch]$Force,        # delete and recreate an existing .venv
    [string]$Python = ""   # explicit path to a Python 3.11 interpreter
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$venvDir = Join-Path $here ".venv"
$venvPy = Join-Path $venvDir "Scripts\python.exe"

function Resolve-BasePython {
    param([string]$Override)
    if ($Override) {
        if (-not (Test-Path $Override)) { throw "Python interpreter not found at $Override" }
        return (Resolve-Path $Override).Path
    }
    $pyCmd = Get-Command py -ErrorAction SilentlyContinue
    if ($pyCmd) {
        $exe = & py -3.11 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe) {
            return (@($exe)[0]).ToString().Trim()
        }
        throw "Python 3.11 was not found through the 'py' launcher. Install Python 3.11 (https://www.python.org/downloads/) and re-run."
    }
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) { return $pythonCmd.Source }
    throw "No Python interpreter found on PATH. Install Python 3.11 and re-run."
}

Write-Host "== RESTS host_app setup ==" -ForegroundColor Cyan

$basePython = Resolve-BasePython -Override $Python
$versionLine = & $basePython -c "import sys; print('%d.%d.%d' % sys.version_info[:3])"
if ($LASTEXITCODE -ne 0) { throw "Could not run $basePython" }
if ($versionLine -notlike "3.11*") {
    throw "Python 3.11 is required, found $versionLine at $basePython. Pass -Python <path> to a 3.11 interpreter."
}
Write-Host "Base interpreter : $basePython ($versionLine)"

if ($Force -and (Test-Path $venvDir)) {
    Write-Host "Removing existing virtual environment..."
    Remove-Item -Recurse -Force $venvDir
}

if (-not (Test-Path $venvPy)) {
    Write-Host "Creating virtual environment at $venvDir ..."
    & $basePython -m venv $venvDir
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
} else {
    Write-Host "Reusing virtual environment at $venvDir"
}

Write-Host "Upgrading pip..."
& $venvPy -m pip install --disable-pip-version-check --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }

if (-not $DevOnly) {
    Write-Host "Installing runtime dependencies (requirements.txt)..."
    & $venvPy -m pip install --disable-pip-version-check -r (Join-Path $here "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "runtime dependency installation failed" }
} else {
    Write-Host "DevOnly: skipping runtime dependencies (torch/transformers/faster-whisper)." -ForegroundColor Yellow
}

Write-Host "Installing test dependencies (requirements-dev.txt)..."
& $venvPy -m pip install --disable-pip-version-check -r (Join-Path $here "requirements-dev.txt")
if ($LASTEXITCODE -ne 0) { throw "test dependency installation failed" }

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "  Interpreter : $venvPy"
Write-Host "  Next steps  :"
if (-not $DevOnly) {
    Write-Host "    1. Download the models once (multi-GB, needs internet):"
    Write-Host "         & `"$venvPy`" `"$here\download_models.py`""
    Write-Host "    2. Start Renode:"
    Write-Host "         powershell -ExecutionPolicy Bypass -File `"$here\renode\launch_renode.ps1`""
    Write-Host "    3. Start the application:"
    Write-Host "         powershell -ExecutionPolicy Bypass -File `"$here\run.ps1`""
} else {
    Write-Host "    Run the automated tests:"
    Write-Host "         powershell -ExecutionPolicy Bypass -File `"$here\test.ps1`""
}
