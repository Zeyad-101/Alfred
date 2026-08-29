"""Application settings persistence.

Settings live in a flat JSON file (default: ``data/settings.json``
in dev mode, ``%APPDATA%\\Alfred\\settings.json`` in a packaged
install -- see ``core.paths.default_settings_path``) as a dict.
The set of known keys is enumerated by :data:`DEFAULT_SETTINGS`;
any key in the file that isn't a known key is silently dropped
on load, and any missing key falls back to its default. This
makes the file forward-compatible: a future version of Alfred
can add a new setting and old settings files load cleanly with
the new key at its default value.

A corrupt or unreadable file is treated as "no settings" and the
app falls back to defaults rather than crashing. The user's
intuition on first launch is "the app just works" -- losing a
single setting because of a one-byte JSON typo would be much
worse UX than quietly using the default for that one key.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from core.paths import default_backup_dir, default_db_path


DEFAULT_SETTINGS: dict[str, Any] = {
    "hotkey": "ctrl+space",
    # Resolved at import time via the platform-aware path
    # helper: ``<project>/data/alfred.db`` in dev, the user's
    # per-user app-data dir in a packaged build.
    "db_path": default_db_path(),
    "backup_dir": default_backup_dir(),
    "notifications_enabled": True,
    "reminders_enabled": True,
    "reminder_interval_minutes": 60,
    # Position of the floating desktop companion window as
    # ``[x, y]`` in screen pixels (top-left origin). The
    # companion persists this whenever the user drags the
    # window, and restores from it on next launch. The default
    # of ``[100, 100]`` is a corner placement that the
    # companion will itself overwrite on first run (it lands
    # near the bottom-right of the primary screen), so first
    # launch typically produces a value like ``[1500, 800]``
    # rather than the literal default.
    "companion_position": [100, 100],
    # Keyword -> project auto-linking rules, as
    # ``{project_name: [keyword, ...]}``. When a capture's text
    # contains one of a project's keywords (plain case-insensitive
    # substring, no fuzzy or semantic matching), the new memory is
    # linked to that project, creating it if it does not exist yet.
    # Empty by default, which makes the whole feature a no-op until
    # the user configures a group in Settings -> Topics. An explicit
    # "... to the X project" phrase in the capture always wins over
    # a keyword match.
    "topic_keywords": {},
}

# Recognized keys, derived from DEFAULT_SETTINGS. Used to filter
# incoming settings on load so a file with stale or bogus keys
# (e.g. a typo, or a key from an older version that has since
# been removed) doesn't pollute the in-memory settings dict.
_RECOGNIZED_KEYS: frozenset[str] = frozenset(DEFAULT_SETTINGS.keys())


# Basic shape for a hotkey: two-or-more word tokens separated by
# ``+``. We deliberately don't try to validate that every token
# is a real key name -- that's the underlying library's job, and
# keeping the regex small means future key aliases (``super``,
# ``meta``, etc.) Just Work without us updating the validator.
# What we DO catch: empty strings, single tokens (no ``+``),
# trailing or leading ``+``, and other obvious typos.
_HOTKEY_RE = re.compile(r"^[a-zA-Z0-9]+(\+[a-zA-Z0-9]+)+$")


def validate_hotkey(s: str) -> bool:
    """Return True iff ``s`` is a structurally plausible hotkey string.

    Accepts ``"ctrl+space"``, ``"ctrl+shift+a"``, ``"alt+1"``, etc.
    Rejects empty strings, bare modifiers (``"ctrl"``), trailing
    ``+``, and anything that isn't letters/digits separated by
    single ``+`` characters.
    """
    if not isinstance(s, str):
        return False
    s = s.strip()
    if not s:
        return False
    return bool(_HOTKEY_RE.match(s))


def load_settings(path: Optional[str] = None) -> dict[str, Any]:
    """Load settings from ``path``, merged with :data:`DEFAULT_SETTINGS`.

    If ``path`` is None (the default), the platform-aware
    settings path from :func:`core.paths.default_settings_path`
    is used -- ``<project>/data/settings.json`` in dev mode,
    ``%APPDATA%\\Alfred\\settings.json`` in a packaged install.

    Behavior:

    * If the file does not exist, it is created with the current
      defaults (so the first launch leaves a file on disk for the
      user to inspect) and the defaults are returned.
    * If the file is unreadable or contains invalid JSON, the
      defaults are returned and the file is left untouched --
      a corrupt file should not brick the app.
    * If the file is valid JSON but is not a top-level object,
      the defaults are returned.
    * For any recognized key whose value is missing from the file,
      the default value is used (forward-compat: future versions
      can add keys without breaking old files).
    * Keys in the file that aren't in :data:`DEFAULT_SETTINGS` are
      silently ignored (defensive: stale keys from a removed
      setting shouldn't crash load).
    """
    if path is None:
        # Import inside the function so the test suite can
        # monkeypatch sys.frozen / APPDATA before the helper is
        # resolved, if it ever needs to.
        from core.paths import default_settings_path
        path = default_settings_path()

    if not os.path.exists(path):
        # First-run / fresh install. Persist defaults so the user
        # can see what Alfred thinks the settings are.
        save_settings(DEFAULT_SETTINGS, path)
        return dict(DEFAULT_SETTINGS)

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_SETTINGS)

    if not isinstance(data, dict):
        return dict(DEFAULT_SETTINGS)

    merged: dict[str, Any] = dict(DEFAULT_SETTINGS)
    for key, value in data.items():
        if key in _RECOGNIZED_KEYS:
            merged[key] = value
    return merged


def save_settings(
    settings: dict[str, Any], path: Optional[str] = None
) -> None:
    """Write ``settings`` to ``path`` as pretty-printed JSON.

    If ``path`` is None, the platform-aware settings path from
    :func:`core.paths.default_settings_path` is used.

    Parent directories are created on demand. The full set of
    known keys is written (not just the ones the caller passed),
    so the file is always a complete snapshot of the current
    settings -- easier to diff, easier to read, easier to hand-edit.
    """
    if path is None:
        from core.paths import default_settings_path
        path = default_settings_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    # Normalize to the recognized keys only. If a caller passes
    # extra keys, drop them silently rather than writing noise to
    # disk that won't be re-read.
    to_write: dict[str, Any] = {
        k: settings[k] for k in DEFAULT_SETTINGS if k in settings
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(to_write, f, indent=2, ensure_ascii=False)
