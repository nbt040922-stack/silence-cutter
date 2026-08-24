param([int]$Port = 8794)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw "Python runtime missing: $python" }
Start-Process -FilePath $python -ArgumentList @((Join-Path $root 'contentops_service_control.py'), $Port) -WorkingDirectory $root -WindowStyle Hidden
