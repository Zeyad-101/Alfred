"""Global hotkey registration for Alfred.

Encapsulates the ``keyboard``-library-first / ``pynput``-fallback
dance, and provides a :class:`HotkeyController` that can swap
hotkeys at runtime without restarting the app. The Settings
dialog's Hotkey page uses this directly to apply changes the
moment the user clicks Save.

The controller holds a reference to a :class:`_HotkeyBridge`
(``QObject``) that bounces a worker-thread press into the Qt
main thread via a ``Signal``. The actual hotkey registration
lives on the worker thread (the ``keyboard`` library's hook, or
``pynput``'s listener); the bridge is what makes the Qt side
happy.

Choosing a hotkey string
------------------------
The hotkey string format is ``modifier+modifier+key``, e.g.
``"ctrl+space"``, ``"ctrl+shift+a"``, ``"alt+1"``. Tokens are
case-insensitive. We don't enforce a canonical order -- the
underlying libraries handle the parsed form, and re-ordering
``ctrl+shift+a`` vs ``shift+ctrl+a`` is fine.

The fallback path (pynput) only knows how to listen for
modifier+target combos where the target is a recognizable
single key (letter, digit, space, tab, escape, enter,
backspace, arrow). For exotic targets (e.g. media keys), the
``keyboard`` library is the only option; the controller will
log a warning and report failure if both libraries can't
service the request.
"""
from __future__ import annotations

import sys
import threading
import traceback
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal


# ---------- bridge ----------


class _HotkeyBridge(QObject):
    """Thread-safe bridge between a keyboard hook and the Qt main thread.

    The underlying library (keyboard or pynput) invokes its callback
    on a worker thread, not the Qt main thread. Qt widgets are
    strictly main-thread-only, so we bounce the press into the
    main thread via a ``Signal``. The default cross-thread
    connection is a queued one, so the connected slot runs on the
    main thread.

    Debounce
    --------
    The keyboard library fires on every key-down event, including
    the OS's auto-repeat while a key is held. Without a debounce,
    holding the hotkey would re-trigger many times per second.
    The lock-protected ``_armed`` flag gates emissions: a press
    is dropped if the bridge isn't armed. The consumer (the
    handler connected to ``triggered``) is responsible for calling
    :meth:`rearm` after a debounce delay. This keeps the debounce
    policy out of the bridge -- callers can tune the delay without
    touching this class.
    """

    triggered = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._armed = True

    def handle_press(self) -> None:
        """Callback passed to the underlying library.

        Runs in the library's hook thread. Acquires the lock,
        checks the armed flag, and emits ``triggered`` exactly
        once per debounce window. The rearm is the consumer's
        responsibility.
        """
        with self._lock:
            if not self._armed:
                return
            self._armed = False
        self.triggered.emit()

    def rearm(self) -> None:
        """Mark the bridge as ready to fire again. Safe from any thread."""
        with self._lock:
            self._armed = True


# ---------- pynput parser ----------


