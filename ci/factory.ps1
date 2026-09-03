# Headless CI cycle of the DuneOS software factory.
# Usage: pwsh -File ci/factory.ps1 <specs/SPEC-*.md> [-Yolo]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Spec,
    [switch]$Yolo
)
$ErrorActionPreference = 'Stop'

if (-not (Test-Path -Path $Spec -PathType Leaf)) {
    Write-Error "Spec not found: $Spec"
    exit 1
}
$approved = Select-String -Path $Spec -Pattern '^Status: APPROVED$' -Quiet
if (-not $approved) {
    Write-Error "'$Spec' is not approved (line 'Status: APPROVED' missing). The human gate happens in an interactive session: /factory-run `"<requirement>`""
    exit 1
}

New-Item -ItemType Directory -Force -Path 'factory-logs' | Out-Null
$ts = Get-Date -Format yyyyMMdd-HHmmss
$log = "factory-logs/factory-$ts.json"

$args = @('-p', "/factory-run '$Spec'", '--output-format', 'json', '--max-turns', '100')
if ($Yolo) {
    $args += '--dangerously-skip-permissions'
} else {
    $args += @(
        '--permission-mode', 'acceptEdits',
        '--allowedTools',
        'Read', 'Glob', 'Grep', 'Write', 'Edit',
        'Bash(git *)', 'Bash(python *)', 'Bash(idf.py *)',
        'PowerShell(git *)', 'PowerShell(python *)', 'PowerShell(idf.py *)'
    )
}

& claude @args *> $log
$rc = $LASTEXITCODE

Write-Host "Log: $log (claude exit code: $rc)"
exit $rc
