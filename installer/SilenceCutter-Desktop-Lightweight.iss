#define Root ".."
#define Payload Root + "\release\SilenceCutter-Desktop-Lightweight"

[Setup]
AppId={{A9C3F0E0-2E91-4D2A-9B12-3F0A0D4A7C11}
AppName=Silence Cutter Desktop
AppVersion=0.1.0-lightweight
AppPublisher=Silence Cutter
DefaultDirName={localappdata}\Programs\SilenceCutterDesktop
DefaultGroupName=Silence Cutter Desktop
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
DisableDirPage=yes
WizardStyle=modern
Compression=lzma2/fast
SolidCompression=no
OutputDir=..\release
OutputBaseFilename=SilenceCutter-Desktop-Lightweight-Setup
UninstallDisplayIcon={app}\Silence Cutter.exe
SetupLogging=yes

[Files]
Source: "{#Payload}\Silence Cutter.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Payload}\Run-SilenceCutter.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Payload}\Run-SilenceCutter.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Payload}\README.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Silence Cutter Desktop"; Filename: "{app}\Run-SilenceCutter.cmd"
Name: "{autodesktop}\Silence Cutter Desktop"; Filename: "{app}\Run-SilenceCutter.cmd"

[Run]
Filename: "{app}\Run-SilenceCutter.cmd"; Description: "Mở Silence Cutter"; Flags: nowait postinstall skipifsilent
