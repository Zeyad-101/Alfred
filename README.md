<div align="center">

<img src="assets/alfred/source/alfred_base.png" width="140" alt="Alfred mascot">

# Alfred

**A butler who lives on your desktop and remembers everything you tell him. Zero AI inside.**

[![Platform](https://img.shields.io/badge/platform-Windows-0078D6?logo=windows&logoColor=white)](https://github.com/Zeyad-101/Alfred/releases)
[![Python](https://img.shields.io/badge/built%20with-Python%20%2B%20PySide6-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![No AI](https://img.shields.io/badge/AI%20used-zero-black)](#why-no-ai)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

[Download](#installation) · [What it does](#what-alfred-does) · [Build from source](BUILD.md)

</div>

---

## What Alfred does

Alfred is a floating desktop companion that captures your notes, tasks, and appointments, and hands them back the moment you ask.

He doesn't sit in a browser tab. He's an actual sprite on your desktop — draggable, always on top — with a global hotkey that opens a one-line capture box from anywhere, on top of any app you're in.

- **Capture from anywhere** — hit your hotkey, type a thought or a question, hit Enter. Under a second, no window-switching.
- **Same field, two jobs** — type "remember that..." to save something, or "what is...?" to ask. Alfred pulls a real answer from what you've already told him, no LLM involved.
- **Notes, tasks, and projects** — write things down, group them under a project, and ask about that project weeks later — Alfred hands back everything filed under it.
- **Understands "tomorrow" and "next friday"** — due dates parsed from plain text, entirely offline.
- **Full-text search** across everything you've ever stored.
- **Nothing's really deleted** — deleted items go to a trash you can restore from, not straight to the void.
- **Export and back up** your whole database to JSON whenever you want, no lock-in.
- **Inbox capture** — jot something down fast, organize or convert it into a task later, whenever you actually have time.

Click the companion once and the capture popup opens next to him. Double-click him and the full window opens.

## Why no AI

Alfred runs on plain Python logic — no LLM calls, no API keys, no account, nothing leaving your machine. Everything he "remembers" lives locally in `%APPDATA%\Alfred`, and it survives an uninstall/reinstall.

If you've wanted something that just quietly keeps track of your stuff without sending it anywhere, that's the whole pitch.

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

He idles quietly on your desktop, switches to a thinking pose when he's looking something up, and gives you a one-shot greeting on launch. Drag him anywhere — he remembers where you left him.

## Tech stack

- **Python + PySide6 (Qt)** for the app and the desktop companion window
- **SQLite (with FTS5)** for storage and full-text search
- **PyInstaller** to package everything into a single `.exe`
- **Inno Setup** for the Windows installer

## Contributing

Issues and pull requests are welcome. If Alfred's useful to you, a star helps other people find it.

## License

MIT — see [LICENSE](LICENSE).
