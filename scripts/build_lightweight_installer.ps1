param(
    [string]$PythonEnvironment = ".venv_asr_test",
    [switch]$SkipDesktopBuild
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$sourceEnvironment = (Resolve-Path (Join-Path $repo $PythonEnvironment)).Path
$stage = Join-Path $repo "lightweight_release"
$payload = Join-Path $stage "Silence Cutter"

if (-not $SkipDesktopBuild) {
    Push-Location (Join-Path $repo "desktop")
    try {
        npm run tauri build -- --no-bundle
        if ($LASTEXITCODE -ne 0) { throw "Tauri build failed with exit code $LASTEXITCODE" }
    } finally { Pop-Location }
}

if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
$resources = Join-Path $payload "resources"
$app = Join-Path $resources "app"
$runtime = Join-Path $resources "runtime\python"
$bin = Join-Path $resources "bin"
New-Item -ItemType Directory -Force -Path $app,$runtime,$bin | Out-Null

foreach ($folder in "backend","enhanced_content_flow","formatter","installer_setup","long_video_selector","production","qwen_worker","semantic_cleaner","silence_cutter","speech_detector") {
    Copy-Item -LiteralPath (Join-Path $repo $folder) -Destination $app -Recurse
}
Copy-Item -LiteralPath (Join-Path $repo "requirements-production.txt") -Destination $app
Copy-Item -LiteralPath (Join-Path $repo "lan_job_api.py") -Destination $app
Copy-Item -LiteralPath (Join-Path $repo "installer\model_manifest.json") -Destination (Join-Path $resources "model_manifest.json")

$pythonZip = Join-Path $env:TEMP "python-3.11.9-embed-amd64.zip"
if (-not (Test-Path -LiteralPath $pythonZip)) {
    Invoke-WebRequest "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip" -OutFile $pythonZip
}
Expand-Archive -LiteralPath $pythonZip -DestinationPath $runtime
New-Item -ItemType Directory -Force -Path (Join-Path $runtime "Lib\site-packages") | Out-Null
Copy-Item -Path (Join-Path $sourceEnvironment "Lib\site-packages\*") -Destination (Join-Path $runtime "Lib\site-packages") -Recurse -Force
Get-ChildItem -LiteralPath $runtime -Directory -Filter "__pycache__" -Recurse | Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $runtime -File -Filter "*.pyc" -Recurse | Remove-Item -Force
Set-Content -LiteralPath (Join-Path $runtime "python311._pth") -Encoding ASCII -Value @(
    "python311.zip", ".", "Lib\site-packages", "..\..\app", "import site"
)

$ffmpeg = (Get-Command ffmpeg -ErrorAction Stop).Source
$ffprobe = (Get-Command ffprobe -ErrorAction Stop).Source
$deno = (Get-Command deno -ErrorAction Stop).Source
Copy-Item -LiteralPath $ffmpeg,$ffprobe,$deno -Destination $bin
Copy-Item -LiteralPath (Join-Path $repo "desktop\src-tauri\target\release\silence-cutter-desktop.exe") -Destination (Join-Path $payload "Silence Cutter.exe")

& (Join-Path $repo "scripts\build_lightweight_installer_package.ps1")
Write-Host "Bản nhẹ đã stage tại $stage"
