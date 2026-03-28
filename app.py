import logging
import os
import threading
from datetime import timedelta
from functools import wraps

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for

import db
from fetcher import fetch_all_tickers

log = logging.getLogger(__name__)

_refreshing = False
_refresh_lock = threading.Lock()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        password = os.environ.get("DASHBOARD_PASSWORD")
        if password and not session.get("authenticated"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def refresh_all_data():
    global _refreshing
    with _refresh_lock:
        if _refreshing:
            return
        _refreshing = True

    try:
        tickers = db.list_tickers()
        if not tickers:
            log.info("No tickers to refresh.")
            return

        log.info(f"Refreshing {len(tickers)} tickers...")
        results = fetch_all_tickers(tickers)

        for data in results:
            db.save_stock_data(data["ticker"], data)

        # Optional Sheets sync
        try:
            from sheets_sync import is_sheets_configured, sync_to_sheets
            if is_sheets_configured():
                sync_to_sheets(results)
        except Exception as e:
            log.warning(f"Sheets sync failed: {e}")

        log.info("Refresh complete.")
    except Exception as e:
        log.error(f"Refresh error: {e}")
    finally:
        _refreshing = False


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24))
    app.permanent_session_lifetime = timedelta(days=30)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    # Initialize database
    db.init_db()

    # Seed from Google Sheets if DB is empty
    if not db.list_tickers():
        try:
            from sheets_sync import import_tickers_from_sheet, is_sheets_configured
            if is_sheets_configured():
                tickers = import_tickers_from_sheet()
                for t in tickers:
                    db.add_ticker(t)
                log.info(f"Imported {len(tickers)} tickers from Google Sheets.")
        except Exception as e:
            log.warning(f"Could not import from Sheets: {e}")

    # Background scheduler
    scheduler = BackgroundScheduler()
    refresh_hour = int(os.environ.get("REFRESH_HOUR", "8"))
    scheduler.add_job(refresh_all_data, "cron", hour=refresh_hour, minute=0)
    scheduler.start()

    @app.route("/login", methods=["GET", "POST"])
    def login():
        password = os.environ.get("DASHBOARD_PASSWORD")
        if not password:
            return redirect(url_for("dashboard"))

        if request.method == "POST":
            if request.form.get("password") == password:
                session.permanent = True
                session["authenticated"] = True
                return redirect(url_for("dashboard"))
            flash("Wrong password.", "error")

        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    @login_required
    def dashboard():
        data = db.get_latest_data()
        last_updated = db.get_last_updated()
        return render_template("dashboard.html", stocks=data, last_updated=last_updated, refreshing=_refreshing)

    @app.route("/refresh", methods=["POST"])
    @login_required
    def refresh():
        if not _refreshing:
            thread = threading.Thread(target=refresh_all_data, daemon=True)
            thread.start()
            flash("Refresh started...", "info")
        else:
            flash("Refresh already in progress.", "info")
        return redirect(url_for("dashboard"))

    @app.route("/ticker/add", methods=["POST"])
    @login_required
    def add_ticker():
        symbol = request.form.get("symbol", "").strip().upper()
        if symbol:
            if db.add_ticker(symbol):
                flash(f"Added {symbol}.", "success")
            else:
                flash(f"{symbol} already exists.", "info")
        return redirect(url_for("dashboard"))

    @app.route("/ticker/<symbol>/remove", methods=["POST"])
    @login_required
    def remove_ticker(symbol):
        db.remove_ticker(symbol)
        flash(f"Removed {symbol}.", "success")
        return redirect(url_for("dashboard"))

    @app.route("/api/status")
    @login_required
    def status():
        return jsonify(refreshing=_refreshing, last_updated=db.get_last_updated())

    return app


if __name__ == "__main__":
    app = create_app()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
