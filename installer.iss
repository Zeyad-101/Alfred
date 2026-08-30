; Inno Setup script for Alfred.
;
; Wraps the single-file PyInstaller binary at dist\Alfred.exe into a
; conventional Windows Setup.exe: Program Files install, Start Menu
; shortcut, an optional desktop shortcut, and an Add/Remove Programs
; entry with a working uninstaller.
;
; Inno Setup is a standalone tool, NOT a pip package -- see BUILD.md.
; Compile with:
;
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
;
; Run `pyinstaller build.spec` first. This script reads dist\Alfred.exe
; and Inno refuses to compile if it is absent, which is the intended
; safety net against shipping a stale binary.
;
; MyAppVersion below and SETTINGS_ABOUT_VERSION in ui/strings.py are the
; two places a version string appears. Bump them together.

#define MyAppName "Alfred"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Zeyad Waled"
#define MyAppExeName "Alfred.exe"

[Setup]
; AppId is the identity Add/Remove Programs keys off. It must stay
; constant across versions so an upgrade replaces the old entry
; instead of stacking a second one beside it. Never regenerate it.
AppId={{C84C38BF-2BE0-4F59-9F43-0B983F5CD311}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}

; {autopf} resolves to Program Files, or Program Files (x86) if the
; install is running in 32-bit mode -- hence the architecture line
; below. The user can still change the location in the wizard.
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes

; Alfred is a 64-bit build (64-bit Python + PySide6), so install into
; the native 64-bit Program Files. Requires Inno Setup 6.3 or later;
; on 6.0-6.2 replace with `ArchitecturesInstallIn64BitMode=x64`.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; Writing to Program Files needs elevation.
PrivilegesRequired=admin

OutputDir=installer_output
OutputBaseFilename=Alfred-Setup-{#MyAppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\icon.ico
UninstallDisplayName={#MyAppName} {#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; Unchecked by default, so the desktop shortcut is opt-in on the
; "Select Additional Tasks" wizard page rather than something the
; user has to notice and untick.
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The onefile PyInstaller build is genuinely self-contained -- one exe,
; no _internal directory, no Python runtime to lay down beside it.
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; Installed alongside so Add/Remove Programs has an icon to display
; (UninstallDisplayIcon above points here). The icon the *running* app
; wears is already baked into the exe by build.spec.
Source: "icon.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    IconFilename: "{app}\icon.ico"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    IconFilename: "{app}\icon.ico"; Tasks: desktopicon

[Run]
; nowait + postinstall: offer to launch on the final wizard page
; without holding the installer open behind the app.
Filename: "{app}\{#MyAppExeName}"; \
    Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; \
    Flags: nowait postinstall skipifsilent

; Deliberately no [UninstallDelete] section touching {userappdata}\Alfred.
; That directory holds the user's database, settings and backups (see
; core/paths.py). Uninstalling the app must not delete the notes -- they
; survive a remove-and-reinstall cycle, and removing them is the user's
; call to make by hand.
