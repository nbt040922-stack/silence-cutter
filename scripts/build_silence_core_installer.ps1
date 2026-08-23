$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$preferredPy = Join-Path $repo ".venv_asr_test\Scripts\python.exe"
$py = if (Test-Path -LiteralPath $preferredPy) { $preferredPy } else { (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $py) { throw "Python build runtime not found" }
$stage = Join-Path $repo "build\silence_core_payload"
$dist = Join-Path $repo "build\silence_core_dist"
Remove-Item -LiteralPath $stage,$dist -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $stage,$dist | Out-Null

foreach($entry in @(
    @{Name="silence_core_setup"; Script="silence_core\entry_setup.py"},
    @{Name="qwen"; Script="silence_core\entry_qwen.py"},
    @{Name="scheduler"; Script="silence_core\entry_scheduler.py"},
    @{Name="lan"; Script="silence_core\entry_lan.py"},
    @{Name="supervisor"; Script="silence_core\entry_supervisor.py"}
)) {
    & $py -m PyInstaller --noconfirm --clean --onedir --name $entry.Name `
        --collect-all PIL `
        --distpath $dist --workpath (Join-Path $repo "build\pyinstaller_$($entry.Name)") `
        --specpath (Join-Path $repo "build") $entry.Script
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed: $($entry.Name)" }
}

New-Item -ItemType Directory -Force -Path (Join-Path $stage "tools"),(Join-Path $stage "silence_core") | Out-Null
Copy-Item -LiteralPath (Join-Path $dist "qwen") -Destination (Join-Path $stage "qwen") -Recurse
New-Item -ItemType Directory -Force -Path (Join-Path $stage "silence_core_setup") | Out-Null
Copy-Item -Path (Join-Path $dist "silence_core_setup\*") -Destination (Join-Path $stage "silence_core_setup") -Recurse
Copy-Item -LiteralPath (Join-Path $dist "scheduler") -Destination (Join-Path $stage "scheduler") -Recurse
Copy-Item -LiteralPath (Join-Path $dist "lan") -Destination (Join-Path $stage "lan") -Recurse
Copy-Item -LiteralPath (Join-Path $dist "supervisor") -Destination (Join-Path $stage "supervisor") -Recurse
Copy-Item -LiteralPath (Join-Path $repo "installer\core_model_manifest.json") -Destination (Join-Path $stage "core_model_manifest.json")
$ffmpeg = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
$ffprobe = (Get-Command ffprobe -ErrorAction SilentlyContinue).Source
if (-not $ffmpeg -or -not $ffprobe) { throw "ffmpeg/ffprobe binaries are required to build the Core payload" }
Copy-Item -LiteralPath $ffmpeg -Destination (Join-Path $stage "tools\ffmpeg.exe")
Copy-Item -LiteralPath $ffprobe -Destination (Join-Path $stage "tools\ffprobe.exe")
$payloadStatus = if (Test-Path (Join-Path $stage "desktop")) { "FAIL" } else { "PASS" }
@{status=$payloadStatus} | ConvertTo-Json | Set-Content (Join-Path $stage "payload-validation.json")
& (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe") (Join-Path $repo "installer\SilenceCore.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
Get-FileHash (Join-Path $repo "release\Silence_Core_Setup.exe") -Algorithm SHA256
