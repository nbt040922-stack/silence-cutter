$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$data = Join-Path $env:ProgramData 'ContentOps\SilenceCore'
$venv = Join-Path $repo '.venv_team\Scripts\python.exe'
$env:SILENCE_CORE_PACKAGED = '0'
$env:SILENCE_CORE_INSTALL_ROOT = $repo
$env:SILENCE_CORE_DATA_ROOT = $data
$profile = Join-Path $data 'state\hardware_profile.json'
if ($env:SEMANTIC_QWEN_MODEL) {
  $env:SILENCE_CORE_MODEL_DIR = $env:SEMANTIC_QWEN_MODEL
} elseif (Test-Path $profile) {
  try { $env:SILENCE_CORE_MODEL_DIR = (Get-Content $profile -Raw | ConvertFrom-Json).model_directory } catch { }
}
if (-not $env:SILENCE_CORE_MODEL_DIR) { $env:SILENCE_CORE_MODEL_DIR = Join-Path $data 'models\qwen2.5-vl-7b' }
$env:SILENCE_CUTTER_RESOURCE_DIR = $repo
$env:SILENCE_CUTTER_DATA_DIR = $data
$env:SILENCE_CUTTER_OUTPUT_DIR = Join-Path $data 'workspace\outputs'
$env:MODELSCOPE_CACHE = Join-Path $data 'models\modelscope'
$env:MODELSCOPE_HOME = $env:MODELSCOPE_CACHE
$env:PYTHONPATH = $repo
& $venv -m silence_core.entry_supervisor
