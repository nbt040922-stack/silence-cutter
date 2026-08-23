$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = (Get-Command python -ErrorAction Stop).Source
}
Set-Location $repo

# Chạy helper ẩn; Chrome vẫn hiện để người dùng đăng nhập thủ công.
$code = 'from backend.job_runner import open_youtube_login; open_youtube_login()'
Start-Process -FilePath $python -ArgumentList @("-c", $code) -WorkingDirectory $repo -WindowStyle Hidden
Write-Host "Đã mở Chrome với profile YouTube riêng. Hãy đăng nhập trong cửa sổ Chrome."
