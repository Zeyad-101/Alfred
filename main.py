"""Application entry point -- boots the DB, builds the main window, and
wires the global hotkey.

This is the script the user actually runs to start Alfred. The Qt
event loop, the database connection, the tray icon, and the global
hotkey all come together here.

Hotkey architecture
-------------------
The global hotkey is registered with the third-party ``keyboard``
library, which on Windows uses a low-level keyboard hook. That
hook callback runs on its own thread, NOT the Qt main thread.
Qt widgets are strictly main-thread-only -- touching one from a
worker thread is undefined behavior. So the bridge object emits
a ``QObject.Signal`` from the worker thread; the connection is
``Qt.QueuedConnection`` so the slot runs on the main thread,
where it's safe to show the popup and call other widget methods.

The ``ui.hotkey.HotkeyController`` owns the actual library hook
and exposes a runtime-swap ``register()`` method. The Settings
dialog uses the same controller to change the hotkey at runtime
without restarting the app.

If the ``keyboard`` library isn't installed, or the OS denies the
hotkey (admin needed, no GUI session, etc.), the controller falls
back to ``pynput``; if that also fails, we log a warning and
continue: the app is still fully usable via the tray menu, the
toolbar button, and the regular window controls -- just without
the system-wide keyboard shortcut.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

from core.db import DEFAULT_DB_PATH, get_connection, init_db
from core.paths import default_db_path
from core.settings import load_settings
from ui.app_icon import app_icon
from ui.desktop_companion import DesktopCompanion
from ui.hotkey import HotkeyController, _HotkeyBridge
from ui.main_window import MainWindow
from ui.strings import APP_TITLE
from ui.theme import apply_theme

# Debounce window for the global hotkey. The keyboard library fires
# on every key-down event, including auto-repeats while the key is
# held. Without a debounce, holding Ctrl+Space would re-trigger the
# popup many times per second. 300ms is short enough that a normal
# user re-press feels instant, long enough that the OS auto-repeat
# is filtered out.
_HOTKEY_DEBOUNCE_MS = 300

# Windows groups taskbar buttons by "Application User Model ID" and
# uses that ID to decide which icon the group wears. A Python process
# that never sets one inherits the host interpreter's identity, which
# is why a source run shows the Python feather on the taskbar however
# many times ``setWindowIcon`` is called. Setting an explicit ID gives
# Alfred his own group and his own icon. Reverse-DNS-ish by
# convention; the string only has to be stable, not registered.
_APP_USER_MODEL_ID = "Alfred.PersonalAssistant"


def _claim_windows_taskbar_identity() -> None:
    """Tell Windows this process is Alfred, not a Python host.

    Best-effort and Windows-only: on any other platform the call
    does not exist, and if it fails the only consequence is a
    less-correct taskbar icon, which is not worth aborting a
    launch over.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            _APP_USER_MODEL_ID
        )
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    """Application entry point. Returns the Qt exit code."""
    argv = list(sys.argv if argv is None else argv)
    # Before the QApplication: Windows reads the app ID when the
    # first top-level window is created, so a late call has no
    # effect on the taskbar grouping.
    _claim_windows_taskbar_identity()
    # High-DPI pixmaps are on by default in Qt6, but explicit is
    # better than implicit -- keeps the tray icon crisp on 4K screens.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(argv)
    app.setApplicationName(APP_TITLE)
    # One icon for the whole process: the taskbar button, the
    # Alt-Tab card, and every dialog that does not set its own
    # inherit this. Previously nothing set it at all, so all three
    # showed the generic Qt/Python icon while the tray showed a
    # hand-painted blue "A" and the .exe showed the mascot.
    app.setWindowIcon(app_icon())
    # We DO NOT call ``app.setQuitOnLastWindowClosed(True)`` -- when
    # the user closes the main window we hide to the tray, and we
    # want the process to keep running. The tray's "Exit Alfred"
    # menu item calls QApplication.quit() explicitly.
    app.setQuitOnLastWindowClosed(False)

    # Load settings from data/settings.json (creating the file on
    # first run). The settings dict is then passed into MainWindow,
    # which owns the working copy and saves back on dialog Save.
    settings = load_settings()

    # Apply the dark theme before any widgets are built so the
    # first paint is already in the right palette. There is no
    # light alternative -- Alfred is dark-mode only.
    apply_theme()

    # Open the database. The path comes from
    # ``core.paths.default_db_path`` -- ``<project>/data/alfred.db``
    # in dev, ``%APPDATA%\\Alfred\\alfred.db`` in a packaged
    # install. The Settings dialog can override it for the NEXT
    # launch (a path change requires a restart -- the live
    # connection can't be rebound to a different file under
    # SQLite).
    conn = get_connection(default_db_path())
    # Make sure the schema exists. ``init_db`` is idempotent (every
    # CREATE statement uses IF NOT EXISTS) so it's safe to call on
    # every launch -- first run creates the tables, subsequent runs
    # are a no-op. Skipping this step (as the test suite gets away
    # with, because its ``conn`` fixture calls it explicitly) would
    # crash a fresh install with ``no such table: memories`` the
    # moment ``MainWindow`` calls ``list_memories``.
    init_db(conn)

    # Build the hotkey controller and bridge. The bridge lives on
    # the main thread (its QObject is created here); the controller
    # calls into the ``keyboard`` / ``pynput`` library from the
    # bridge's handle_press callback, which bounces through a
    # Signal so the slot runs on the main thread.
    bridge = _HotkeyBridge()

    def _hotkey_unavailable() -> None:
        # Surface the failure in the status bar so the user has a
        # visible reason for the missing shortcut, rather than just
        # a console message they may not see.
        try:
            window.statusBar().showMessage(
                "Global hotkey unavailable. Use the tray menu.",
                5000,
            )
        except Exception:
            pass

    # The main-thread handler for the bridge's triggered signal.
    # It calls the window's hotkey handler and then schedules the
    # rearm so the next press is accepted after the debounce window.
    def _on_triggered() -> None:
        try:
            window._on_hotkey_pressed()
        finally:
            # Rearm after the debounce. QTimer.singleShot creates
            # the timer in the calling thread (main) so it fires
            # there. Doing this in a finally means a buggy
            # handler can't wedge the bridge.
            QTimer.singleShot(_HOTKEY_DEBOUNCE_MS, bridge.rearm)

    bridge.triggered.connect(_on_triggered)

    controller = HotkeyController(bridge)
    controller.register(
        settings.get("hotkey", "ctrl+space"),
        on_unavailable=_hotkey_unavailable,
    )

    # Build the main window AFTER the controller exists, so it can
    # be passed in (the Settings dialog uses it to swap hotkeys at
    # runtime).
    window = MainWindow(
        conn, settings=settings, hotkey_controller=controller
    )
    window.show()

    # Floating desktop companion -- where the mascot lives,
    # rather than in the main window. It is a top-level QWidget
    # independent of ``window``: closing the main window
    # (close-to-tray) does
    # not affect it, and the companion only tears down when the
    # user picks "Exit Alfred" from the tray.
    #
    # The companion's signals hook into the main window's
    # existing show/restore and capture-popup paths so we
    # don't duplicate that logic. Double-clicking the mascot
    # goes to ``open_dashboard`` rather than the tray's plain
    # ``_show_main_window`` restore: asking Alfred to come out
    # is asking him "what have we got?", so he lands on the
    # Dashboard every time instead of on whichever view the
    # window happened to be left on. The tray's "Open Alfred"
    # keeps the plain restore -- that one is "put the window
    # back", not "start over".
    companion = DesktopCompanion(settings=settings, parent=None)
    companion.capture_popup_requested.connect(window.show_capture_popup_near)
    companion.main_window_requested.connect(window.open_dashboard)
    # Every route to the popup now speaks out of the sprite, not just a
    # click on him: the hotkey used to drop the bubble in the middle of
    # the screen while Alfred stood off to one side. A callable, not a
    # stored point, because the user drags him around.
    window.capture_popup.set_anchor_provider(companion.bubble_anchor)

    # State-machine wiring. The companion exposes
    # ``play_idle`` / ``play_thinking`` / ``play_greeting`` so
    # the rest of the app can give Alfred a visible cue
    # without poking at the private pet widget. We wire the
    # two most common feedback points:
    #
    # * Hotkey press -> greeting. When the user invokes the
    #   global hotkey (Ctrl+Space) Alfred greets them, the
    #   same gesture as a single-click on the companion.
    # * Editor save -> greeting. A successful save is the
    #   most common "I did the thing" moment; greeting
    #   confirms it without flooding the visual channel.
    #
    # Operations that are expected to take longer (search,
    # refresh) get the thinking pose. We wrap the existing
    # ``_run_search`` so the companion briefly switches to
    # thinking while the list refreshes, then goes back to
    # idle. Saves are too quick to be worth a state change.
    def _on_hotkey_with_greeting() -> None:
        try:
            window._on_hotkey_pressed()
        finally:
            companion.play_greeting()

    bridge.triggered.disconnect(_on_triggered)
    bridge.triggered.connect(_on_hotkey_with_greeting)

    def _on_save_with_greeting(data: dict) -> None:
        try:
            window._on_editor_save(data)
        finally:
            companion.play_greeting()

    # ``editor.save_clicked`` carries the editor's data dict.
    # The original connection (set up inside MainWindow's
    # __init__) used ``self._on_editor_save`` directly; we
    # replace it with our wrapper. disconnect() raises if the
    # connection was never made, so we swallow that and fall
    # through to connect.
    try:
        window.editor.save_clicked.disconnect(window._on_editor_save)
    except (RuntimeError, TypeError):
        pass
    window.editor.save_clicked.connect(_on_save_with_greeting)

    def _on_search_with_thinking() -> None:
        # No pose change here. ``_run_search`` -> ``_refresh_list`` is
        # synchronous on the main thread, so a thinking -> idle pair
        # around it could never paint a single thinking frame; all it
        # actually did was re-enter the idle animation, which used to
        # drag the sprite back to frame 0 on every keystroke of a
        # search. Poses that need to be *seen* are held with
        # ``force_pose`` by the butler layer below.
        window._run_search()

    # The original textChanged wiring inside MainWindow routes
    # through a debounce ``search_timer`` (see
    # ``MainWindow._run_search``). We wrap the timer-driven
    # ``_run_search`` so the pet gets the thinking pose for
    # the actual search, not for every keystroke. The
    # companion's frame loop (~1.3 s for thinking) is longer
    # than any reasonable debounce, so this stays clean.
    try:
        window.search_timer.timeout.disconnect(window._run_search)
    except (RuntimeError, TypeError):
        pass
    window.search_timer.timeout.connect(_on_search_with_thinking)

    # Butler interaction layer. An exchange holds the
    # thinking pose from the moment it is submitted until its answer is
    # revealed -- ``force_pose`` pins the existing thinking frames so the
    # idle cycle can't wander off mid-question, and ``release_pose``
    # hands control back. The controller's minimum-visibility hold is
    # what makes this pose readable rather than a single-frame flicker.
    # No new art and no new states: these are the same three poses the
    # companion has always had.
    #
    # The pose is released on three separate paths because a lookup
    # can end in three ways: an answer arrives, the user hits Escape
    # while it's still running, or the popup closes for any other
    # reason. Without the ``finished`` connection, an Escape mid-lookup
    # would leave Alfred stuck mid-think forever.
    def _on_assistant_thinking() -> None:
        try:
            companion.force_pose("thinking")
        except Exception:
            pass

    def _on_assistant_done(*_args: object) -> None:
        try:
            companion.release_pose()
        except Exception:
            pass

    # The pose follows every exchange, not only the substantial ones:
    # ``pose_thinking_started`` fires on submission whatever the weight,
    # and ``pose_released`` when Alfred has finished saying his line. The
    # release is queued on the sprite's own ``cycle_completed`` inside
    # ``release_pose``, so a fast answer still gets a whole visible lap of
    # the thinking animation instead of a three-frame twitch.
    window.capture_popup.pose_thinking_started.connect(_on_assistant_thinking)
    window.capture_popup.pose_released.connect(_on_assistant_done)
    # ``finished`` is the backstop: Escape mid-lookup, or a close on any
    # other path, must not leave him stuck mid-think forever.
    window.capture_popup.finished.connect(_on_assistant_done)
    # Show the companion and play the one-shot greeting. This
    # is independent of the main window -- if the user has
    # already closed the main window to the tray by some
    # future code path, the companion still appears.
    companion.start()
    # Save the final position on app quit. The companion also
    # saves on every drag-end, so this is just a
    # belt-and-suspenders for the very last move. We also
    # persist the whole settings dict here so the
    # companion's position lands on disk even if the user
    # drags it around but never opens the Settings dialog.
    def _on_about_to_quit() -> None:
        try:
            companion.shutdown()
        except Exception:
            pass
        try:
            from core.settings import save_settings
            save_settings(settings)
        except OSError:
            # The companion's final position is lost if the
            # settings file can't be written, but the user
            # explicitly chose Exit Alfred -- losing the
            # last-dragged position is much less surprising
            # than blocking exit.
            pass

    app.aboutToQuit.connect(_on_about_to_quit)

    # Make sure keyboard's listener threads don't block process
    # shutdown. The ``atexit`` shim below is a belt-and-suspenders
    # cleanup; in normal use Qt's quit handler should suffice.
    try:
        import atexit

        def _cleanup() -> None:
            try:
                controller.unregister()
            except Exception:
                pass

        atexit.register(_cleanup)
    except Exception:
        pass

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
