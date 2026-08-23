param(
    [int]$Port = 8780
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$env:SILENCE_CUTTER_LAN_PORT = "$Port"
Set-Location $repo

# Chỉ mở cổng nhận job; không khởi động lại Qwen hay Content Ops bridge.
$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = Join-Path $repo ".venv_asr_test\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $python)) { $python = (Get-Command python -ErrorAction Stop).Source }
& $python -m lan_job_api
