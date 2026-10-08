; The student sensor kit's installer (Inno Setup 6.5+). Built by EEGResearch\scripts\build_student_kit.ps1, which
; passes /DAppVersion, /DSourceDir (the staged AdaptiveLearningSensors folder) and /DOutputDir; /DUpdateOnly
; builds the Update installer, which carries no kit.json or update.json and changes an installed kit's code only.

#if Ver < EncodeVer(6, 5, 0)
  #error Inno Setup 6.5 or later: as SYSTEM, older versions extract into a temp folder users can write
#endif
#ifndef AppVersion
  #error Pass /DAppVersion=x.y.z
#endif
#ifndef SourceDir
  #error Pass /DSourceDir=<the staged AdaptiveLearningSensors folder>
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif
#define AppExe "AdaptiveLearningSensors.exe"
#define UpdateTask "AdaptiveLearning Sensors Update"

[Setup]
; Fixed for good: an upgrade finds the installed copy by this id.
AppId={{6B1E8C52-3F0A-4D7E-9C1B-2A5D8E7F4C31}
AppName=AdaptiveLearning Sensors
AppVersion={#AppVersion}
AppPublisher=AdaptiveLearning
DefaultDirName={autopf}\AdaptiveLearning Sensors
DefaultGroupName=AdaptiveLearning Sensors
DisableProgramGroupPage=yes
; Every user of the computer by default; "just for me" stays on offer for a machine without an admin.
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog commandline
; Without these {autopf} is Program Files (x86) for a 64-bit app.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
#ifdef UpdateOnly
OutputBaseFilename=AdaptiveLearningSensors-Update-{#AppVersion}
#else
OutputBaseFilename=AdaptiveLearningSensors-Setup-{#AppVersion}
#endif
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExe}
CloseApplications=force
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no

[Tasks]
; Only for an all-users install in Program Files, where students cannot write what the SYSTEM task runs.
Name: "autoupdate"; Description: "Keep the sensors up to date automatically"; Check: CanUpdateItself

[Files]
; For /DUpdateOnly, SourceDir is the build's copy without kit.json and update.json, scanned for both secrets.
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Start sensors"; Filename: "{app}\{#AppExe}"
Name: "{group}\Stop sensors"; Filename: "{app}\{#AppExe}"; Parameters: "--stop"
; The all-users Startup folder for an admin install, the user's own otherwise.
Name: "{autostartup}\AdaptiveLearning Sensors"; Filename: "{app}\{#AppExe}"

[Run]
; Not skipifsilent: an IT install with the box ticked registers the task too, and the task's own run keeps it.
Filename: "{app}\{#AppExe}"; Parameters: "--register-task"; Flags: runhidden waituntilterminated; Tasks: autoupdate
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""{#UpdateTask}"" /F"; Flags: runhidden waituntilterminated; Tasks: not autoupdate; Check: IsAdminInstallMode
; As the signed-in user, never as the admin or SYSTEM account that ran setup. A silent install waits for sign-in.
Filename: "{app}\{#AppExe}"; Description: "Start the sensors now"; Flags: postinstall nowait skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""{#UpdateTask}"" /F"; Flags: runhidden waituntilterminated; RunOnceId: "DeleteUpdateTask"; Check: IsAdminInstallMode
Filename: "{app}\{#AppExe}"; Parameters: "--stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopSensors"
; --stop reaches only this user's copy; elevated, this ends one in another user's session, children included.
Filename: "{sys}\taskkill.exe"; Parameters: "/F /T /IM {#AppExe}"; Flags: runhidden waituntilterminated; RunOnceId: "KillSensors"

[UninstallDelete]
; The updater's staged installers, status and logs.
Type: filesandordirs; Name: "{app}\updates"
; Logs are per user; only a just-for-me install knows whose to remove.
Type: filesandordirs; Name: "{localappdata}\AdaptiveLearning\Sensors"; Check: not IsAdminInstallMode

[Code]
function CanUpdateItself: Boolean;
begin
  Result := IsAdminInstallMode and
    (CompareText(ExpandConstant('{app}'), ExpandConstant('{commonpf64}\AdaptiveLearning Sensors')) = 0);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  OldExe: String;
  ResultCode: Integer;
begin
  Result := '';
#ifdef UpdateOnly
  { An Update installer brings no settings, so it only ever updates a kit a full installer set up. Exit code 7. }
  if not FileExists(ExpandConstant('{app}\kit.json')) then
  begin
    Result := 'No AdaptiveLearning Sensors kit is installed here. Install the full Setup first.';
    Exit;
  end;
#endif
  OldExe := ExpandConstant('{app}\{#AppExe}');
  if FileExists(OldExe) then
  begin
    { --stop returns once the old copy has exited, so its files can be replaced. }
    if not ExecAsOriginalUser(OldExe, '--stop', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
      Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;
