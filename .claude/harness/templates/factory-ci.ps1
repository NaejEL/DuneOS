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

# A relative path is read from the current folder when it exists there, else from the repository root;
# it is made absolute because claude runs from the root.
if (-not [System.IO.Path]::IsPathRooted($Spec)) {
    if (Test-Path -LiteralPath $Spec -PathType Leaf) { $Spec = (Resolve-Path -LiteralPath $Spec).Path }
    else { $Spec = Join-Path $repoDir $Spec }
}

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
$null = Get-Command claude -ErrorAction Stop
$status = $null
Push-Location $repoDir
# 'Continue': under 'Stop', Windows PowerShell 5.1 turns each redirected stderr line into a terminating error.
$ErrorActionPreference = 'Continue'
try {
    & claude @claudeArgs 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $logFile
    $status = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = 'Stop'
    Pop-Location
}
if ($null -eq $status) { $status = 1 }

Write-Host "claude exit: exit code $status"
exit $status
