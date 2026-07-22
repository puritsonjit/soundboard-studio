$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Split-Path -Parent $PSScriptRoot)).Path
$targets = @(
    (Join-Path $repoRoot ".ruff_cache"),
    (Join-Path $repoRoot "build_pyqt6")
)
$targets += Get-ChildItem -LiteralPath $repoRoot -File -Filter "*.spec"

$cacheRoots = @(
    (Join-Path $repoRoot "src\python_qt6"),
    (Join-Path $repoRoot "tests"),
    (Join-Path $repoRoot "tools")
)

foreach ($cacheRoot in $cacheRoots) {
    if (Test-Path $cacheRoot) {
        $targets += Get-ChildItem -LiteralPath $cacheRoot -Recurse -Directory -Filter "__pycache__"
    }
}

foreach ($target in $targets) {
    $path = if ($target -is [System.IO.FileSystemInfo]) { $target.FullName } else { [string]$target }
    $fullPath = [System.IO.Path]::GetFullPath($path)
    if (-not $fullPath.StartsWith($repoRoot + [System.IO.Path]::DirectorySeparatorChar)) {
        throw "Refusing to remove a path outside the project: $fullPath"
    }
    if (Test-Path -LiteralPath $fullPath) {
        Remove-Item -LiteralPath $fullPath -Recurse -Force
        Write-Host "Removed $fullPath"
    }
}

Write-Host "Project build caches are clean."
