$ErrorActionPreference = "Stop"
$source = Join-Path $PSScriptRoot "Silence Cutter RC"
$install = Join-Path $env:LOCALAPPDATA "Programs\SilenceCutter-RC"
if (-not (Test-Path -LiteralPath (Join-Path $source "Silence Cutter.exe"))) {
    throw "RC payload is incomplete: $source"
}
New-Item -ItemType Directory -Force -Path $install | Out-Null
Copy-Item -Path (Join-Path $source "*") -Destination $install -Recurse -Force

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "Silence Cutter RC.lnk"))
$shortcut.TargetPath = Join-Path $install "Silence Cutter.exe"
$shortcut.WorkingDirectory = $install
$shortcut.Save()
Write-Host "Silence Cutter RC installed at $install"
