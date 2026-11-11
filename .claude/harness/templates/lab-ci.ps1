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

# A relative path is read from the current folder when it exists there, else from the repository root;
# it is made absolute because claude runs from the root.
if (-not [System.IO.Path]::IsPathRooted($Protocol)) {
    if (Test-Path -LiteralPath $Protocol -PathType Leaf) { $Protocol = (Resolve-Path -LiteralPath $Protocol).Path }
    else { $Protocol = Join-Path $repoDir $Protocol }
}

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
