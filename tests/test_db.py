"""Tests for db.py — the storage layer.

Every test follows the same three-part shape:
  1. set up a scenario,
  2. do the thing being tested,
  3. assert what should now be true.

`temp_db` is the fixture from conftest.py: a fresh empty database that
pytest builds before the test and discards after.
"""
import db


# ── Ticker list ──────────────────────────────────────────────────────

def test_add_and_list_tickers(temp_db):
    db.add_ticker("AAPL", temp_db)
    db.add_ticker("MSFT", temp_db)

    # list_tickers sorts alphabetically
    assert db.list_tickers(temp_db) == ["AAPL", "MSFT"]


def test_tickers_are_normalised_to_uppercase(temp_db):
    db.add_ticker("  aapl  ", temp_db)

    assert db.list_tickers(temp_db) == ["AAPL"]


def test_adding_the_same_ticker_twice_is_ignored(temp_db):
    first = db.add_ticker("AAPL", temp_db)
    second = db.add_ticker("AAPL", temp_db)

    assert first is True       # newly added
    assert second is False     # already there — the UI shows "already exists"
    assert db.list_tickers(temp_db) == ["AAPL"]


def test_removing_a_ticker_also_removes_its_data(temp_db):
    """The schema declares ON DELETE CASCADE; this proves it's switched on.

    SQLite ignores foreign keys unless PRAGMA foreign_keys is set per
    connection, so this is easy to break by accident.
    """
    db.add_ticker("AAPL", temp_db)
    db.save_stock_data("AAPL", {"current_price": 100.0, "status": "OK"}, temp_db)

    db.remove_ticker("AAPL", temp_db)

    assert db.list_tickers(temp_db) == []
    conn = db.get_db(temp_db)
    try:
        orphans = conn.execute("SELECT COUNT(*) FROM stock_data").fetchone()[0]
    finally:
        conn.close()
    assert orphans == 0


# ── Saving and reading stock data ────────────────────────────────────

def test_zero_values_are_stored_as_zero_not_null(temp_db):
    """Regression test for the `value or None` bug.

    A stock with zero Strong Sell ratings is real data. Before the fix
    it was stored as NULL and the dashboard showed "—" instead of "0".
    """
    db.add_ticker("AAPL", temp_db)
    db.save_stock_data("AAPL", {
        "current_price": 100.0,
        "strong_sell": 0,
        "sell": 0,
        "upside": 0,
        "status": "OK",
    }, temp_db)

    row = db.get_latest_data(temp_db)[0]
    assert row["strong_sell"] == 0
    assert row["sell"] == 0
    assert row["upside"] == 0


def test_blank_values_are_stored_as_null(temp_db):
    """The other half of the same rule: empty string means "no data".

    fetcher.py returns "" for fields Yahoo didn't provide.
    """
    db.add_ticker("AAPL", temp_db)
    db.save_stock_data("AAPL", {"current_price": 100.0, "beta": "", "status": "OK"}, temp_db)

    row = db.get_latest_data(temp_db)[0]
    assert row["beta"] is None


def test_get_latest_data_returns_the_newest_snapshot(temp_db):
    """Each refresh inserts a new row; the dashboard must show the last one."""
    db.add_ticker("AAPL", temp_db)
    db.save_stock_data("AAPL", {"current_price": 100.0, "updated": "2026-01-01 05:00"}, temp_db)
    db.save_stock_data("AAPL", {"current_price": 250.0, "updated": "2026-06-01 05:00"}, temp_db)

    rows = db.get_latest_data(temp_db)
    assert len(rows) == 1                    # one row per ticker, not per snapshot
    assert rows[0]["current_price"] == 250.0


def test_tickers_with_no_data_still_appear(temp_db):
    """A just-added ticker must show up before its first fetch completes."""
    db.add_ticker("AAPL", temp_db)

    rows = db.get_latest_data(temp_db)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAPL"
    assert rows[0]["current_price"] is None


def test_saving_data_for_an_unknown_ticker_does_nothing(temp_db):
    """Should be a silent no-op, not a crash."""
    db.save_stock_data("NOPE", {"current_price": 100.0}, temp_db)

    assert db.get_latest_data(temp_db) == []


# ── Sparklines ───────────────────────────────────────────────────────

def test_sparklines_round_trip(temp_db):
    """Prices are stored as a JSON string and must come back as numbers."""
    db.add_ticker("AAPL", temp_db)
    db.save_sparkline("AAPL", [1.5, 2.5, 3.5], temp_db)

    assert db.get_all_sparklines(temp_db) == {"AAPL": [1.5, 2.5, 3.5]}


def test_saving_a_sparkline_twice_replaces_it(temp_db):
    """Otherwise the cache would accumulate duplicate rows per ticker."""
    db.add_ticker("AAPL", temp_db)
    db.save_sparkline("AAPL", [1.0, 2.0], temp_db)
    db.save_sparkline("AAPL", [9.0, 8.0], temp_db)

    assert db.get_all_sparklines(temp_db) == {"AAPL": [9.0, 8.0]}


# ── Analyst actions and recommendations ──────────────────────────────

def test_saving_analyst_actions_replaces_the_previous_set(temp_db):
    """Each refresh replaces the ticker's actions rather than appending."""
    db.add_ticker("AAPL", temp_db)
    db.save_recent_analyst_actions("AAPL", [{"date": "2026-01-01", "firm": "Old Bank"}], temp_db)
    db.save_recent_analyst_actions("AAPL", [{"date": "2026-06-01", "firm": "New Bank"}], temp_db)

    actions = db.get_dashboard_trends(temp_db)["AAPL"]
    assert len(actions) == 1
    assert actions[0]["firm"] == "New Bank"


def test_recommendations_keep_their_order(temp_db):
    """The sentiment bar uses the first entry as the current month, so the
    order rows were saved in has to survive the round trip."""
    db.add_ticker("AAPL", temp_db)
    db.save_recommendations_cache("AAPL", [
        {"period": "0m", "strong_buy": 10},
        {"period": "-1m", "strong_buy": 5},
    ], temp_db)

    recs = db.get_all_recommendations_cache(temp_db)["AAPL"]
    assert [r["period"] for r in recs] == ["0m", "-1m"]


# ── tickers.txt export / import ──────────────────────────────────────

def test_ticker_file_export_import_round_trip(temp_db, tmp_path):
    path = str(tmp_path / "tickers.txt")
    db.add_ticker("AAPL", temp_db)
    db.add_ticker("MSFT", temp_db)

    db.export_tickers_to_file(path, temp_db)

    assert db.import_tickers_from_file(path) == ["AAPL", "MSFT"]


def test_importing_a_missing_file_returns_empty(tmp_path):
    """A fresh install with no ticker file must not crash on startup."""
    assert db.import_tickers_from_file(str(tmp_path / "nope.txt")) == []
