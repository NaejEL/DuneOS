# Runs a headless AI-lab cycle on an already pre-registered protocol.
# Usage: pwsh -File ci/lab.ps1 <protocol-path> [-Yolo]
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Protocol,

    [switch]$Yolo
)

$ErrorActionPreference = 'Stop'

$repoDir = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path -LiteralPath $Protocol -PathType Leaf)) {
    Write-Error "Protocol not found: $Protocol"
    exit 1
}

if (-not (Select-String -LiteralPath $Protocol -Pattern '^\s*(Status\s*:\s*PRE-REGISTERED|Statut\s*:\s*PRE-ENREGISTRE)' -Quiet)) {
    Write-Error "Protocol not pre-registered: '$Protocol' does not contain 'Status: PRE-REGISTERED' (or the French 'Statut : PRE-ENREGISTRE'). A CI cycle requires a protocol approved by the PI."
    exit 1
}

$logDir = Join-Path $repoDir 'lab-logs'
if (-not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}
$logFile = Join-Path $logDir ("lab-{0}.json" -f (Get-Date -Format yyyyMMdd-HHmmss))

$claudeArgs = @(
    '-p', "/lab-run '$Protocol'",
    '--output-format', 'json',
    '--max-turns', '150'
)

if ($Yolo) {
    $claudeArgs += '--dangerously-skip-permissions'
    $mode = 'YOLO (permissions ignored)'
}
else {
    $claudeArgs += @('--permission-mode', 'acceptEdits')
    $claudeArgs += @('--allowedTools', '@ALLOWED@')
    $mode = 'acceptEdits + allowlist'
}

Write-Host "Protocol: $Protocol"
Write-Host "Mode    : $mode"
Write-Host "Log     : $logFile"

$env:CLAUDE_CODE_DISABLE_BACKGROUND_TASKS = '1'
$env:CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS = '0'
& claude @claudeArgs | Tee-Object -FilePath $logFile
$status = $LASTEXITCODE

Write-Host "claude exit: exit code $status"
exit $status
