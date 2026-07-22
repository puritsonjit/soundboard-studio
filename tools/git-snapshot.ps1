param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Message,
    [switch]$NoBackup
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$gitDir = Join-Path $repoRoot ".git"

if (-not (Test-Path $gitDir)) {
    throw "This folder is not initialized as a Git repository."
}

& git -C $repoRoot add -A
if ($LASTEXITCODE -ne 0) {
    throw "Git could not stage the project changes."
}

& git -C $repoRoot diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host "No source changes to save."
    exit 0
}
if ($LASTEXITCODE -ne 1) {
    throw "Git could not inspect the staged changes."
}

& git -C $repoRoot commit -m $Message
if ($LASTEXITCODE -ne 0) {
    throw "Git could not create the snapshot."
}

if (-not $NoBackup) {
    & git -C $repoRoot remote get-url local-backup *> $null
    if ($LASTEXITCODE -eq 0) {
        & git -C $repoRoot push local-backup main
        if ($LASTEXITCODE -ne 0) {
            throw "The commit was saved, but the local backup mirror could not be updated."
        }
    }
}

Write-Host ""
Write-Host "Snapshot saved:"
& git -C $repoRoot log -1 --oneline
