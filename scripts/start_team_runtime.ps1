$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$data = Join-Path $env:ProgramData 'ContentOps\SilenceCore'
$venv = Join-Path $repo '.venv_team\Scripts\python.exe'
$env:SILENCE_CORE_PACKAGED = '0'
$env:SILENCE_CORE_INSTALL_ROOT = $repo
$env:SILENCE_CORE_DATA_ROOT = $data
$env:SILENCE_CUTTER_RESOURCE_DIR = $repo
$env:SILENCE_CUTTER_DATA_DIR = $data
$env:SILENCE_CUTTER_OUTPUT_DIR = Join-Path $data 'workspace\outputs'
$env:MODELSCOPE_CACHE = Join-Path $data 'models\modelscope'
$env:MODELSCOPE_HOME = $env:MODELSCOPE_CACHE
$env:PYTHONPATH = $repo
& $venv -m silence_core.entry_supervisor
