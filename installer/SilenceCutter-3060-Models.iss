#define Root ".."
#define Qwen Root + "\local_models\Qwen2.5-VL-7B-Instruct-AWQ"
#define Sense Root + "\desktop\src-tauri\target\release\resources\models\SenseVoiceSmall"
#define Vad Root + "\desktop\src-tauri\target\release\resources\models\fsmn-vad"

[Setup]
AppId={{B2CFB0C2-2C7B-4F75-9F9D-306012345678}
AppName=Silence Cutter 3060 Model Pack
AppVersion=0.1.0-3060
AppPublisher=Silence Cutter
DefaultDirName={localappdata}\SilenceCutter
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
DisableProgramGroupPage=yes
DisableDirPage=yes
WizardStyle=modern
Compression=lzma2/fast
SolidCompression=no
DiskSpanning=yes
DiskSliceSize=2000000000
OutputDir=..\release
OutputBaseFilename=SilenceCutter-3060-Models-Setup
SetupLogging=yes

[Files]
Source: "{#Qwen}\*"; DestDir: "{app}\models\Qwen2.5-VL-7B-Instruct-AWQ"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Sense}\*"; DestDir: "{app}\models\SenseVoiceSmall"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Vad}\*"; DestDir: "{app}\models\fsmn-vad"; Flags: ignoreversion recursesubdirs createallsubdirs

[Run]
Filename: "{localappdata}\Programs\SilenceCutter\Silence Cutter.exe"; Description: "Mở Silence Cutter"; Flags: nowait postinstall skipifsilent
