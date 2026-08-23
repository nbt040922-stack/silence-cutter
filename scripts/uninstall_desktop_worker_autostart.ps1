$ErrorActionPreference = "SilentlyContinue"
Unregister-ScheduledTask -TaskName "Silence Cutter Desktop Worker" -Confirm:$false
Write-Host "Đã tắt tự khởi động Desktop Worker."
