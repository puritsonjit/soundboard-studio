param(
    [string]$Name = "SoundboardQt6",
    [string]$PythonExecutable = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$localPython = Join-Path $repoRoot "env\pyqt6\Scripts\python.exe"
if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
  if (Test-Path -LiteralPath $localPython) {
    $python = $localPython
  } else {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
      throw "Python was not found. Create env\pyqt6 or pass -PythonExecutable."
    }
    $python = $pythonCommand.Source
  }
} else {
  $python = (Resolve-Path -LiteralPath $PythonExecutable).Path
}
$entry = Join-Path $repoRoot "src\python_qt6\main.py"
$webSource = Join-Path $repoRoot "src\python_qt6\soundboard_qt6\web"
$assetsSource = Join-Path $repoRoot "src\python_qt6\soundboard_qt6\assets"
$iconSource = Join-Path $assetsSource "app_icon.ico"
$distPath = Join-Path $repoRoot "dist_pyqt6"
$buildPath = Join-Path $repoRoot "build_pyqt6"
$exePath = Join-Path $distPath ($Name + ".exe")
$versionInfo = Join-Path $buildPath "version_info.txt"

& $python (Join-Path $repoRoot "tools\create-brand-assets.py")
if ($LASTEXITCODE -ne 0) {
  throw "Brand asset generation failed with exit code $LASTEXITCODE."
}

& $python (Join-Path $repoRoot "tools\create-version-info.py") --output $versionInfo
if ($LASTEXITCODE -ne 0) {
  throw "Version metadata generation failed with exit code $LASTEXITCODE."
}

& $python -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --windowed `
  --name $Name `
  --icon $iconSource `
  --version-file $versionInfo `
  --distpath $distPath `
  --workpath $buildPath `
  --paths (Join-Path $repoRoot "src\python_qt6") `
  --add-data "$webSource;soundboard_qt6\web" `
  --add-data "$assetsSource;soundboard_qt6\assets" `
  $entry
if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller failed with exit code $LASTEXITCODE. Close any running $Name process and try again."
}

New-Item -ItemType Directory -Force -Path (Join-Path $distPath "data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $distPath "runtime\sounds") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $distPath "runtime\images") | Out-Null

$cableZipSource = Join-Path $repoRoot "env\driver_cache\VBCABLE_Driver_Pack45.zip"
$driverTargetDir = Join-Path $distPath "driver"
$cableZipTargetDir = Join-Path $distPath "driver\signed_cable_fallback"
if (Test-Path $driverTargetDir) {
  Remove-Item -LiteralPath $driverTargetDir -Recurse -Force
}
if (Test-Path $cableZipSource) {
  New-Item -ItemType Directory -Force -Path $cableZipTargetDir | Out-Null
  Copy-Item -Force $cableZipSource (Join-Path $cableZipTargetDir "VBCABLE_Driver_Pack45.zip")
  Write-Host "Staged VB-CABLE setup package:"
  Write-Host "  $(Join-Path $cableZipTargetDir 'VBCABLE_Driver_Pack45.zip')"
} else {
  Write-Host "VB-CABLE setup package cache not found; the app will download it on first run."
}

& $python (Join-Path $repoRoot "tools\collect-license-notices.py") `
  --output (Join-Path $distPath "licenses") `
  --project-license (Join-Path $repoRoot "LICENSE")
if ($LASTEXITCODE -ne 0) {
  throw "License notice collection failed with exit code $LASTEXITCODE."
}

Get-ChildItem -LiteralPath $distPath -File -Filter "CODE_*_POLICY.md" -ErrorAction SilentlyContinue |
  Remove-Item -Force

foreach ($document in @(
  "LICENSE",
  "README.md",
  "CHANGELOG.md",
  "PRIVACY.md",
  "SECURITY.md",
  "THIRD_PARTY_NOTICES.md"
)) {
  Copy-Item -LiteralPath (Join-Path $repoRoot $document) -Destination (Join-Path $distPath $document) -Force
}
Copy-Item -LiteralPath (Join-Path $repoRoot "docs\UNINSTALL.md") -Destination (Join-Path $distPath "UNINSTALL.md") -Force

Write-Host ""
Write-Host "Built EXE:"
Write-Host "  $exePath"
