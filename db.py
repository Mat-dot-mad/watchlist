import os
import sqlite3
from datetime import datetime

DATABASE_PATH = os.environ.get("DATABASE_PATH", "watchlist.db")
# Where add/remove-ticker writes the ticker list. In production point this
# outside the git clone (e.g. /var/lib/watchlist/tickers.txt) so the app
# never modifies a tracked file and `git pull` stays conflict-free.
TICKERS_FILE = os.environ.get("TICKERS_FILE", "tickers.txt")


def get_db(db_path=None):
    conn = sqlite3.connect(db_path or DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path=None):
    conn = get_db(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tickers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT UNIQUE NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS stock_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker_id INTEGER NOT NULL REFERENCES tickers(id) ON DELETE CASCADE,
            current_price REAL,
            target_mean REAL,
            target_low REAL,
            target_high REAL,
            upside REAL,
            n_analysts INTEGER,
            strong_buy INTEGER,
            buy INTEGER,
            hold INTEGER,
            sell INTEGER,
            strong_sell INTEGER,
            updated_at TIMESTAMP,
            status TEXT,
            FOREIGN KEY (ticker_id) REFERENCES tickers(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS recent_analyst_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker_id INTEGER NOT NULL REFERENCES tickers(id) ON DELETE CASCADE,
            action_date TEXT,
            firm TEXT,
            to_grade TEXT,
            from_grade TEXT,
            action_type TEXT,
            price_target REAL,
            prior_target REAL
        );
        CREATE TABLE IF NOT EXISTS recommendations_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker_id INTEGER NOT NULL REFERENCES tickers(id) ON DELETE CASCADE,
            period TEXT,
            strong_buy INTEGER DEFAULT 0,
            buy INTEGER DEFAULT 0,
            hold INTEGER DEFAULT 0,
            sell INTEGER DEFAULT 0,
            strong_sell INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_stock_data_ticker ON stock_data(ticker_id);
        CREATE INDEX IF NOT EXISTS idx_stock_data_updated ON stock_data(updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_analyst_actions_ticker ON recent_analyst_actions(ticker_id);
        CREATE INDEX IF NOT EXISTS idx_rec_cache_ticker ON recommendations_cache(ticker_id);
    """)
    conn.commit()
    conn.close()
    _migrate_db(db_path)


def _migrate_db(db_path=None):
    conn = get_db(db_path)
    try:
        existing_sd = {row[1] for row in conn.execute("PRAGMA table_info(stock_data)").fetchall()}
        new_stock_cols = {
            "market_cap": "REAL", "trailing_pe": "REAL", "forward_pe": "REAL",
            "price_to_book": "REAL", "fifty_day_avg": "REAL", "two_hundred_day_avg": "REAL",
            "fifty_two_week_high": "REAL", "fifty_two_week_low": "REAL",
            "beta": "REAL", "dividend_yield": "REAL",
        }
        for col, typ in new_stock_cols.items():
            if col not in existing_sd:
                conn.execute(f"ALTER TABLE stock_data ADD COLUMN {col} {typ}")

        existing_t = {row[1] for row in conn.execute("PRAGMA table_info(tickers)").fetchall()}
        for col, typ in {"sector": "TEXT", "long_name": "TEXT", "currency": "TEXT"}.items():
            if col not in existing_t:
                conn.execute(f"ALTER TABLE tickers ADD COLUMN {col} {typ}")

        # Sparkline cache table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sparkline_cache (
                ticker_id INTEGER PRIMARY KEY REFERENCES tickers(id) ON DELETE CASCADE,
                prices TEXT
            )
        """)

        # Watchlists: named groupings of tickers. Many-to-many — a ticker can
        # sit in several lists — so membership lives in a join table rather
        # than a column on tickers. The composite primary key stops the same
        # ticker being added to the same list twice. Lists only filter what
        # the dashboard shows; fetching is still once per ticker.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL COLLATE NOCASE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist_tickers (
                watchlist_id INTEGER NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
                ticker_id INTEGER NOT NULL REFERENCES tickers(id) ON DELETE CASCADE,
                PRIMARY KEY (watchlist_id, ticker_id)
            )
        """)
        # The primary key index starts with watchlist_id; this one serves the
        # reverse lookup ("which lists is NVDA in?").
        conn.execute("CREATE INDEX IF NOT EXISTS idx_wt_ticker ON watchlist_tickers(ticker_id)")

        conn.commit()
    finally:
        conn.close()


def add_ticker(symbol, db_path=None):
    conn = get_db(db_path)
    try:
        conn.execute("INSERT OR IGNORE INTO tickers (symbol) VALUES (?)", (symbol.strip().upper(),))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def remove_ticker(symbol, db_path=None):
    conn = get_db(db_path)
    try:
        conn.execute("DELETE FROM tickers WHERE symbol = ?", (symbol.strip().upper(),))
        conn.commit()
    finally:
        conn.close()


def list_tickers(db_path=None):
    conn = get_db(db_path)
    try:
        rows = conn.execute("SELECT symbol FROM tickers ORDER BY symbol").fetchall()
        return [r["symbol"] for r in rows]
    finally:
        conn.close()


def export_tickers_to_file(filepath=None, db_path=None):
    tickers = list_tickers(db_path)
    if tickers:
        with open(filepath or TICKERS_FILE, "w") as f:
            f.write("\n".join(tickers) + "\n")


def import_tickers_from_file(filepath=None):
    # Prefer the configured file; fall back to the repo's tickers.txt so a
    # fresh install still seeds even before TICKERS_FILE has been written.
    candidates = [filepath] if filepath else [TICKERS_FILE, "tickers.txt"]
    for path in candidates:
        try:
            with open(path) as f:
                return [line.strip().upper() for line in f if line.strip()]
        except FileNotFoundError:
            continue
    return []


def update_ticker_metadata(symbol, sector, long_name, currency=None, db_path=None):
    conn = get_db(db_path)
    try:
        conn.execute(
            "UPDATE tickers SET sector = ?, long_name = ?, currency = ? WHERE symbol = ?",
            (sector, long_name, currency, symbol)
        )
        conn.commit()
    finally:
        conn.close()


def _num(val):
    # 0 is real data (e.g. zero Strong Sell ratings); only blank means missing.
    # A bare `or None` would turn 0 into NULL.
    return None if val in (None, "") else val


def save_stock_data(ticker_symbol, data, db_path=None):
    conn = get_db(db_path)
    try:
        row = conn.execute("SELECT id FROM tickers WHERE symbol = ?", (ticker_symbol,)).fetchone()
        if not row:
            return
        conn.execute("""
            INSERT INTO stock_data
                (ticker_id, current_price, target_mean, target_low, target_high,
                 upside, n_analysts, strong_buy, buy, hold, sell, strong_sell,
                 updated_at, status,
                 market_cap, trailing_pe, forward_pe, price_to_book,
                 fifty_day_avg, two_hundred_day_avg, fifty_two_week_high, fifty_two_week_low,
                 beta, dividend_yield)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            row["id"],
            _num(data.get("current_price")),
            _num(data.get("target_mean")),
            _num(data.get("target_low")),
            _num(data.get("target_high")),
            _num(data.get("upside")),
            _num(data.get("n_analysts")),
            _num(data.get("strong_buy")),
            _num(data.get("buy")),
            _num(data.get("hold")),
            _num(data.get("sell")),
            _num(data.get("strong_sell")),
            data.get("updated") or datetime.now().strftime("%Y-%m-%d %H:%M"),
            data.get("status"),
            _num(data.get("market_cap")),
            _num(data.get("trailing_pe")),
            _num(data.get("forward_pe")),
            _num(data.get("price_to_book")),
            _num(data.get("fifty_day_avg")),
            _num(data.get("two_hundred_day_avg")),
            _num(data.get("fifty_two_week_high")),
            _num(data.get("fifty_two_week_low")),
            _num(data.get("beta")),
            _num(data.get("dividend_yield")),
        ))
        conn.commit()
    finally:
        conn.close()


