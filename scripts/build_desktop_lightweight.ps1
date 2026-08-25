param(
    [switch]$SkipDesktopBuild
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$exe = Join-Path $repo "desktop\src-tauri\target\release\silence-cutter-desktop.exe"
$stage = Join-Path $repo "release\SilenceCutter-Desktop-Lightweight"
$zip = Join-Path $repo "release\SilenceCutter-Desktop-Lightweight.zip"

if (-not $SkipDesktopBuild) {
    Push-Location (Join-Path $repo "desktop")
    try {
        npm run tauri build -- --no-bundle
        if ($LASTEXITCODE -ne 0) { throw "Tauri build failed with exit code $LASTEXITCODE" }
    } finally { Pop-Location }
}

if (-not (Test-Path -LiteralPath $exe)) { throw "Desktop executable not found: $exe" }
if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $stage | Out-Null
Copy-Item -LiteralPath $exe -Destination (Join-Path $stage "Silence Cutter.exe")

$launcher = @'
$ErrorActionPreference = "Stop"
$root = $null
$cursor = $PSScriptRoot
while ($cursor) {
    if (Test-Path (Join-Path $cursor "production")) { $root = $cursor; break }
    $parent = Split-Path $cursor -Parent
    if ($parent -eq $cursor) { break }
    $cursor = $parent
}
if (-not $root) { throw "Khong tim thay thu muc repo Silence Cutter (thu muc production). Hay giai nen goi nay trong repo hoac dat SILENCE_CUTTER_ROOT." }
$env:SILENCE_CUTTER_ROOT = $root
$pythonCandidates = @(
    (Join-Path $root ".venv\Scripts\python.exe"),
    (Join-Path $root ".venv_asr_test\Scripts\python.exe"),
    (Join-Path $root ".venv_team\Scripts\python.exe")
)
$python = $pythonCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($python) { $env:SILENCE_CUTTER_PYTHON = $python }
Start-Process -FilePath (Join-Path $PSScriptRoot "Silence Cutter.exe") -WorkingDirectory $root
'@
Set-Content -LiteralPath (Join-Path $stage "Run-SilenceCutter.ps1") -Value $launcher -Encoding UTF8
$cmd = "@echo off`r`npowershell.exe -NoProfile -ExecutionPolicy Bypass -File `"%~dp0Run-SilenceCutter.ps1`"`r`n"
Set-Content -LiteralPath (Join-Path $stage "Run-SilenceCutter.cmd") -Value $cmd -Encoding ASCII
Set-Content -LiteralPath (Join-Path $stage "README.txt") -Value @"
SILENCE CUTTER - DESKTOP LIGHTWEIGHT

Goi nay chi chua giao dien desktop, khong kem Python, torch, model hoac FFmpeg.
May chay can co repo Silence Cutter va moi truong da cai san.

Giai nen thu muc nay vao trong repo, hoac dat bien moi truong SILENCE_CUTTER_ROOT
tro den thu muc co thu muc production. Nhan Run-SilenceCutter.cmd de mo.
"@ -Encoding UTF8

if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $zip -CompressionLevel Optimal
$hash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash
$size = (Get-Item -LiteralPath $zip).Length
Write-Host "Desktop lightweight package: $zip"
Write-Host "Size: $size bytes"
Write-Host "SHA256: $hash"
