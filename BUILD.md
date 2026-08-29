# Building Alfred

Two separate steps, both Windows-only and both optional if you just want to run
Alfred from source (`python main.py` needs nothing here):

1. **PyInstaller** turns the source tree into a single `Alfred.exe`.
2. **Inno Setup** wraps that `.exe` into a `Setup.exe` with shortcuts and an
   uninstaller.

## 1. The executable

```
pip install -r requirements-dev.txt
pyinstaller build.spec
```

Output lands at `dist/Alfred.exe`. End users just need that one file -- no
Python, no pip, no source tree. Their database, settings, and backups live in
`%APPDATA%\Alfred\` (resolved at runtime by `core/paths.py`).

PyInstaller cannot overwrite a running binary, so close Alfred (including the
tray icon -- **Exit Alfred**, not just the window) before rebuilding, or the
build fails with `PermissionError: [WinError 5]`.

### Regenerating the art

Both are checked in, so a normal build never needs them:

```
python assets/generate_alfred_frames.py   # assets/alfred/frames/
python assets/build_icon.py               # icon.ico
```

`build_icon.py` crops its mascot from `assets/alfred/frames/idle/idle_00.png`,
so run the frame generator first if you change the source drawing.

## 2. The installer

Inno Setup is a standalone Windows tool, **not** a pip package -- it is
deliberately absent from `requirements-dev.txt`. Install it once from
<https://jrsoftware.org/isdl.php> (the default "Inno Setup 6" installer is
fine), then compile `installer.iss` either way:

* **GUI** -- open `installer.iss` in the Inno Setup Compiler and press
  *Build > Compile* (or `Ctrl+F9`).
* **Command line** -- `ISCC.exe` ships with Inno Setup and lands in
  `C:\Program Files (x86)\Inno Setup 6\` by default:

```
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
```

The result is `installer_output\Alfred-Setup-<version>.exe`. The script reads
`dist\Alfred.exe`, so run step 1 first -- Inno will refuse to compile with a
clear "file not found" error otherwise, which is the intended safety net
against shipping a stale binary.

What the installer does:

* installs to `C:\Program Files\Alfred\` by default, changeable in the wizard
* creates a Start Menu shortcut, plus a desktop shortcut if the user ticks the
  box on the *Select Additional Tasks* page
* registers an uninstaller in Add/Remove Programs, using `icon.ico`
* leaves `%APPDATA%\Alfred\` alone on uninstall, so notes survive removing and
  reinstalling the app

Bump `MyAppVersion` in `installer.iss` alongside `SETTINGS_ABOUT_VERSION` in
`ui/strings.py` when you cut a release; they are the two places a version
string appears.
