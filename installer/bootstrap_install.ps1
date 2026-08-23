$ErrorActionPreference = 'Stop'
$app = Split-Path -Parent $MyInvocation.MyCommand.Path
$data = Join-Path $env:ProgramData 'ContentOps\SilenceCore'
$runtime = Join-Path $data 'runtime'
$venv = Join-Path $runtime 'venv'
$downloads = Join-Path $data 'downloads'
$source = Join-Path $runtime 'source'
$tools = Join-Path $runtime 'tools'
New-Item -ItemType Directory -Force -Path $runtime,$downloads,$source | Out-Null
$logDir = Join-Path $data 'logs\installer'
New-Item -ItemType Directory -Force -Path $logDir,(Join-Path $data 'state') | Out-Null
$logPath = Join-Path $logDir 'bootstrap.log'
$fallbackLog = Join-Path $env:TEMP 'SilenceCoreBootstrap.log'
try { Add-Content -LiteralPath $logPath -Value "`n[$(Get-Date -Format o)] bootstrap started" -Encoding UTF8 } catch { }
try { Add-Content -LiteralPath $fallbackLog -Value "`n[$(Get-Date -Format o)] bootstrap started; primary=$logPath" -Encoding UTF8 } catch { }
try { Start-Transcript -Path $logPath -Append | Out-Null } catch { }
trap {
  $detail = "[$(Get-Date -Format o)] UNHANDLED ERROR: $($_.Exception.GetType().Name): $($_.Exception.Message)"
  try { Add-Content -LiteralPath $logPath -Value $detail -Encoding UTF8 } catch { }
  try { Add-Content -LiteralPath $fallbackLog -Value $detail -Encoding UTF8 } catch { }
  try {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show("Cài đặt thất bại.`n$detail`nLog: $fallbackLog", 'Silence Core', 'OK', 'Error') | Out-Null
  } catch { Write-Host $detail }
  try { Stop-Transcript | Out-Null } catch { }
  exit 1
}
function Finish([bool]$ok, [string]$message) {
  $result = @{status = if ($ok) { 'PASS' } else { 'FAIL' }; message = $message; log = $logPath; timestamp = (Get-Date).ToString('o')}
  $result | ConvertTo-Json | Set-Content (Join-Path $data 'state\install-result.json') -Encoding UTF8
  try { Add-Content -LiteralPath $fallbackLog -Value "[$(Get-Date -Format o)] $message" -Encoding UTF8 } catch { }
  try {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show($message + "`nLog: $fallbackLog", 'Silence Core', 'OK', $(if ($ok) { 'Information' } else { 'Error' })) | Out-Null
  } catch { Write-Host $message }
  Stop-Transcript | Out-Null
  if (-not $ok) { exit 1 }
}

function Download([string]$url, [string]$path) {
  if (-not (Test-Path $path)) { Invoke-WebRequest -Uri $url -OutFile $path -UseBasicParsing }
}