def save_recent_analyst_actions(symbol, actions, db_path=None):
    conn = get_db(db_path)
    try:
        row = conn.execute("SELECT id FROM tickers WHERE symbol = ?", (symbol,)).fetchone()
        if not row:
            return
        ticker_id = row["id"]
        conn.execute("DELETE FROM recent_analyst_actions WHERE ticker_id = ?", (ticker_id,))
        for a in actions:
            conn.execute("""
                INSERT INTO recent_analyst_actions
                    (ticker_id, action_date, firm, to_grade, from_grade, action_type, price_target, prior_target)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ticker_id, a.get("date"), a.get("firm"), a.get("to_grade"),
                a.get("from_grade"), a.get("action_type"), a.get("price_target"), a.get("prior_target"),
            ))
        conn.commit()
    finally:
        conn.close()


def save_recommendations_cache(symbol, recs, db_path=None):
    conn = get_db(db_path)
    try:
        row = conn.execute("SELECT id FROM tickers WHERE symbol = ?", (symbol,)).fetchone()
        if not row:
            return
        ticker_id = row["id"]
        conn.execute("DELETE FROM recommendations_cache WHERE ticker_id = ?", (ticker_id,))
        for r in recs:
            conn.execute("""
                INSERT INTO recommendations_cache
                    (ticker_id, period, strong_buy, buy, hold, sell, strong_sell)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                ticker_id, r.get("period"), r.get("strong_buy", 0), r.get("buy", 0),
                r.get("hold", 0), r.get("sell", 0), r.get("strong_sell", 0),
            ))
        conn.commit()
    finally:
        conn.close()


