[Setup]
AppId={{B3F9B1C2-4B51-4B62-9D6A-BOOTSTRAPCORE}
AppName=Silence Core Bootstrap
AppVersion=0.2.0
DefaultDirName={autopf}\ContentOps\SilenceCoreBootstrap
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
DisableDirPage=yes
Compression=lzma2/fast
SolidCompression=no
OutputDir=..\release
OutputBaseFilename=Silence_Core_Bootstrap_Setup
SetupLogging=yes

[Files]
Source: "..\build\silence_core_bootstrap\bootstrap_install.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\build\silence_core_bootstrap\source.zip"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\requirements-production.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\installer\core_model_manifest.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\installer\core_model_manifest_3b.json"; DestDir: "{app}"; Flags: ignoreversion

[Run]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\bootstrap_install.ps1"""; Flags: waituntilterminated; StatusMsg: "Đang tải runtime CUDA, model và khởi động Silence Core..."
