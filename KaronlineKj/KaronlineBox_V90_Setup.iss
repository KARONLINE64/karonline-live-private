; Script Inno Setup pour KaronlineBox_V90.
; Empaquette dist\KaronlineBox_V90\ (produit par PyInstaller) en un
; installeur unique KaronlineBox_V90_Setup.exe, utilisable par
; telechargement ou depuis une cle USB.

#define MyAppName "KaronlineBox"
; Numero de version : a incrementer a chaque nouveau setup publie.
#define MyAppVersionShort "V91.1"
#define MyAppBuildDate "05-09-2026"
#define MyAppVersion MyAppVersionShort + " (" + MyAppBuildDate + ")"
#define MyAppExeName "KaronlineBox_V90.exe"
#define MySourceDir "dist\KaronlineBox_V90"

[Setup]
AppId={{6F1B0B7B-6E9E-4A6E-9B79-KARONLINEV90}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=.
OutputBaseFilename=KaronlineBox_V91_1_Setup
SetupIconFile=ui\kb_logo_luxury.ico
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; GroupDescription: "Raccourcis :"

[Files]
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; cloudflared.exe n'est plus necessaire : KaronlineBox utilise desormais le
; relais central (connexion sortante uniquement, aucun tunnel/port entrant).
; Bundle GStreamer allege (quelques dizaines de Mo, uniquement les plugins
; utilises par KaronlineBox) genere par tools\build_gstreamer_bundle.ps1.
; Optionnel : si redist\gstreamer_runtime est absent, l'installeur proposera
; simplement d'ouvrir la page de telechargement officielle a la place.
Source: "redist\gstreamer_runtime\*"; DestDir: "{app}\gstreamer_runtime"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Désinstaller {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Lancer {#MyAppName} maintenant"; Flags: nowait postinstall skipifsilent

[Code]
const
  GStreamerDownloadUrl = 'https://gstreamer.freedesktop.org/download/#windows';

function SystemGStreamerInstalled(): Boolean;
begin
  Result := DirExists('C:\Program Files\gstreamer\1.0\msvc_x86_64\bin');
end;

{ Vrai si le bundle allege redist\gstreamer_runtime a ete inclus a la
  compilation et copie a cote de l'exe (voir tools\build_gstreamer_bundle.ps1). }
function BundledGStreamerInstalled(): Boolean;
begin
  Result := DirExists(ExpandConstant('{app}\gstreamer_runtime\bin'));
end;

procedure OfferGStreamerDownload();
var
  ResultCode: Integer;
begin
  if SystemGStreamerInstalled() or BundledGStreamerInstalled() then
    Exit;

  if MsgBox(
    'GStreamer n''est pas détecté sur cet ordinateur.' + #13#10 +
    'KaronlineBox en a besoin pour le son et la vidéo.' + #13#10#13#10 +
    'Voulez-vous ouvrir la page de téléchargement officielle maintenant ?',
    mbConfirmation, MB_YESNO) = IDYES then
  begin
    ShellExec('open', GStreamerDownloadUrl, '', '', SW_SHOWNORMAL, ewNoWait, ResultCode);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  // verifie apres la copie des fichiers : le bundle allege (s'il a ete inclus)
  // est alors deja present dans le dossier d'installation
  if CurStep = ssPostInstall then
    OfferGStreamerDownload();
end;