def save_sparkline(symbol, prices, db_path=None):
    import json
    conn = get_db(db_path)
    try:
        row = conn.execute("SELECT id FROM tickers WHERE symbol = ?", (symbol,)).fetchone()
        if not row:
            return
        conn.execute(
            "INSERT OR REPLACE INTO sparkline_cache (ticker_id, prices) VALUES (?, ?)",
            (row["id"], json.dumps(prices))
        )
        conn.commit()
    finally:
        conn.close()


def get_all_sparklines(db_path=None):
    import json
    conn = get_db(db_path)
    try:
        rows = conn.execute("""
            SELECT t.symbol, sc.prices
            FROM sparkline_cache sc
            JOIN tickers t ON t.id = sc.ticker_id
        """).fetchall()
        return {r["symbol"]: json.loads(r["prices"]) for r in rows}
    finally:
        conn.close()


def get_latest_data(db_path=None, watchlist_id=None, unsorted=False):
    """Latest snapshot per ticker. Pass watchlist_id to limit to one list,
    or unsorted=True for tickers that are in no list. Default: all tickers."""
    if watchlist_id is not None:
        where = "WHERE t.id IN (SELECT ticker_id FROM watchlist_tickers WHERE watchlist_id = ?)"
        params = (watchlist_id,)
    elif unsorted:
        where = "WHERE t.id NOT IN (SELECT ticker_id FROM watchlist_tickers)"
        params = ()
    else:
        where, params = "", ()
    conn = get_db(db_path)
    try:
        rows = conn.execute(f"""
            SELECT t.symbol, t.sector, t.long_name, t.currency,
                   sd.current_price, sd.target_mean, sd.target_low,
                   sd.target_high, sd.upside, sd.n_analysts, sd.strong_buy,
                   sd.buy, sd.hold, sd.sell, sd.strong_sell, sd.updated_at, sd.status,
                   sd.market_cap, sd.trailing_pe, sd.forward_pe, sd.price_to_book,
                   sd.fifty_day_avg, sd.two_hundred_day_avg,
                   sd.fifty_two_week_high, sd.fifty_two_week_low,
                   sd.beta, sd.dividend_yield
            FROM tickers t
            LEFT JOIN stock_data sd ON sd.id = (
                SELECT id FROM stock_data
                WHERE ticker_id = t.id
                ORDER BY updated_at DESC
                LIMIT 1
            )
            {where}
            ORDER BY t.symbol
        """, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Watchlists ───────────────────────────────────────────────────────

def list_watchlists(db_path=None):
    """All lists in creation order, each with its ticker count."""
    conn = get_db(db_path)
    try:
        rows = conn.execute("""
            SELECT w.id, w.name, COUNT(wt.ticker_id) AS count
            FROM watchlists w
            LEFT JOIN watchlist_tickers wt ON wt.watchlist_id = w.id
            GROUP BY w.id
            ORDER BY w.id
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def count_unsorted(db_path=None):
    conn = get_db(db_path)
    try:
        return conn.execute("""
            SELECT COUNT(*) FROM tickers
            WHERE id NOT IN (SELECT ticker_id FROM watchlist_tickers)
        """).fetchone()[0]
    finally:
        conn.close()


def create_watchlist(name, db_path=None):
    """Returns the new list's id, or None if the name is blank or taken."""
    name = name.strip()
    if not name:
        return None
    conn = get_db(db_path)
    try:
        cur = conn.execute("INSERT OR IGNORE INTO watchlists (name) VALUES (?)", (name,))
        conn.commit()
        return cur.lastrowid if cur.rowcount else None
    finally:
        conn.close()


def rename_watchlist(watchlist_id, name, db_path=None):
    """Returns False if the name is blank or already used by another list."""
    name = name.strip()
    if not name:
        return False
    conn = get_db(db_path)
    try:
        cur = conn.execute("UPDATE OR IGNORE watchlists SET name = ? WHERE id = ?", (name, watchlist_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_watchlist(watchlist_id, db_path=None):
    """Deletes the list only; its tickers stay (memberships cascade away)."""
    conn = get_db(db_path)
    try:
        conn.execute("DELETE FROM watchlists WHERE id = ?", (watchlist_id,))
        conn.commit()
    finally:
        conn.close()


def get_memberships(db_path=None):
    """{symbol: {watchlist_id, ...}} for every ticker that is in a list."""
    conn = get_db(db_path)
    try:
        rows = conn.execute("""
            SELECT t.symbol, wt.watchlist_id
            FROM watchlist_tickers wt
            JOIN tickers t ON t.id = wt.ticker_id
        """).fetchall()
        result = {}
        for r in rows:
            result.setdefault(r["symbol"], set()).add(r["watchlist_id"])
        return result
    finally:
        conn.close()


def set_ticker_watchlists(symbol, watchlist_ids, db_path=None):
    """Replace a ticker's memberships with exactly the given list ids."""
    conn = get_db(db_path)
    try:
        row = conn.execute("SELECT id FROM tickers WHERE symbol = ?", (symbol,)).fetchone()
        if not row:
            return
        conn.execute("DELETE FROM watchlist_tickers WHERE ticker_id = ?", (row["id"],))
        # INSERT ... SELECT skips ids of lists that don't exist (e.g. deleted
        # in another tab) instead of failing on the foreign key.
        for wid in set(watchlist_ids):
            conn.execute("""
                INSERT INTO watchlist_tickers (watchlist_id, ticker_id)
                SELECT id, ? FROM watchlists WHERE id = ?
            """, (row["id"], wid))
        conn.commit()
    finally:
        conn.close()


def add_ticker_to_watchlist(symbol, watchlist_id, db_path=None):
    """Returns True if newly added, False if already there (or no such list)."""
    conn = get_db(db_path)
    try:
        cur = conn.execute("""
            INSERT OR IGNORE INTO watchlist_tickers (watchlist_id, ticker_id)
            SELECT w.id, t.id FROM watchlists w, tickers t
            WHERE w.id = ? AND t.symbol = ?
        """, (watchlist_id, symbol))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def remove_ticker_from_watchlist(symbol, watchlist_id, db_path=None):
    conn = get_db(db_path)
    try:
        conn.execute("""
            DELETE FROM watchlist_tickers
            WHERE watchlist_id = ?
              AND ticker_id = (SELECT id FROM tickers WHERE symbol = ?)
        """, (watchlist_id, symbol))
        conn.commit()
    finally:
        conn.close()


def get_last_updated(db_path=None):
    conn = get_db(db_path)
    try:
        row = conn.execute("SELECT MAX(updated_at) as last FROM stock_data").fetchone()
        return row["last"] if row else None
    finally:
        conn.close()


def get_dashboard_trends(db_path=None):
    conn = get_db(db_path)
    try:
        rows = conn.execute("""
            SELECT t.symbol, a.action_date, a.firm, a.to_grade, a.from_grade,
                   a.action_type, a.price_target, a.prior_target
            FROM recent_analyst_actions a
            JOIN tickers t ON t.id = a.ticker_id
            ORDER BY a.action_date DESC
        """).fetchall()
        result = {}
        for r in rows:
            sym = r["symbol"]
            if sym not in result:
                result[sym] = []
            result[sym].append(dict(r))
        return result
    finally:
        conn.close()


def get_all_recommendations_cache(db_path=None):
    conn = get_db(db_path)
    try:
        rows = conn.execute("""
            SELECT t.symbol, rc.period, rc.strong_buy, rc.buy, rc.hold, rc.sell, rc.strong_sell
            FROM recommendations_cache rc
            JOIN tickers t ON t.id = rc.ticker_id
            ORDER BY t.symbol, rc.id ASC
        """).fetchall()
        result = {}
        for r in rows:
            sym = r["symbol"]
            if sym not in result:
                result[sym] = []
            result[sym].append(dict(r))
        return result
    finally:
        conn.close()


def get_ticker_detail(symbol, db_path=None):
    conn = get_db(db_path)
    try:
        row = conn.execute("""
            SELECT t.symbol, t.sector, t.long_name, t.currency,
                   sd.current_price, sd.target_mean, sd.target_low, sd.target_high,
                   sd.upside, sd.n_analysts, sd.strong_buy, sd.buy, sd.hold, sd.sell,
                   sd.strong_sell, sd.updated_at, sd.status,
                   sd.market_cap, sd.trailing_pe, sd.forward_pe, sd.price_to_book,
                   sd.fifty_day_avg, sd.two_hundred_day_avg,
                   sd.fifty_two_week_high, sd.fifty_two_week_low,
                   sd.beta, sd.dividend_yield
            FROM tickers t
            LEFT JOIN stock_data sd ON sd.id = (
                SELECT id FROM stock_data
                WHERE ticker_id = t.id
                ORDER BY updated_at DESC
                LIMIT 1
            )
            WHERE t.symbol = ?
        """, (symbol.upper(),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
