[Setup]
AppId={{4AC4B6C2-E4B5-44D0-A652-000010282026}
AppName=EarShot Relay Staging
AppVersion=0.9.23
DefaultDirName={localappdata}\EarShotRelay-Staging
DefaultGroupName=EarShot Relay Staging
PrivilegesRequired=lowest
OutputDir=D:\PopYu\ds破\_staging\EarShot
OutputBaseFilename=EarShot-Relay-Staging-{#GetDateTimeString('yyyymmdd-hhnnss','','')}
Compression=lzma2/fast
SolidCompression=yes
UninstallDisplayIcon={app}\EarShot.exe
DisableProgramGroupPage=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no

[Files]
Source: "D:\PopYu\ds破\_staging\EarShot-complete\payload\EarShot\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\EarShot Relay Staging"; Filename: "{app}\EarShot.exe"
Name: "{group}\Settings"; Filename: "{app}\EarShot.exe"; Parameters: "--run ui/settings_win.py"
Name: "{group}\Uninstall"; Filename: "{uninstallexe}"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\.tmp"
Type: files; Name: "{app}\.runtime_port"
Type: files; Name: "{app}\.runtime_profile"
