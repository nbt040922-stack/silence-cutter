$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$webView = Get-ChildItem (Join-Path $env:LOCALAPPDATA "tauri\x64") -Filter "MicrosoftEdgeWebView2RuntimeInstallerX64.exe" -Recurse -File |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $webView) { throw "WebView2 offline installer not found; run Tauri bundling once first" }
$assets = Join-Path $repo "installer_assets"
New-Item -ItemType Directory -Force -Path $assets | Out-Null
Copy-Item -LiteralPath $webView.FullName -Destination (Join-Path $assets $webView.Name) -Force

$compiler = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe")
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $compiler) { throw "Inno Setup 6 compiler not found" }
& $compiler (Join-Path $repo "installer\SilenceCutter.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup build failed with exit code $LASTEXITCODE" }
Get-FileHash (Join-Path $repo "release\SilenceCutter-Internal-RC-Setup.exe") -Algorithm SHA256
