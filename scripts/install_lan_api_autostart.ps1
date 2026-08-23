$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$launcher = Join-Path $repo "scripts\start_lan_job_api_hidden.vbs"
$wscript = (Get-Command wscript.exe).Source
$action = "`"$wscript`" //B //Nologo `"$launcher`""

$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
New-Item -Path $runKey -Force | Out-Null
Set-ItemProperty -Path $runKey -Name "SilenceCutterLanApi" -Value $action
Write-Host "Đã bật tự khởi động LAN API cùng Windows."
Write-Host "Cổng LAN: 8780"
