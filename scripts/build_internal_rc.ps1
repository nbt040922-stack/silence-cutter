param(
    [string]$PythonEnvironment = ".venv_asr_test",
    [string]$SenseVoiceModelSource = $env:SILENCE_CUTTER_SENSEVOICE_MODEL_SOURCE,
    [string]$FsmnVadModelSource = $env:SILENCE_CUTTER_FSMN_VAD_MODEL_SOURCE,
    [string]$QwenModelSource = $env:SILENCE_CUTTER_QWEN_MODEL_SOURCE,
    [string]$DenoSource = $env:SILENCE_CUTTER_DENO_SOURCE,
    [switch]$SkipDesktopBuild
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$sourceEnvironment = (Resolve-Path (Join-Path $repo $PythonEnvironment)).Path
$stage = Join-Path $repo "internal_release_rc"
$payload = Join-Path $stage "Silence Cutter RC"
$bundleLink = Join-Path $repo "r"
$bootstrapResources = $null

if (([IO.Path]::GetFullPath($stage)).TrimEnd('\') -ne (Join-Path $repo "internal_release_rc")) {
    throw "Refusing to clean unexpected staging path: $stage"
}

if (-not $SkipDesktopBuild) {
    if (-not (Test-Path -LiteralPath $bundleLink)) {
        $existingLink = Get-Item -LiteralPath $bundleLink -Force -ErrorAction SilentlyContinue
        if ($existingLink) {
            if (-not ($existingLink.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw "Refusing to replace non-junction path: $bundleLink"
            }
            [IO.Directory]::Delete($bundleLink)
        }
        $bootstrapResources = Join-Path ([IO.Path]::GetTempPath()) "silence-cutter-tauri-resources"
        New-Item -ItemType Directory -Force -Path $bootstrapResources | Out-Null
        New-Item -ItemType Junction -Path $bundleLink -Target $bootstrapResources | Out-Null
    }
    Push-Location (Join-Path $repo "desktop")
    try {
        npm run tauri build -- --no-bundle
        if ($LASTEXITCODE -ne 0) { throw "Tauri build failed with exit code $LASTEXITCODE" }
    } finally { Pop-Location }
}

if (Test-Path -LiteralPath $stage) {
    Remove-Item -LiteralPath $stage -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $payload | Out-Null

$resources = Join-Path $payload "resources"
$app = Join-Path $resources "app"
$runtime = Join-Path $resources "runtime\python"
$bin = Join-Path $resources "bin"
$models = Join-Path $resources "models"
$benchmark = Join-Path $resources "benchmark"
New-Item -ItemType Directory -Force -Path $app,$runtime,$bin,$models,$benchmark | Out-Null

foreach ($folder in "backend","formatter","production","semantic_cleaner","silence_cutter","speech_detector") {
    Copy-Item -LiteralPath (Join-Path $repo $folder) -Destination $app -Recurse
}
Copy-Item -LiteralPath (Join-Path $repo "requirements-production.txt") -Destination $app

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
    "python311.zip",
    ".",
    "Lib\site-packages",
    "..\..\app",
    "import site"
)

if (-not $SenseVoiceModelSource -or -not $FsmnVadModelSource -or -not $QwenModelSource) {
    throw "Provide SenseVoice, FSMN-VAD, and Qwen model sources (or their SILENCE_CUTTER_* environment variables)"
}
$senseVoice = (Resolve-Path $SenseVoiceModelSource).Path
$fsmnVad = (Resolve-Path $FsmnVadModelSource).Path
$qwen = (Resolve-Path $QwenModelSource).Path
Copy-Item -LiteralPath $senseVoice -Destination (Join-Path $models "SenseVoiceSmall") -Recurse
Copy-Item -LiteralPath $fsmnVad -Destination (Join-Path $models "fsmn-vad") -Recurse
Copy-Item -LiteralPath $qwen -Destination (Join-Path $models "Qwen2.5-VL-7B-Instruct-AWQ") -Recurse

$ffmpeg = (Get-Command ffmpeg -ErrorAction Stop).Source
$ffprobe = (Get-Command ffprobe -ErrorAction Stop).Source
$deno = if ($DenoSource) {
    (Resolve-Path $DenoSource).Path
} else {
    (Get-Command deno -ErrorAction Stop).Source
}
Copy-Item -LiteralPath $ffmpeg,$ffprobe,$deno -Destination $bin
Copy-Item -LiteralPath (Join-Path $repo "release_assets\hardware_benchmark.mp4") -Destination $benchmark
Copy-Item -LiteralPath (Join-Path $repo "desktop\src-tauri\target\release\silence-cutter-desktop.exe") -Destination (Join-Path $payload "Silence Cutter.exe")
Copy-Item -LiteralPath (Join-Path $repo "scripts\Install-SilenceCutter-RC.ps1") -Destination $stage
Copy-Item -LiteralPath (Join-Path $repo "TEAM_HARDWARE_VALIDATION_REPORT.md") -Destination $stage

& (Join-Path $repo "scripts\finalize_internal_rc.ps1")
if (Test-Path -LiteralPath $bundleLink) { [IO.Directory]::Delete($bundleLink) }
New-Item -ItemType Junction -Path $bundleLink -Target $resources | Out-Null
if ($bootstrapResources) { Remove-Item -LiteralPath $bootstrapResources -Force }
Write-Host "RC staged at $stage"
