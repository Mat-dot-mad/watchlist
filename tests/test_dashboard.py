"""Tests for the web layer — routes and dashboard rendering.

The `client` fixture (see conftest.py) is a fake browser: it makes
requests to the app in-process, with no server and no network.

These tests exist mainly to catch *silent* template breakage. A missing
<td> doesn't raise an error — it just shifts every later value under the
wrong heading, which is easy to miss by eye and impossible to miss here.
"""
import re

import db


def _add_stock(**overrides):
    """Put one ticker with data into whatever database the app is using."""
    data = {"current_price": 100.0, "fifty_two_week_high": 125.0, "status": "OK"}
    data.update(overrides)
    symbol = data.pop("symbol", "AAPL")
    db.add_ticker(symbol)
    db.save_stock_data(symbol, data)


# ── Basic routes ─────────────────────────────────────────────────────

def test_dashboard_loads_when_empty(client):
    """A brand-new install with no tickers must still render."""
    resp = client.get("/")

    assert resp.status_code == 200
    assert "No tickers yet" in resp.get_data(as_text=True)


def test_dashboard_shows_a_ticker(client):
    _add_stock()

    body = client.get("/").get_data(as_text=True)
    assert "AAPL" in body


def test_status_endpoint_returns_json(client):
    """The dashboard polls this while a refresh is running."""
    resp = client.get("/api/status")

    assert resp.status_code == 200
    assert "refreshing" in resp.get_json()


def test_login_is_required_when_a_password_is_set(client, monkeypatch):
    """With DASHBOARD_PASSWORD set, an unauthenticated visit must redirect."""
    monkeypatch.setenv("DASHBOARD_PASSWORD", "hunter2")

    resp = client.get("/")

    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_correct_password_grants_access(client, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "hunter2")

    resp = client.post("/login", data={"password": "hunter2"})

    assert resp.status_code == 302
    assert "/login" not in resp.headers["Location"]


def test_wrong_password_is_rejected(client, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "hunter2")

    resp = client.post("/login", data={"password": "wrong"}, follow_redirects=True)

    assert "Wrong password" in resp.get_data(as_text=True)


# ── Table structure ──────────────────────────────────────────────────

def test_every_row_has_one_cell_per_column(client):
    """Catches a <td> being added or dropped without its <th>.

    Without this, a missing cell silently shifts all later values one
    column to the left, under the wrong headings.
    """
    _add_stock(symbol="AAPL")
    _add_stock(symbol="MSFT", current_price=None, fifty_two_week_high=None)

    body = client.get("/").get_data(as_text=True)
    rows = re.findall(r"<tr>(.*?)</tr>", body, re.S)
    header, data_rows = rows[0], rows[1:]

    n_columns = len(re.findall(r"<th[ >]", header))
    for row in data_rows:
        assert len(re.findall(r"<td[ >]", row)) == n_columns


def test_sort_indices_match_column_positions(client):
    """The sort code finds cells by position, so each th's data-col must
    equal its actual index in the header row."""
    _add_stock()

    body = client.get("/").get_data(as_text=True)
    header = re.search(r"<thead>(.*?)</thead>", body, re.S).group(1)

    for position, th in enumerate(re.findall(r"<th[^>]*>", header)):
        declared = re.search(r'data-col="(\d+)"', th)
        if declared:  # only sortable columns carry data-col
            assert int(declared.group(1)) == position


# ── Off High % column ────────────────────────────────────────────────

def test_off_high_shows_distance_below_the_52_week_high(client):
    _add_stock(current_price=100.0, fifty_two_week_high=125.0)

    body = client.get("/").get_data(as_text=True)
    assert 'data-val="-20.0"' in body      # 100/125 - 1 = -20%


def test_off_high_is_marked_positive_at_a_new_high(client):
    """Yahoo's 52-week high can lag a breakout, giving a small positive."""
    _add_stock(current_price=105.0, fifty_two_week_high=100.0)

    body = client.get("/").get_data(as_text=True)
    assert 'data-val="5.0" class="positive"' in body


def test_off_high_is_blank_without_a_price(client):
    """Must not divide by a missing high, or by zero."""
    _add_stock(current_price=None, fifty_two_week_high=None)

    resp = client.get("/")
    assert resp.status_code == 200        # rendered without crashing


# ── Sparkline data ───────────────────────────────────────────────────

def test_sparkline_data_is_valid_json(client):
    """The dashboard JS does JSON.parse on this attribute, and JSON.parse
    rejects NaN — which is what once broke table sorting."""
    import json

    db.add_ticker("AAPL")
    db.save_sparkline("AAPL", [1.5, 2.5, 3.5])

    body = client.get("/").get_data(as_text=True)
    raw = re.search(r'data-prices="([^"]+)"', body).group(1)
    parsed = json.loads(raw.replace("&#34;", '"'))

    assert parsed == [1.5, 2.5, 3.5]
