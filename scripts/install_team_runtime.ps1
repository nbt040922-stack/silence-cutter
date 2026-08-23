param([switch]$Update)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$data = Join-Path $env:ProgramData 'ContentOps\SilenceCore'
$runtime = Join-Path $data 'runtime'
$downloads = Join-Path $data 'downloads'
$venv = Join-Path $repo '.venv_team'
$python = Join-Path $venv 'Scripts\python.exe'
$logDir = Join-Path $data 'logs\installer'
New-Item -ItemType Directory -Force -Path $runtime,$downloads,$logDir,(Join-Path $data 'state') | Out-Null
$log = Join-Path $logDir 'team-runtime.log'
Start-Transcript -Path $log -Append | Out-Null
try {
  $uv = Join-Path $runtime 'uv.exe'
  if (-not (Test-Path $uv)) {
    $zip = Join-Path $downloads 'uv.zip'
    Invoke-WebRequest 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip' -OutFile $zip -UseBasicParsing
    Expand-Archive $zip (Join-Path $runtime 'uv-extract') -Force
    Copy-Item (Get-ChildItem (Join-Path $runtime 'uv-extract') -Filter uv.exe -Recurse | Select-Object -First 1).FullName $uv
  }
  if (-not (Test-Path $venv)) { & $uv venv --python 3.11 $venv }
  & $uv pip install --python $python --extra-index-url https://download.pytorch.org/whl/cu130 -r (Join-Path $repo 'requirements-production.txt')
  $vram = [int]((& nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | Select-Object -First 1).Trim())
  if ($vram -ge 10240) { $profile='qwen7b'; $manifest='core_model_manifest.json'; $model='qwen2.5-vl-7b' }
  elseif ($vram -ge 6144) { $profile='qwen3b'; $manifest='core_model_manifest_3b.json'; $model='qwen2.5-vl-3b' }
  else { throw "GPU VRAM không đủ: ${vram} MiB" }
  $profileData = @{gpu_vram_mib=$vram; qwen_profile=$profile; model_directory=(Join-Path $data "models\$model"); updated_at=(Get-Date).ToString('o')}
  $profileData | ConvertTo-Json | Set-Content (Join-Path $data 'state\hardware_profile.json') -Encoding UTF8
  $env:SILENCE_CORE_PACKAGED='0'; $env:SILENCE_CORE_INSTALL_ROOT=$repo; $env:SILENCE_CORE_DATA_ROOT=$data; $env:SILENCE_CORE_MODEL_DIR=(Join-Path $data "models\$model"); $env:SILENCE_CUTTER_RESOURCE_DIR=$repo; $env:SILENCE_CUTTER_DATA_DIR=$data; $env:SILENCE_CUTTER_OUTPUT_DIR=(Join-Path $data 'workspace\outputs'); $env:MODELSCOPE_CACHE=(Join-Path $data 'models\modelscope'); $env:MODELSCOPE_HOME=$env:MODELSCOPE_CACHE; $env:PYTHONPATH=$repo
  New-Item -ItemType Directory -Force -Path $env:MODELSCOPE_CACHE | Out-Null
  & $python -c "from modelscope import snapshot_download; snapshot_download('iic/SenseVoiceSmall', cache_dir=r'$($env:MODELSCOPE_CACHE)'); snapshot_download('iic/speech_fsmn_vad_zh-cn-16k-common-pytorch', cache_dir=r'$($env:MODELSCOPE_CACHE)')"
  & $python -m silence_core.setup install --manifest (Join-Path $repo "installer\$manifest")
  $start = Join-Path $repo 'scripts\start_team_runtime.ps1'
  schtasks.exe /Create /TN 'ContentOps\SilenceCore-Team' /SC ONLOGON /RL HIGHEST /F /TR ("powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$start`"") | Out-Null
  Start-Process powershell.exe -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',$start -WindowStyle Hidden
  Write-Host "Cài môi trường thành công: $profile; log: $log"
} catch { Write-Error $_; Write-Host "Cài đặt thất bại. Log: $log"; throw }
finally { Stop-Transcript | Out-Null }
