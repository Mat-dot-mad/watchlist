import os
import sqlite3
from datetime import datetime

DATABASE_PATH = os.environ.get("DATABASE_PATH", "watchlist.db")


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
        CREATE INDEX IF NOT EXISTS idx_stock_data_ticker ON stock_data(ticker_id);
        CREATE INDEX IF NOT EXISTS idx_stock_data_updated ON stock_data(updated_at DESC);
    """)
    conn.commit()
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
                 updated_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            row["id"],
            data.get("current_price") or None,
            data.get("target_mean") or None,
            data.get("target_low") or None,
            data.get("target_high") or None,
            data.get("upside") or None,
            data.get("n_analysts") or None,
            data.get("strong_buy") or None,
            data.get("buy") or None,
            data.get("hold") or None,
            data.get("sell") or None,
            data.get("strong_sell") or None,
            data.get("updated") or datetime.now().strftime("%Y-%m-%d %H:%M"),
            data.get("status"),
        ))
        conn.commit()
    finally:
        conn.close()


def get_latest_data(db_path=None):
    conn = get_db(db_path)
    try:
        rows = conn.execute("""
            SELECT t.symbol, sd.current_price, sd.target_mean, sd.target_low,
                   sd.target_high, sd.upside, sd.n_analysts, sd.strong_buy,
                   sd.buy, sd.hold, sd.sell, sd.strong_sell, sd.updated_at, sd.status
            FROM tickers t
            LEFT JOIN stock_data sd ON sd.id = (
                SELECT id FROM stock_data
                WHERE ticker_id = t.id
                ORDER BY updated_at DESC
                LIMIT 1
            )
            ORDER BY t.symbol
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_last_updated(db_path=None):
    conn = get_db(db_path)
    try:
        row = conn.execute("SELECT MAX(updated_at) as last FROM stock_data").fetchone()
        return row["last"] if row else None
    finally:
        conn.close()
