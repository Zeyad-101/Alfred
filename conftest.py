"""Shared pytest fixtures.

Lives at the project root so pytest treats this directory as the rootdir
and adds it to sys.path, letting the test files do `from core...` imports.
"""
import pytest

from core.db import get_connection, init_db


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "alfred_test.db"
    c = get_connection(str(db_path))
    init_db(c)
    yield c
    c.close()


@pytest.fixture(scope="session")
def qapp():
    """A single QApplication for the whole test session.

    Qt only allows one QApplication per process; creating more than
    one raises. Tests that touch widgets (the capture popup, the
    editor, etc.) all share this instance. We use ``argv=[]`` so
    pytest's own argv doesn't get parsed as Qt flags.
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
    # Don't quit the app between tests — other tests in the same
    # session still need it. Pytest will tear it down on exit.
