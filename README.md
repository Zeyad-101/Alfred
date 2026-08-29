# Alfred

A local-first personal knowledge base -- notes, tasks, inbox, projects, full-text search, manual backups, all in a single SQLite file you can copy, back up, or delete without ceremony. Built as part of the 30 Days / 30 Projects challenge.

## Features

- **Notes, inbox, tasks, projects** -- four content types in one schema, with FTS5-powered search across the lot
- **Dashboard** -- the default landing view: a time-of-day greeting, a counts row (memories, open tasks, projects), and what is due today, all read through existing `core/` queries
- **Trash** -- deletes are soft; the Trash view previews an entry before you restore it or remove it for good
- **Export and import** -- the File menu writes every entry, tag, project and link to a single JSON file and reads one back (additive, never merging or deduplicating)
- **Keyword project filing** -- give a project a list of topic keywords in Settings and any capture mentioning one is filed under it; naming the project outright always wins over a keyword match
- **Tags and related-entries links** -- many-to-many tagging plus explicit `memory_links` rows for "see also" navigation
- **Version history** -- every save snapshots the previous version; restore from the History dialog
- **Periodic task reminders** -- tray balloon when open-task count changes (configurable interval)
- **Manual backup & restore** -- timestamped `.db` copies, restorable from the Settings dialog
- **Settings dialog** -- hotkey change, storage location, backup folder, notifications, danger zone (vacuum, reset, restore)
- **Global hotkey** -- `Ctrl+Space` pops a frameless quick-capture window from anywhere
- **System tray** -- close-to-tray, reopen from the tray, quick-capture from the tray menu
- **Butler interaction layer** -- the quick-capture box also answers a fixed set of question shapes from your own data ("what's my name?", "what are my tasks today?", "what do we have tomorrow?", "anything due today?", "what's left on the Batman project?", "what did I work on yesterday?", "open my tasks", "open my ESP32 project", "add a task to my ESP32 project: test the sensor"). This is curated pattern matching with templated wording, not NLU: recognized shapes get specific answers built from real rows, everything else falls back to a plain FTS5 search. Captures can name their project inline -- "remember that I need to test the sensor to the ESP32 project" files the note and links it to ESP32, creating that project if it doesn't exist yet. Project requests are classified into distinct shapes rather than one catch-all regex -- a *question* about a project only reads (and never creates one), "open my X project" confirms and switches the view without running a search, and "add a task to my X project: ..." performs a real write through `core/projects.py` before Alfred says it did
- **Pixel-art butler desktop companion** -- a small butler sprite that floats always-on-top on your desktop: greets on launch, cycles through idle poses, draggable to any screen position (persisted), single-click to capture, double-click to open Alfred. Every pose is derived procedurally from one drawing by `assets/generate_alfred_frames.py` -- the figure is cut into head / torso / napkin / legs at its own anatomy and each layer is transformed about a pivot sitting on its seam, so a breathing idle, a head-tilt-and-thought-cloud think and a foreshortened bow all come out of the single original sprite without redrawing it. The same frames are the source for `icon.ico`, so the taskbar icon and the companion are always the same character
- **He speaks out of himself** -- ask him anything and the sprite visibly thinks while he works, then the reply grows out of him as a tailed speech bubble anchored beside him on *every* path (the `Ctrl+Space` hotkey included, not just clicking the mascot). The bubble is non-modal, so the app behind it keeps working while he talks
- **Dark theme only**, applied unconditionally at startup
- **Packaged Windows .exe** -- single-file, no Python install required for end users

## Tech stack

- **Python 3.12** with **PySide6** (Qt 6) for the UI
- **SQLite + FTS5** for storage and search (zero-config, file-based)
- **markdown** for rendering note previews
- **keyboard** (with **pynput** fallback) for the global hotkey
- **Pillow** for the two build-time art tools under `assets/` (dev dependency only -- their output is checked in)
- **PyInstaller** for the single-file Windows build, **Inno Setup** for the installer (both dev-only)

## How it works

