<div align="center">

<img src="assets/alfred/source/alfred_base.png" width="140" alt="Alfred mascot">

# Alfred

**A butler for your brain. Runs on your desktop. Zero AI inside.**

[![Platform](https://img.shields.io/badge/platform-Windows-0078D6?logo=windows&logoColor=white)](https://github.com/Zeyad-101/Alfred/releases)
[![Python](https://img.shields.io/badge/built%20with-Python-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![No AI](https://img.shields.io/badge/AI%20used-zero-black)](#why-no-ai)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

[Download](#installation) · [What it does](#what-alfred-does) · [Build from source](BUILD.md)

</div>

---

## What Alfred does

Alfred is a desktop assistant that remembers things so you don't have to.

Tell it about an appointment, and it'll bring it back up when it matters. Tell it something worth keeping, and it files it under a project. Ask it about that project three weeks later, and it hands you back everything you've told it — notes, tasks, schedule, all of it.

- **Notes** — write things down, Alfred keeps them
- **Schedule & tasks** — appointments and to-dos, with reminders
- **Projects** — group anything you tell it under a project, and query it later
- **Memory** — ask Alfred what it knows about something, and it tells you

It's less a chatbot and more a second brain that lives in your system tray.

## Why no AI

Alfred runs on plain Python logic — no LLM calls, no API keys, no account, nothing leaving your machine. Everything it "remembers" is stored locally at `%APPDATA%\Alfred`, and it survives an uninstall/reinstall.

If you've ever wanted something that just quietly keeps track of your stuff without phoning home, that's the whole pitch.

## Installation

Grab the installer from [Releases](https://github.com/Zeyad-101/Alfred/releases) and run it.

- Installs to `C:\Program Files\Alfred\` by default
- Adds a Start Menu shortcut (desktop shortcut optional)
- Registers a proper uninstaller in Add/Remove Programs

Prefer to run it from source instead? All you need is:

```bash
pip install -r requirements.txt
python main.py
```

No build step required for that. Full build/packaging instructions (PyInstaller + Inno Setup) are in [BUILD.md](BUILD.md).

## Meet Alfred

<img src="assets/alfred/frames/thinking/thinking_00.png" width="100" alt="Alfred thinking">

He sits in your tray and thinks about your schedule so you don't have to.

## Tech stack

- **Python** for the core logic and UI
- **PyInstaller** to package it into a single `.exe`
- **Inno Setup** for the Windows installer
- **pytest** for tests

## Contributing

Issues and pull requests are welcome. If Alfred's useful to you, a star helps other people find it.

## License

MIT — see [LICENSE](LICENSE).
