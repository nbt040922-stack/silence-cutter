$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$stage = (Resolve-Path (Join-Path $repo "internal_release_rc")).Path
$resources = (Resolve-Path (Join-Path $stage "Silence Cutter RC\resources")).Path
if (-not $resources.StartsWith($stage, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe cleanup target: $resources"
}
Get-ChildItem -LiteralPath $resources -Directory -Filter "__pycache__" -Recurse | Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $resources -File -Filter "*.pyc" -Recurse | Remove-Item -Force

$bundleLink = Join-Path $repo "r"
if (Test-Path -LiteralPath $bundleLink) {
    $item = Get-Item -LiteralPath $bundleLink -Force
    if (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Refusing to replace non-junction path: $bundleLink"
    }
    Remove-Item -LiteralPath $bundleLink -Force
}
New-Item -ItemType Junction -Path $bundleLink -Target $resources | Out-Null

$payload = Get-ChildItem -LiteralPath $resources -Recurse -File | Measure-Object Length -Sum
Write-Host "NSIS resources ready: $($payload.Count) files, $([math]::Round($payload.Sum / 1GB, 2)) GiB"
