param(
    [string]$PythonCommand = "py"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$envRoot = Join-Path $repoRoot "env\pyqt6"
$requirements = Join-Path $repoRoot "requirements-dev.txt"

if (Test-Path $envRoot) {
    Write-Host "Python environment already exists:"
    Write-Host "  $envRoot"
    exit 0
}

if (Get-Command $PythonCommand -ErrorAction SilentlyContinue) {
    if ($PythonCommand -eq "py") {
        & $PythonCommand -3.12 -m venv $envRoot
    } else {
        & $PythonCommand -m venv $envRoot
    }
} else {
    throw "Python launcher '$PythonCommand' was not found. Install Python 3.12 or pass -PythonCommand with its executable name."
}

if ($LASTEXITCODE -ne 0) {
    throw "Failed to create the local Python environment."
}

$python = Join-Path $envRoot "Scripts\python.exe"
& $python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "Failed to update pip."
}

& $python -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install the project dependencies."
}

Write-Host ""
Write-Host "Local Python environment is ready:"
Write-Host "  $envRoot"
