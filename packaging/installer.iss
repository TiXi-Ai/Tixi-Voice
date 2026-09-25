; Inno Setup script for Tixi Voice.
;
; Build:  iscc packaging\installer.iss /DAppVersion=1.0.0
; Output: dist\installer\TixiVoice-Setup-1.0.0.exe
;
; The installer never downloads models: it installs the application only and
; offers to open the AI Models page afterwards. Uninstalling keeps the user's
; data (settings, history, models) unless they ask for it to be removed.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

#define AppName        "Tixi Voice"
#define AppPublisher   "TiXi-Ai"
#define AppURL         "https://github.com/TiXi-Ai/Tixi-Voice"
#define AppExeName     "TixiVoice.exe"
#define SourceDir      "..\dist\TixiVoice"

[Setup]
AppId={{8F4A0C21-3B7E-4C9A-9D3E-5F0A1B2C3D4E}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
LicenseFile={#SourceDir}\licenses\LICENSE.txt
OutputDir=..\dist\installer
OutputBaseFilename=TixiVoice-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\{#AppExeName}
SetupIconFile=tixi-voice.ico
ShowLanguageDialog=auto

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "farsi";   MessagesFile: "compiler:Languages\Farsi.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startup";     Description: "Start {#AppName} with Windows"; GroupDescription: "Startup:"; Flags: unchecked
Name: "associate";   Description: "Open .tixi library files with {#AppName}"; GroupDescription: "File associations:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "..\docs\*"; DestDir: "{app}\docs"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Parameters: "--minimised"; Tasks: startup

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{#AppExeName}"; Parameters: "--open models"; Description: "Install a Persian voice and a speech-recognition model"; Flags: postinstall skipifsilent unchecked

[UninstallDelete]
Type: filesandordirs; Name: "{app}\docs"

[Code]
// Never delete user data silently: ask, and default to keeping everything.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\TixiVoice');
    if DirExists(DataDir) then
    begin
      if MsgBox('Your models, recordings and history are stored in:' + #13#10 + DataDir +
                #13#10#13#10 + 'Delete this folder as well?' + #13#10 +
                '(Choose No to keep your downloaded models for a future reinstall.)',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not IsWin64 then
  begin
    MsgBox('{Tixi Voice requires 64-bit Windows 10 or newer.', mbCriticalError, MB_OK);
    Result := False;
  end;
end;
