$ErrorActionPreference = "Stop"
Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "SilenceCutterLanApi" -ErrorAction SilentlyContinue
Write-Host "Đã tắt tự khởi động LAN API."
