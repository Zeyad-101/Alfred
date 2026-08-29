"""Resolve on-disk locations for Alfred's data and bundled assets.

Alfred has two distinct kinds of "where do files live?":

1. **Writable per-user data** -- the SQLite database,
   ``settings.json``, and the backups directory. In a packaged
   install (a PyInstaller-built ``Alfred.exe``) these live in a
   per-user application-data directory (``%APPDATA%\\Alfred`` on
   Windows, ``~/Library/Application Support/Alfred`` on macOS,
   ``$XDG_DATA_HOME/Alfred`` on Linux) so they survive
   reinstalls, are writable even when the .exe lives under
   ``Program Files``, and don't pollute the directory the user
   double-clicked the .exe from. In dev mode (``python main.py``
   from the project root, or the test suite) they live under
   ``<project>/data/`` so dev files stay next to the source.

2. **Read-only bundled assets** -- the sprite PNGs, any future
   stylesheet files. In dev mode they live under
   ``<project>/assets/``; in a PyInstaller bundle PyInstaller
   unpacks them under ``sys._MEIPASS`` and we look them up there.

The split is detected at runtime: ``sys.frozen`` (set by
PyInstaller at launch) marks packaged mode, and ``sys._MEIPASS``
points at the unpacked bundle directory.

The directory returned by :func:`user_data_dir` is NOT created
here -- callers should call ``Path.mkdir(parents=True,
exist_ok=True)`` before writing.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "Alfred"

# The dev-mode data directory is named "data" so a developer
# running ``python main.py`` gets a predictable location
# (``./data/alfred.db``) and the conftest fixtures can keep
# using ``tmp_path``-based fresh dbs without colliding.
_DEV_DATA_SUBDIR = "data"


def is_frozen() -> bool:
    """True when running inside a PyInstaller bundle.

    PyInstaller sets ``sys.frozen = True`` and ``sys._MEIPASS``
    to the bundle's extraction directory at launch. Either is
    sufficient; we check both because some PyInstaller hooks
    set one before the other depending on the platform.
    """
    return bool(getattr(sys, "frozen", False)) or bool(
        getattr(sys, "_MEIPASS", None)
    )


def user_data_dir() -> Path:
    """Return the per-user writable directory for Alfred's data.

    Resolution order:

    * If frozen (packaged ``.exe``): use the platform-appropriate
      per-user app-data directory -- ``%APPDATA%\\Alfred`` on
      Windows, ``~/Library/Application Support/Alfred`` on
      macOS, ``$XDG_DATA_HOME/Alfred`` (or
      ``~/.local/share/Alfred``) on Linux.
    * If not frozen (dev or test): use
      ``<project_root>/data/`` so dev runs leave files next to
      the source rather than in a hidden user directory.

    The directory is NOT created here. Callers must
    ``mkdir(parents=True, exist_ok=True)`` before writing.
    """
    if is_frozen():
        if sys.platform == "win32":
            appdata = os.environ.get("APPDATA")
            if appdata:
                return Path(appdata) / APP_NAME
            # No APPDATA on Windows is unusual; fall back to
            # the user's home so we still have a writable spot.
            return Path.home() / APP_NAME
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Application Support" / APP_NAME
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            return Path(xdg) / APP_NAME
        return Path.home() / ".local" / "share" / APP_NAME
    # Dev mode: <project_root>/data. The path is computed from
    # this file's location (core/paths.py -> <project_root>),
    # not the current working directory, so it doesn't matter
    # which directory the user launched ``python main.py`` from.
    return Path(__file__).resolve().parent.parent / _DEV_DATA_SUBDIR


def default_db_path() -> str:
    """Default on-disk path for the SQLite database.

    Resolves to ``<user_data_dir>/alfred.db``. In dev mode that
    is ``<project>/data/alfred.db``; in a packaged install it
    is ``%APPDATA%\\Alfred\\alfred.db``.
    """
    return str(user_data_dir() / "alfred.db")


def default_settings_path() -> str:
    """Default on-disk path for the settings JSON file.

    Resolves to ``<user_data_dir>/settings.json``.
    """
    return str(user_data_dir() / "settings.json")


def default_backup_dir() -> str:
    """Default directory for database backups.

    Resolves to ``<user_data_dir>/backups``.
    """
    return str(user_data_dir() / "backups")


def asset_path(rel: str) -> Path:
    """Resolve a path to a read-only asset bundled with the app.

    In a PyInstaller bundle, returns the path inside
    ``sys._MEIPASS`` (the temp directory PyInstaller unpacks
    data into at launch). In dev mode, returns the path under
    the project root.

    ``rel`` is a path relative to the project root, e.g.
    ``"assets/alfred/frames/idle/idle_00.png"``.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / rel
    return Path(__file__).resolve().parent.parent / rel
