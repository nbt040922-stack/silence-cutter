$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$launcher = Join-Path $repo "scripts\start_desktop_worker_hidden.vbs"
$wscript = (Get-Command wscript.exe).Source
$taskName = "Silence Cutter Desktop Worker"
$action = New-ScheduledTaskAction -Execute $wscript -Argument "//B //Nologo `"$launcher`"" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Tự khởi động Desktop Worker của Silence Cutter" -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Host "Đã bật tự khởi động Desktop Worker."
