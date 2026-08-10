#define Root ".."
#define Payload Root + "\internal_release_rc\Silence Cutter RC"
#define WebView Root + "\installer_assets\MicrosoftEdgeWebView2RuntimeInstallerX64.exe"

[Setup]
AppId={{4A32B5EC-EE50-4F5B-B76E-A9CA6B6D0281}
AppName=Silence Cutter
AppVersion=0.1.0-rc.1
AppPublisher=Silence Cutter Internal
DefaultDirName={localappdata}\Programs\SilenceCutter
DefaultGroupName=Silence Cutter
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
DisableDirPage=yes
DisableWelcomePage=yes
WizardStyle=modern
Compression=lzma2/fast
SolidCompression=no
OutputDir=..\release
OutputBaseFilename=SilenceCutter-Internal-RC-Setup
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
Filename: "{tmp}\MicrosoftEdgeWebView2RuntimeInstallerX64.exe"; Parameters: "/silent /install"; StatusMsg: "Preparing application runtime..."; Flags: waituntilterminated
Filename: "{app}\Silence Cutter.exe"; Description: "Open Silence Cutter"; Flags: nowait postinstall skipifsilent

[Code]
procedure InitializeWizard;
begin
  ExtractTemporaryFile('MicrosoftEdgeWebView2RuntimeInstallerX64.exe');
end;
