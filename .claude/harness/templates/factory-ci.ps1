# Runs a headless software-factory cycle on an already approved spec.
# Usage: pwsh -File ci/factory.ps1 <spec-path> [-Yolo]
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Spec,

    [switch]$Yolo
)

$ErrorActionPreference = 'Stop'

$repoDir = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path -LiteralPath $Spec -PathType Leaf)) {
    Write-Error "Spec not found: $Spec"
    exit 1
}

if (-not (Select-String -LiteralPath $Spec -Pattern '^\s*(Status\s*:\s*APPROVED|Statut\s*:\s*APPROUVEE)' -Quiet)) {
    Write-Error "Spec not approved: '$Spec' does not contain 'Status: APPROVED' (or the French 'Statut : APPROUVEE'). A CI cycle requires a spec validated by the Product Owner."
    exit 1
}

$logDir = Join-Path $repoDir 'factory-logs'
if (-not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}
$logFile = Join-Path $logDir ("factory-{0}.json" -f (Get-Date -Format yyyyMMdd-HHmmss))

$claudeArgs = @(
    '-p', "/factory-run '$Spec'",
    '--output-format', 'json',
    '--max-turns', '100'
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

Write-Host "Spec    : $Spec"
Write-Host "Mode    : $mode"
Write-Host "Log     : $logFile"

$env:CLAUDE_CODE_DISABLE_BACKGROUND_TASKS = '1'
$env:CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS = '0'
& claude @claudeArgs | Tee-Object -FilePath $logFile
$status = $LASTEXITCODE

Write-Host "claude exit: exit code $status"
exit $status
