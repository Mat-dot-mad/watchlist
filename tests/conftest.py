"""Shared test setup.

pytest loads this file automatically. Anything defined here with the
@pytest.fixture decorator becomes available to every test file: a test
just names the fixture as an argument and pytest builds it on demand,
fresh for each test.
"""
import os
import sys

import pytest

# Make the project importable regardless of where pytest is run from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402


@pytest.fixture
def temp_db(tmp_path):
    """An empty, initialised database, thrown away after each test.

    `tmp_path` is built into pytest: a fresh temporary directory per test.
    Using it means tests never touch the real watchlist.db.

    Returns the path as a string, to pass as db_path=... to db functions.
    """
    path = str(tmp_path / "test.db")
    db.init_db(path)
    return path


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A fake browser for making requests to the app without a real server.

    db.py reads DATABASE_PATH once at import time, and create_app() calls
    db.init_db() with no argument — so pointing the app at a test database
    means overriding those module-level values. `monkeypatch` is pytest's
    tool for that: it undoes the change automatically when the test ends.
    """
    monkeypatch.setattr(db, "DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(db, "TICKERS_FILE", str(tmp_path / "tickers.txt"))
    # create_app() seeds an empty database from tickers.txt, falling back to
    # a *relative* path. Run from the temp directory so tests start empty
    # instead of loading the repo's real 38-ticker list.
    monkeypatch.chdir(tmp_path)
    # No password set => the login wall is skipped, which is what most
    # tests want. test_login_required sets one explicitly to test the wall.
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)

    from app import create_app
    app = create_app()
    app.config["TESTING"] = True  # surfaces errors instead of hiding them
    return app.test_client()