def _parse_hotkey_for_pynput(
    hotkey: str,
) -> Optional[tuple[set, object, object]]:
    """Parse ``hotkey`` into ``(modifier_keys, target_key, repr)``.

    Returns ``None`` if the hotkey string isn't a modifier+key
    combo that pynput can listen for. The third return value is
    a small ``repr``-ish summary of the combo, useful for the
    "what did I just register?" status bar message.
    """
    try:
        from pynput import keyboard as pynput_keyboard
    except Exception:
        return None

    parts = [p.strip() for p in hotkey.lower().split("+") if p.strip()]
    if len(parts) < 2:
        return None

    modifier_strs = parts[:-1]
    target_str = parts[-1]

    mod_map = {
        "ctrl": (
            pynput_keyboard.Key.ctrl,
            pynput_keyboard.Key.ctrl_l,
            pynput_keyboard.Key.ctrl_r,
        ),
        "alt": (
            pynput_keyboard.Key.alt,
            pynput_keyboard.Key.alt_l,
            pynput_keyboard.Key.alt_r,
        ),
        "shift": (
            pynput_keyboard.Key.shift,
            pynput_keyboard.Key.shift_l,
            pynput_keyboard.Key.shift_r,
        ),
    }

    modifier_keys: set = set()
    for m in modifier_strs:
        if m not in mod_map:
            return None
        modifier_keys.update(mod_map[m])

    # Map the target. Most non-letter keys are spelled out as
    # pynput Key enum members; single letters/digits go through
    # ``KeyCode.from_char``.
    target_key: object
    named = {
        "space": pynput_keyboard.Key.space,
        "tab": pynput_keyboard.Key.tab,
        "esc": pynput_keyboard.Key.esc,
        "escape": pynput_keyboard.Key.esc,
        "enter": pynput_keyboard.Key.enter,
        "return": pynput_keyboard.Key.enter,
        "backspace": pynput_keyboard.Key.backspace,
        "up": pynput_keyboard.Key.up,
        "down": pynput_keyboard.Key.down,
        "left": pynput_keyboard.Key.left,
        "right": pynput_keyboard.Key.right,
    }
    if target_str in named:
        target_key = named[target_str]
    elif len(target_str) == 1 and target_str.isalnum():
        try:
            target_key = pynput_keyboard.KeyCode.from_char(target_str)
        except Exception:
            return None
    else:
        return None

    return modifier_keys, target_key, target_str


# ---------- controller ----------


class HotkeyController:
    """Manages a single global hotkey with runtime-swap support.

    The controller is paired with a :class:`_HotkeyBridge`. The
    bridge lives on the Qt main thread (so its ``Signal`` can
    safely be connected to a main-thread slot); the controller
    wires the underlying library's hook/listener to the bridge's
    ``handle_press`` method.

    Lifecycle::

        ctrl = HotkeyController(bridge)
        ctrl.register("ctrl+space")
        ...
        ctrl.register("ctrl+shift+space")  # unhooks the old, hooks the new
        ...
        ctrl.unregister()  # explicit teardown at app exit
    """

    def __init__(self, bridge: _HotkeyBridge) -> None:
        self._bridge = bridge
        # The cleanup hook is set by register() and called by
        # unregister(). It is a closure (rather than a method)
        # because the exact teardown depends on which library
        # is in use and what handle/identifier it returned.
        self._cleanup: Optional[Callable[[], None]] = None

    def register(
        self,
        hotkey: str,
        on_unavailable: Optional[Callable[[], None]] = None,
    ) -> bool:
        """Register ``hotkey``. Unregisters any prior hotkey first.

        Returns ``True`` on success. On total failure (both
        libraries refused), calls ``on_unavailable`` (if given)
        and returns ``False``.

        The caller is expected to have already connected
        ``bridge.triggered`` to whatever slot should fire on
        a press; this method just wires the library side of
        the bridge.
        """
        # Always unregister first. register() is the universal
        # "make this controller serve this hotkey right now" API;
        # re-registering with the same hotkey should be a clean
        # no-op rather than a leak.
        self.unregister()

        if _try_keyboard_lib(hotkey, self._bridge, self):
            return True
        if _try_pynput_lib(hotkey, self._bridge, self):
            return True

        # Both libraries failed. Notify the caller (e.g. so the
        # status bar can surface "Global hotkey unavailable").
        if on_unavailable is not None:
            try:
                on_unavailable()
            except Exception:
                pass
        return False

    def unregister(self) -> None:
        """Tear down the current hotkey registration, if any."""
        if self._cleanup is None:
            return
        cleanup = self._cleanup
        self._cleanup = None
        try:
            cleanup()
        except Exception:
            # Don't let a buggy cleanup callback keep the
            # controller wedged -- the next register() will
            # re-attempt fresh.
            pass

    # Internal -- used by the registration helpers to set
    # ``self._cleanup`` after a successful registration.
    def _set_cleanup(self, cleanup: Callable[[], None]) -> None:
        self._cleanup = cleanup


# ---------- library paths ----------


