#define Root ".."
#define Payload Root + "\build\silence_core_payload"

[Setup]
AppId={{A1E2A04C-721E-4A25-B74E-CONTENTOPSCORE}
AppName=Silence Core
AppVersion=0.1.0
AppPublisher=ContentOps
DefaultDirName={autopf}\ContentOps\SilenceCore
DefaultGroupName=ContentOps Silence Core
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
DisableDirPage=yes
WizardStyle=modern
Compression=none
SolidCompression=no
DiskSpanning=yes
DiskSliceSize=2000000000
OutputDir=..\release
OutputBaseFilename=Silence_Core_Setup
SetupLogging=yes
Uninstallable=yes

[Files]
Source: "{#Payload}\qwen\*"; DestDir: "{app}\qwen"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Payload}\scheduler\*"; DestDir: "{app}\scheduler"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Payload}\lan\*"; DestDir: "{app}\lan"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Payload}\supervisor\*"; DestDir: "{app}\supervisor"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Payload}\tools\*"; DestDir: "{app}\tools"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Payload}\core_model_manifest.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Payload}\silence_core_setup\*"; DestDir: "{app}\silence_core_setup"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Root}\installer\start_silence_core.cmd"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{commonappdata}\ContentOps\SilenceCore\config"
Name: "{commonappdata}\ContentOps\SilenceCore\state"
Name: "{commonappdata}\ContentOps\SilenceCore\queue"
Name: "{commonappdata}\ContentOps\SilenceCore\logs\installer"
Name: "{commonappdata}\ContentOps\SilenceCore\models\qwen2.5-vl-7b"

[Run]
Filename: "{app}\silence_core_setup\silence_core_setup.exe"; Parameters: "install --manifest=""{app}\core_model_manifest.json"""; Flags: waituntilterminated; StatusMsg: "Đang cài Qwen và khởi động Silence Core..."
Filename: "{sys}\schtasks.exe"; Parameters: "/Create /TN ""ContentOps\SilenceCore"" /SC ONSTART /DELAY 0001:00 /RU SYSTEM /RL HIGHEST /F /TR ""{app}\start_silence_core.cmd"""; Flags: runhidden waituntilterminated; StatusMsg: "Đang bật tự khởi động Silence Core..."

[UninstallRun]
Filename: "{app}\silence_core_setup\silence_core_setup.exe"; Parameters: "stop"; Flags: waituntilterminated skipifdoesntexist
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""ContentOps\SilenceCore"" /F"; Flags: runhidden skipifdoesntexist