$uvZip = Join-Path $downloads 'uv.zip'
Download 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip' $uvZip
$uvDir = Join-Path $runtime 'uv'
if (-not (Test-Path (Join-Path $uvDir 'uv.exe'))) {
  New-Item -ItemType Directory -Force -Path $uvDir | Out-Null
  tar.exe -xf $uvZip -C $uvDir
  $nested = Get-ChildItem $uvDir -Filter uv.exe -Recurse | Select-Object -First 1
  Copy-Item $nested.FullName (Join-Path $uvDir 'uv.exe') -Force
}
$uv = Join-Path $uvDir 'uv.exe'
if (-not (Test-Path $venv)) { & $uv venv --python 3.11 $venv }
$python = Join-Path $venv 'Scripts\python.exe'
& $uv pip install --python $python --extra-index-url https://download.pytorch.org/whl/cu130 -r (Join-Path $app 'requirements-production.txt')
Expand-Archive -Path (Join-Path $app 'source.zip') -DestinationPath $source -Force
$ffmpegZip = Join-Path $downloads 'ffmpeg.zip'
Download 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' $ffmpegZip
if (-not (Test-Path (Join-Path $tools 'ffmpeg.exe'))) {
  $ffmpegExtract = Join-Path $downloads 'ffmpeg-extract'
  Expand-Archive -Path $ffmpegZip -DestinationPath $ffmpegExtract -Force
  New-Item -ItemType Directory -Force -Path $tools | Out-Null
  $ffmpeg = Get-ChildItem $ffmpegExtract -Filter ffmpeg.exe -Recurse | Select-Object -First 1
  $ffprobe = Get-ChildItem $ffmpegExtract -Filter ffprobe.exe -Recurse | Select-Object -First 1
  if (-not $ffmpeg -or -not $ffprobe) { throw 'FFmpeg package missing ffmpeg.exe/ffprobe.exe' }
  Copy-Item $ffmpeg.FullName (Join-Path $tools 'ffmpeg.exe') -Force
  Copy-Item $ffprobe.FullName (Join-Path $tools 'ffprobe.exe') -Force
}
Copy-Item (Join-Path $app 'core_model_manifest.json') (Join-Path $runtime 'core_model_manifest.json') -Force
$vramMiB = 0
try { $vramMiB = [int]((& nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | Select-Object -First 1).Trim()) } catch { }
if ($vramMiB -ge 10240) {
  $modelManifest = Join-Path $runtime 'core_model_manifest.json'
  $modelDir = Join-Path $data 'models\qwen2.5-vl-7b'
} elseif ($vramMiB -ge 6144) {
  Copy-Item (Join-Path $app 'core_model_manifest_3b.json') (Join-Path $runtime 'core_model_manifest_3b.json') -Force
  $modelManifest = Join-Path $runtime 'core_model_manifest_3b.json'
  $modelDir = Join-Path $data 'models\qwen2.5-vl-3b'
} else {
  throw "Không tìm thấy GPU NVIDIA đủ VRAM cho Qwen (VRAM=${vramMiB}MiB)"
}
$env:SILENCE_CORE_MODEL_DIR = $modelDir
$env:SILENCE_CORE_PACKAGED = '0'
$env:SILENCE_CORE_INSTALL_ROOT = $runtime
$env:SILENCE_CORE_DATA_ROOT = $data
$env:SILENCE_CUTTER_RESOURCE_DIR = $source
$env:SILENCE_CUTTER_DATA_DIR = $data
$env:SILENCE_CUTTER_OUTPUT_DIR = Join-Path $data 'workspace\outputs'
$env:PATH = $tools + ';' + $env:PATH
$env:SILENCE_CORE_RESOURCE_DIR = $source
$env:PYTHONPATH = $source
$env:MODELSCOPE_CACHE = Join-Path $data 'models\modelscope'
$env:MODELSCOPE_HOME = Join-Path $data 'models\modelscope'
New-Item -ItemType Directory -Force -Path $env:MODELSCOPE_CACHE | Out-Null
Write-Host 'Đang tải SenseVoiceSmall...'
& $python -c "from modelscope import snapshot_download; snapshot_download('iic/SenseVoiceSmall', cache_dir=r'$($env:MODELSCOPE_CACHE)')"
if ($LASTEXITCODE) { Finish $false 'Tải SenseVoiceSmall thất bại. Xem log: ' + $logPath }
Write-Host 'Đang tải FSMN-VAD...'
& $python -c "from modelscope import snapshot_download; snapshot_download('iic/speech_fsmn_vad_zh-cn-16k-common-pytorch', cache_dir=r'$($env:MODELSCOPE_CACHE)')"
if ($LASTEXITCODE) { Finish $false 'Tải FSMN-VAD thất bại. Xem log: ' + $logPath }
& $python -m silence_core.setup install --manifest $modelManifest
if ($LASTEXITCODE) { Finish $false ('Cài Qwen thất bại. Xem log: ' + $logPath) }
$startScript = Join-Path $runtime 'start_core.ps1'
@"
`$env:SILENCE_CORE_PACKAGED = '0'
`$env:SILENCE_CORE_INSTALL_ROOT = '$runtime'
`$env:SILENCE_CORE_DATA_ROOT = '$data'
`$env:SILENCE_CORE_MODEL_DIR = '$modelDir'
`$env:MODELSCOPE_CACHE = '$(Join-Path $data 'models\modelscope')'
`$env:MODELSCOPE_HOME = '$(Join-Path $data 'models\modelscope')'
`$env:SILENCE_CUTTER_RESOURCE_DIR = '$source'
`$env:SILENCE_CUTTER_DATA_DIR = '$data'
`$env:SILENCE_CUTTER_OUTPUT_DIR = '$(Join-Path $data 'workspace\outputs')'
`$env:PATH = '$tools;' + `$env:PATH
`$env:PYTHONPATH = '$source'
& '$python' -m silence_core.entry_supervisor
"@ | Set-Content $startScript -Encoding UTF8
schtasks.exe /Create /TN 'ContentOps\SilenceCore' /SC ONSTART /DELAY 0001:00 /RU SYSTEM /RL HIGHEST /F /TR ("$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$startScript`"") | Out-Null
Start-Process -FilePath $python -ArgumentList '-m','silence_core.entry_supervisor' -WorkingDirectory $source -WindowStyle Hidden
$deadline = (Get-Date).AddMinutes(5)
do {
  Start-Sleep -Seconds 2
  try {
    $qwen = Invoke-RestMethod 'http://127.0.0.1:8792/health' -TimeoutSec 3
    $scheduler = Invoke-RestMethod 'http://127.0.0.1:8791/health' -TimeoutSec 3
    $lan = Invoke-RestMethod 'http://127.0.0.1:8780/health' -TimeoutSec 3
    if ($qwen.status -eq 'READY' -and $qwen.model_loaded -and $qwen.warmed_up -and $scheduler.status -eq 'READY' -and $lan.status -eq 'READY') {
      Finish $true 'Cài đặt thành công. Qwen warm, cổng 8792/8791/8780 đều READY.'
    }
  } catch { }
} while ((Get-Date) -lt $deadline)
Finish $false 'Cài đặt chưa hoàn tất: không đạt READY trong 5 phút. Xem log: ' + $logPath