def _try_keyboard_lib(
    hotkey: str, bridge: _HotkeyBridge, controller: HotkeyController
) -> bool:
    """Try the ``keyboard`` library. Returns True on success.

    On success, sets the controller's cleanup callback to
    unhook the registered hotkey.
    """
    try:
        import keyboard  # type: ignore[import-not-found]
    except Exception as exc:
        print(
            f"[alfred] keyboard library not available ({exc!r}); "
            f"falling back to pynput.",
            file=sys.stderr,
        )
        return False
    try:
        handle = keyboard.add_hotkey(
            hotkey,
            bridge.handle_press,
            suppress=False,
        )
    except Exception as exc:
        print(
            f"[alfred] keyboard.add_hotkey failed ({exc!r}); "
            f"falling back to pynput.",
            file=sys.stderr,
        )
        return False
    # ``remove_hotkey`` is the inverse of ``add_hotkey``; the
    # handle it returned is the key. We use a closure that
    # captures the handle so the cleanup is exact, not "unhook
    # everything" (which would also nuke hotkeys other parts of
    # the app might have registered -- not a concern today, but
    # being precise is cheap).
    controller._set_cleanup(
        lambda h=handle: _safe_remove_keyboard_hotkey(h, hotkey)
    )
    return True


def _safe_remove_keyboard_hotkey(handle, hotkey: str) -> None:
    try:
        import keyboard  # type: ignore[import-not-found]
        try:
            keyboard.remove_hotkey(handle)
            return
        except Exception:
            # Some versions of the library raise if the handle
            # has already been removed; that's fine.
            pass
        # Fall back to removing by name. Slightly more
        # aggressive (removes ALL hotkeys matching this name,
        # not just ours) but it's the same name we registered,
        # and no other code path registers hotkeys in this app.
        try:
            keyboard.remove_hotkey(hotkey)
        except Exception:
            pass
    except Exception:
        pass


def _try_pynput_lib(
    hotkey: str, bridge: _HotkeyBridge, controller: HotkeyController
) -> bool:
    """Try the ``pynput`` library. Returns True on success.

    The pynput path has to listen for individual key events and
    decide when the configured combo is fully pressed. The
    parser only supports a small set of target keys (letters,
    digits, a few named keys), so the defaults "ctrl+space" /
    "ctrl+shift+a" / "alt+1" all work, but something exotic
    like a media key would fall through to "unavailable".
    """
    try:
        from pynput import keyboard as pynput_keyboard  # type: ignore[import-not-found]
    except Exception as exc:
        print(
            f"[alfred] pynput also unavailable ({exc!r}); "
            f"global hotkey disabled.",
            file=sys.stderr,
        )
        return False

    parsed = _parse_hotkey_for_pynput(hotkey)
    if parsed is None:
        print(
            f"[alfred] pynput cannot service hotkey {hotkey!r}; "
            f"global hotkey disabled.",
            file=sys.stderr,
        )
        return False
    modifier_keys, target_key, _target_repr = parsed

    # Track which modifier keys are currently held. The bridge
    # fires only when ALL expected modifiers are down and the
    # target key has just been pressed.
    pressed_mods: set = set()
    target_was_down: bool = False

    def _on_press(key) -> None:
        nonlocal target_was_down
        try:
            if key in modifier_keys:
                pressed_mods.add(key)
                return
            if key == target_key:
                # Avoid firing on key-repeat: only fire on a
                # fresh press (target wasn't down just before).
                if (
                    not target_was_down
                    and modifier_keys.issubset(pressed_mods)
                ):
                    bridge.handle_press()
                target_was_down = True
        except Exception:
            traceback.print_exc()

    def _on_release(key) -> None:
        nonlocal target_was_down
        try:
            if key in modifier_keys:
                pressed_mods.discard(key)
            if key == target_key:
                target_was_down = False
        except Exception:
            pass

    try:
        listener = pynput_keyboard.Listener(
            on_press=_on_press, on_release=_on_release
        )
        listener.daemon = True
        listener.start()
    except Exception as exc:
        print(
            f"[alfred] pynput listener failed to start ({exc!r}); "
            f"global hotkey disabled.",
            file=sys.stderr,
        )
        return False

    controller._set_cleanup(listener.stop)
    return True