Alfred is a Qt desktop app talking to a local SQLite database. There is no server, no network call, no telemetry. The data file is a regular `.db` next to the source (dev) or in `%APPDATA%\Alfred\` (packaged), readable by any SQLite tool -- `DB Browser for SQLite`, the `sqlite3` CLI, etc. Search uses SQLite's built-in FTS5 with auto-sync triggers so the index never gets out of date. The whole codebase is single-threaded UI plus the one worker thread that owns the global-hotkey library's keyboard hook (which marshals back to the main thread via a `QObject` signal so it never touches widgets directly).

## Running locally (dev mode)

```
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe main.py
```

Data files land in `./data/` (the database, settings.json, and a `backups/` subdirectory).

## Running the packaged .exe

Build once (see `BUILD.md`):

```
venv\Scripts\python.exe -m pip install -r requirements-dev.txt
venv\Scripts\python.exe -m PyInstaller build.spec
```

Then double-click `dist/Alfred.exe`. No Python install required on the target machine. The packaged build stores its data in `%APPDATA%\Alfred\` (per-user app data) so it survives reinstalls and stays writable even when the `.exe` lives under `Program Files`.

## Installing (Windows)

`installer.iss` wraps `dist/Alfred.exe` into a conventional `Setup.exe` -- Program Files install, Start Menu shortcut, optional desktop shortcut, and an Add/Remove Programs entry. It needs [Inno Setup](https://jrsoftware.org/isdl.php) installed locally; it is not a pip dependency. See `BUILD.md` for the two-line recipe.

## What I learned

- **PyInstaller's `sys._MEIPASS` pattern** is the right way to ship read-only assets -- `core/paths.py` centralises the resolution so every other module just calls `asset_path("...")` without thinking about whether it's running from source or from a frozen bundle.
- **Per-user data belongs in `%APPDATA%`**, not next to the binary -- a `.exe` the user might place under `Program Files` (or copy around) shouldn't be the source of truth for their notes.
- **The 5-minute path-refactor cost me a test fixture**: rewriting `core/settings.py` to take an `Optional[str]` path arg instead of a default-value string forced every test that called `save_settings(DEFAULT_SETTINGS)` (no path) to keep working. The fix was small but it was a useful reminder that "make it optional" is rarely a one-line change in a codebase with broad call-sites.
- **Match the scaling mode to the art, or avoid scaling entirely**: the first mascot was a 24x32 pixel grid blown up at runtime, which needed `Qt.FastTransformation` (nearest-neighbour) to stay crisp. The current companion sidesteps the question -- `assets/generate_alfred_frames.py` authors every frame at 160x160, which is exactly the size the window displays, so `ui/alfred_pet.py` sets `setScaledContents(False)` and blits 1:1. No resample, no filter choice, no judgement call about art style at runtime.
- **Drag-vs-click on a transparent window**: `mouseDoubleClickEvent` doesn't conflict with manual drag detection the way you'd think -- you record the press, ignore `mouseMoveEvent` until the cursor moves past a small threshold, then on `mouseReleaseEvent` either start a single-shot timer (which `mouseDoubleClickEvent` can cancel) or treat it as a drag. Cleaner than the "double-click = single + single within X ms" recipe.
- **PyInstaller + PySide6 works out of the box** with `hiddenimports=['PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets']` and a `datas=[('assets/alfred/frames/idle/*.png', 'assets/alfred/frames/idle')]` entry -- no hook scripts needed for a simple Qt6 app.
- **Don't ship a console window** with a desktop app -- `console=False` in `build.spec`, otherwise the user gets a black cmd window next to the GUI on every launch.

- **Perceived latency is a design axis, not a measurement.** The butler layer classifies every question on two axes: whether it needs a lookup at all, and -- if it does -- whether that lookup is *trivial* (one row, one regex) or *substantial* (a search, a task bucket, a project roll-up). Only substantial work is allowed to show "Lemme check, sir." That split matters because the two are independent: a trivial question that happens to run slowly must still feel instant, so the thinking state is never engaged for it at all. Making that structural rather than timing-dependent is also what makes it testable -- the test asserts the state was never engaged, not that a timer happened not to fire.
- **A minimum *visibility* is not the same thing as a delay.** The first version announced "Lemme check, sir." only once a lookup had already run for 300 ms, which in practice meant never -- local SQLite over a few thousand rows finishes in single digits, so the state existed but nobody ever saw it. The fix is to show it immediately and hold the *reveal* of the answer for 500 ms, which is a different thing from sleeping for 500 ms: the query starts at once and runs at full speed, and the answer is published when both it and the timer have finished, whichever is later. An 80 ms query is revealed at ~500 ms; a 900 ms query at ~900 ms, never padded to 1400. Nothing blocks the event loop to achieve it -- a finished answer is *parked* in an attribute and published by the timer's `timeout`, so the two completions join in either order and the window stays responsive and repainting the whole time.
- **Re-asserting a state has to be a no-op, not a restart.** The companion looked frozen even though the frames, the timer and the packaging were all correct: `set_pose` restarted from frame 0 unconditionally, so every re-assertion of the *same* state -- and the search box was re-asserting on every keystroke -- dragged the sprite back to frame 0 before the second frame could paint. Making `set_pose` idempotent (same state + live timer = return early, with an explicit `restart=True` for a genuine replay) is what turned a correct-on-paper frame engine into visible motion. The lesson generalises: an "apply this state" call that is cheap to issue will be issued often, so it has to be *safe* to issue often.
- **Hold a pose on the animation's own clock, not a second timer.** A 12-frame sway released the instant the answer arrived showed 3-5 frames and read as a twitch. The fix is not a minimum duration in milliseconds -- that is a guess about frame counts and intervals that goes stale the moment the art changes. Instead the pet emits `cycle_completed` when it wraps, and a release that lands mid-cycle parks itself until that signal, so the loop always finishes the lap it is on. One timer stays in charge of frames; nothing races it.
- **A background thread needs its own SQLite connection, not a lock.** Substantial lookups run on a `QThreadPool` worker that opens a short-lived second connection to the same file and closes it when done. The alternative -- serialising queries back onto the owning thread through a queue -- would have moved the work off the UI thread in name only.

- **A feather needs something behind it.** Posing by layer transform means the upper layer keeps a few rows past its seam with alpha ramped to zero, so its cut edge dissolves instead of showing a step. That is only half the trick: when the chest lifts, that ramp rides *up* with it, and if the layer beneath starts exactly at the seam then the ramped rows are sitting over bare canvas -- a 2px translucent band straight across his hip on every frame where he breathes in. The fix is not more feathering but the opposite one: the *lower* layer extends a few rows back up behind the seam at full alpha. It costs nothing, because the lower layer is drawn first and completely covered.
- **A test can be green and still be measuring the wrong thing.** The animation checks asserted the frames loaded, were 160x160, and were distinct `QPixmap` *objects* -- and 28 copies of the same drawing satisfy all three, which is exactly what shipped. Object identity is not image content. What they should have asserted is what a person would actually notice: consecutive frames differ by a real percentage of the figure's footprint, `thinking_NN` differs from `idle_NN` above the shoulders, no frame duplicates another except the one intentional hold at the bottom of the bow, and no pixel that is solid on all sides is translucent. Every one of those would have failed on the old art.
- **The reaction and the excuse are two different cues.** "Lemme check, sir." should only appear when there is genuinely something to check, but the *sprite* should react to being spoken to at all -- a butler who answers instantly from a dead stop reads as a text box. Those are one signal only if you conflate "I am working" with "this is taking a moment", so they are two: the pose fires on every submission, the words stay gated to substantial lookups.

## Day 30/30

This is the last day of the 30 Days / 30 Projects challenge. The project started as a plain SQLite schema and grew one feature at a time -- CLI, then Qt UI, then search, then projects and links, then settings, then the mascot, then a packaged .exe. Every layer added a clear, testable boundary (a `core/` module, a `ui/` widget, or a one-line config in `requirements.txt`), which made the codebase relatively painless to extend incrementally.
