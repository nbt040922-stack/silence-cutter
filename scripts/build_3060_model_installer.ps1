$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
foreach ($path in @(
    (Join-Path $repo "local_models\Qwen2.5-VL-7B-Instruct-AWQ"),
    (Join-Path $repo "desktop\src-tauri\target\release\resources\models\SenseVoiceSmall"),
    (Join-Path $repo "desktop\src-tauri\target\release\resources\models\fsmn-vad")
)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Model source missing: $path" }
}
$compiler = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe")
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $compiler) { throw "Inno Setup 6 compiler not found" }
& $compiler (Join-Path $repo "installer\SilenceCutter-3060-Models.iss")
if ($LASTEXITCODE -ne 0) { throw "3060 model installer failed with exit code $LASTEXITCODE" }
Write-Output ((Get-FileHash (Join-Path $repo "release\SilenceCutter-3060-Models-Setup.exe") -Algorithm SHA256).Hash)
