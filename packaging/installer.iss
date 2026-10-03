; Inno Setup 6 script — Windows installer for Luut Video Downloader.
; The executable bundles Python, Qt and FFmpeg; yt-dlp.exe ships beside it (bin\) and is updated by the app, so the installer only needs to
; place it, create shortcuts and register the uninstaller. No administrator rights are required.
;
; Build: run build.bat first, then:  iscc packaging\installer.iss

#define AppName "Luut Video Downloader"
#define AppVersion "1.3.0"
#define AppPublisher "Luut"
#define AppExe "Luut Video Downloader.exe"

[Setup]
AppId={{6B7B0C57-5E1B-4C0E-9A3E-2F1C4D7A9B10}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist\installer
OutputBaseFilename=LuutVideoDownloader-Setup-{#AppVersion}
SetupIconFile=..\assets\images\app.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription=Instalador do {#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
; Seed copy of the official yt-dlp.exe. The app copies it to %LOCALAPPDATA%\LuutVideoDownloader\bin on first run and
; updates it there (no administrator rights needed, no reinstall of Luut when yt-dlp changes).
Source: "..\dist\bin\yt-dlp.exe"; DestDir: "{app}\bin"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Desinstalar {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; "Iniciar com o Windows" is created by the app itself (per user); only removed here on uninstall.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "LuutVideoDownloader"; Flags: uninsdeletevalue dontcreatekey

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM ""{#AppExe}"" /F"; Flags: runhidden; RunOnceId: "StopApp"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\LuutVideoDownloader');
    if DirExists(DataDir) then
      if SuppressibleMsgBox('Deseja apagar também o histórico, as configurações e os logs do aplicativo?' + #13#10 +
                'Os vídeos baixados não serão excluídos.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
