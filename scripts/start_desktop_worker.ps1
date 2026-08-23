$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$env:SILENCE_CUTTER_DATA_DIR = $repo
$python = Join-Path $repo ".venv_asr_test\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { $python = Join-Path $repo ".venv\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $python)) { throw "Silence Cutter Python runtime missing" }
$pythonPattern = [regex]::Escape($python)
$stdout = Join-Path $repo "desktop-worker.stdout.log"
$stderr = Join-Path $repo "desktop-worker.stderr.log"
$folderHelper = Join-Path $repo "scripts\start_folder_helper.ps1"

while ($true) {
    $existingHelper = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'start_folder_helper\.ps1' } |
        Select-Object -First 1
    if (-not $existingHelper) {
        Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", $folderHelper) -WorkingDirectory $repo -WindowStyle Hidden | Out-Null
    }
    $existing = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -match 'backend\.job_runner\s+worker' -and
            $_.CommandLine -match $pythonPattern
        } |
        Select-Object -First 1
    if ($existing) {
        Wait-Process -Id ([int]$existing.ProcessId) -ErrorAction SilentlyContinue
    } else {
        $worker = Start-Process -FilePath $python -ArgumentList @("-m", "backend.job_runner", "worker") `
            -WorkingDirectory $repo -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        Wait-Process -Id $worker.Id -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 5
}
