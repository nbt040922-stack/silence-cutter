$ErrorActionPreference = "Stop"
$b = Join-Path $PSScriptRoot "..\build\silence_core_bootstrap"
Remove-Item $b -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $b | Out-Null
Copy-Item (Join-Path $PSScriptRoot "..\installer\bootstrap_install.ps1") $b
$items = @("silence_core","silence_cutter","caption_engine","timeline_engine","speech_detector","production","formatter","semantic_cleaner","qwen_worker","contentops_process_bridge.py","lan_job_api.py","requirements-production.txt")
Compress-Archive -Path $items -DestinationPath (Join-Path $b "source.zip") -CompressionLevel Fastest
$iscc = Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"
& $iscc (Join-Path $PSScriptRoot "..\installer\SilenceCoreBootstrap.iss")
if ($LASTEXITCODE) { exit $LASTEXITCODE }
