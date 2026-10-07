<#
.SYNOPSIS
  Start Renode with the RESTS STM32F429 setup (sysbus.usart1 on TCP 12345).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File host_app\renode\launch_renode.ps1

.EXAMPLE
  # custom ELF location + headless (no GUI):
  powershell -ExecutionPolicy Bypass -File host_app\renode\launch_renode.ps1 `
      -ElfPath "D:\build\RESTS_STM32.elf" -Headless
#>
[CmdletBinding()]
param(
    [string]$RenodePath = "",
    [string]$ElfPath = "",
    [int]$Port = 12345,
    [switch]$Headless
)

$ErrorActionPreference = "Stop"

$here = $PSScriptRoot
$repoRoot = Split-Path -Parent (Split-Path -Parent $here)   # host_app -> repo root
$rescPath = Join-Path $here "stm32f429_rests.resc"

function Resolve-Renode {
    param([string]$Override)
    if ($Override) {
        if (-not (Test-Path $Override)) { throw "renode.exe not found at $Override" }
        return (Resolve-Path $Override).Path
    }
    if ($env:RENODE_EXE -and (Test-Path $env:RENODE_EXE)) { return $env:RENODE_EXE }
    $candidate = "C:\Program Files\Renode\renode.exe"
    if (Test-Path $candidate) { return $candidate }
    $cmd = Get-Command renode -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw "renode.exe not found. Install Renode or pass -RenodePath / set RENODE_EXE."
}

$renode = Resolve-Renode -Override $RenodePath

if (-not $ElfPath) { $ElfPath = Join-Path $repoRoot "Debug\RESTS_STM32.elf" }
if (-not (Test-Path $ElfPath)) {
    Write-Error ("ELF not found:`n" + $ElfPath + "`nBuild it first (CubeIDE 'Debug' configuration, or the headless build described in README.md).")
    exit 1
}

$tempResc = $null
# Always generate a tiny wrapper that pins the ELF (and port) before the main
# script runs. Renode only resolves '@' paths (relative to its CWD, or
# absolute) in LoadELF - a bare relative string is rejected - and Renode
# executes -e commands *after* the script, so the values must be set here.
$tempResc = Join-Path $env:TEMP ("rests_renode_" + [guid]::NewGuid().ToString("N") + ".resc")
$elfEscaped = $ElfPath -replace '\\', '/'
@"
`$elf?=@$elfEscaped
`$port?=$Port
include "$($rescPath -replace '\\','/')"
"@ | Set-Content -Path $tempResc -Encoding UTF8
$scriptResc = $tempResc

$renodeArgs = @()
if ($Headless) { $renodeArgs += "--disable-xwt" }
$renodeArgs += $scriptResc

Write-Host "Renode    : $renode"
Write-Host "Script    : $scriptResc"
Write-Host "ELF       : $ElfPath"
Write-Host "UART TCP  : 127.0.0.1:$Port"
Write-Host "Press Ctrl+C in this window (or close it) to stop the emulation."
Write-Host ""

# The wrapper pins the ELF to an absolute path, but Renode's own CWD still
# matters for '@platforms/...' resolution, so run from the repository root.
Push-Location $repoRoot
try {
    & $renode @renodeArgs
    $code = $LASTEXITCODE
}
finally {
    Pop-Location
    if ($tempResc -and (Test-Path $tempResc)) { Remove-Item $tempResc -Force -ErrorAction SilentlyContinue }
}
exit $code
