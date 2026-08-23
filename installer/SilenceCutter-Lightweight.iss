#define Root ".."
#define Payload Root + "\lightweight_release\Silence Cutter"
#define WebView Root + "\installer_assets\MicrosoftEdgeWebView2RuntimeInstallerX64.exe"

[Setup]
AppId={{8A8ACB35-1B2B-4E35-9D53-8A9F4A1F4D0C}
AppName=Silence Cutter
AppVersion=0.1.0-lightweight
AppPublisher=Silence Cutter
DefaultDirName={localappdata}\Programs\SilenceCutter
DefaultGroupName=Silence Cutter
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
DisableDirPage=yes
WizardStyle=modern
Compression=lzma2/fast
SolidCompression=no
OutputDir=..\release
OutputBaseFilename=SilenceCutter-Lightweight-Setup
UninstallDisplayIcon={app}\Silence Cutter.exe
SetupLogging=yes

[Files]
Source: "{#Payload}\Silence Cutter.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Payload}\resources\*"; DestDir: "{app}\resources"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#WebView}"; Flags: dontcopy

[Icons]
Name: "{autoprograms}\Silence Cutter"; Filename: "{app}\Silence Cutter.exe"
Name: "{autodesktop}\Silence Cutter"; Filename: "{app}\Silence Cutter.exe"

[Run]
Filename: "{tmp}\MicrosoftEdgeWebView2RuntimeInstallerX64.exe"; Parameters: "/silent /install"; StatusMsg: "Đang chuẩn bị thành phần giao diện..."; Flags: waituntilterminated
Filename: "{app}\Silence Cutter.exe"; Description: "Mở Silence Cutter"; Flags: nowait postinstall skipifsilent

[Code]
procedure InitializeWizard;
begin
  ExtractTemporaryFile('MicrosoftEdgeWebView2RuntimeInstallerX64.exe');
end;
