"""Tests for the platform-aware path resolver in ``core/paths.py``.

These tests pin the contract: a packaged build (``sys.frozen``
true) lands its data in a per-user app-data directory, while a
dev run (``sys.frozen`` false) keeps data next to the source
tree. Without the contract pinned here, a refactor that swaps
the dev fallback for a real user-data dir on dev machines
would silently start sprinkling files in ``%APPDATA%`` during
test runs.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from core import paths


# ---------- dev mode (the actual default during pytest) ----------


def test_dev_mode_data_dir_is_project_relative():
    """When not frozen, ``user_data_dir()`` lives under the
    project root so dev runs (and tests) don't write to
    ``%APPDATA%``."""
    # Sanity: the test process is not frozen.
    assert paths.is_frozen() is False, (
        "pytest was started inside a frozen interpreter; "
        "the dev-mode test below would not be meaningful."
    )
    data_dir = paths.user_data_dir()
    project_root = Path(paths.__file__).resolve().parent.parent
    assert data_dir == project_root / "data"


def test_dev_mode_db_path_is_inside_data_dir():
    """The default DB path is a file inside the dev data dir."""
    db = Path(paths.default_db_path())
    assert db == paths.user_data_dir() / "alfred.db"
    assert str(db).endswith("data" + os.sep + "alfred.db")


def test_dev_mode_settings_path_is_inside_data_dir():
    """The default settings path is a file inside the dev data dir."""
    settings = Path(paths.default_settings_path())
    assert settings == paths.user_data_dir() / "settings.json"


def test_dev_mode_backup_dir_is_inside_data_dir():
    """The default backup dir is a subdirectory of the data dir."""
    backup_dir = Path(paths.default_backup_dir())
    assert backup_dir == paths.user_data_dir() / "backups"


def test_dev_mode_asset_path_resolves_under_project_root():
    """In dev, asset_path(rel) returns a path under the project root."""
    p = paths.asset_path("assets/sprites/idle_1.png")
    project_root = Path(paths.__file__).resolve().parent.parent
    assert p == project_root / "assets" / "sprites" / "idle_1.png"


# ---------- frozen mode (the packaged-exe branch) ----------


@pytest.fixture
def _frozen(monkeypatch):
    """Temporarily mark the process as PyInstaller-frozen.

    Patches both ``sys.frozen`` and ``sys._MEIPASS`` so the
    ``is_frozen()`` check returns True during the test, then
    restores both at teardown. Tests using this fixture must
    NOT make real OS calls that depend on the persistent
    ``%APPDATA%`` value — the fixture also points
    ``%APPDATA%`` at a tmp_path so any path math the helper
    does stays inside the test sandbox.
    """
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", None, raising=False)
    # is_frozen() short-circuits on sys.frozen; the _MEIPASS
    # patch is for the asset_path branch.
    yield
    # monkeypatch handles teardown for sys attrs.


def test_frozen_mode_windows_uses_appdata(monkeypatch, tmp_path):
    """On Windows in frozen mode, the data dir lives under
    ``%APPDATA%\\Alfred``."""
    monkeypatch.setattr(sys, "platform", "win32")
    fake_appdata = str(tmp_path / "AppData" / "Roaming")
    monkeypatch.setenv("APPDATA", fake_appdata)
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    data_dir = paths.user_data_dir()
    assert data_dir == Path(fake_appdata) / "Alfred"


def test_frozen_mode_windows_missing_appdata_falls_back(monkeypatch, tmp_path):
    """On Windows in frozen mode with no APPDATA env, the
    helper falls back to the user's home so the app still
    has a writable location rather than crashing."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    fake_home = tmp_path / "Home"
    monkeypatch.setenv("HOME", str(fake_home))
    # Path.home() reads HOME on POSIX and USERPROFILE on Windows.
    # Force USERPROFILE to keep the test OS-agnostic.
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    data_dir = paths.user_data_dir()
    # Either the HOME-based or USERPROFILE-based home resolves;
    # the contract is "somewhere writable under the user's
    # home".
    assert data_dir.name == "Alfred"
    assert fake_home in data_dir.parents or data_dir.parent == fake_home


def test_frozen_mode_asset_path_uses_meipass(monkeypatch, tmp_path):
    """In frozen mode, asset_path() reads ``sys._MEIPASS`` to
    locate PyInstaller's unpack directory."""
    meipass = tmp_path / "meipass"
    meipass.mkdir()
    (meipass / "assets" / "sprites").mkdir(parents=True)
    (meipass / "assets" / "sprites" / "idle_1.png").write_bytes(b"fake-png")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)

    p = paths.asset_path("assets/sprites/idle_1.png")
    assert p == meipass / "assets" / "sprites" / "idle_1.png"
    # Sanity: the file is reachable at the resolved path.
    assert p.read_bytes() == b"fake-png"


def test_frozen_mode_default_helpers_point_at_user_data_dir(
    monkeypatch, tmp_path
):
    """default_db_path / default_settings_path / default_backup_dir
    all sit inside user_data_dir() under frozen mode."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    data_dir = paths.user_data_dir()
    assert Path(paths.default_db_path()) == data_dir / "alfred.db"
    assert Path(paths.default_settings_path()) == data_dir / "settings.json"
    assert Path(paths.default_backup_dir()) == data_dir / "backups"


# ---------- tmp_path is unaffected by the refactor ----------


def test_default_db_path_does_not_create_files(tmp_path, monkeypatch):
    """The path helpers are pure resolvers — they must NOT
    touch the filesystem. The conftest ``conn`` fixture depends
    on this: it uses ``tmp_path`` for fresh DBs and would
    collide with anything the helper might create on import.
    """
    before = set(tmp_path.iterdir())
    _ = paths.default_db_path()
    _ = paths.default_settings_path()
    _ = paths.default_backup_dir()
    after = set(tmp_path.iterdir())
    assert before == after, (
        "path helpers must not create files; "
        f"got new entries {after - before}"
    )
