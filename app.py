import logging
import os
import threading
from datetime import timedelta
from functools import wraps

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from markupsafe import Markup

import db
from fetcher import fetch_all_tickers, fetch_ticker_data, fetch_ticker_detail_live

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


def _save_ticker_data(data):
    symbol = data["ticker"]
    db.save_stock_data(symbol, data)
    if data.get("sector") or data.get("long_name"):
        db.update_ticker_metadata(symbol, data.get("sector"), data.get("long_name"))
    if data.get("recent_actions"):
        db.save_recent_analyst_actions(symbol, data["recent_actions"])
    if data.get("rec_summary"):
        db.save_recommendations_cache(symbol, data["rec_summary"])


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
            _save_ticker_data(data)

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

    # Template filters
    @app.template_filter('sentiment_bar')
    def sentiment_bar(rec_list, width=80, height=14):
        if not rec_list:
            return Markup('<span class="no-data">—</span>')
        # Use the first (current month) entry
        r = rec_list[0] if isinstance(rec_list, list) else rec_list
        sb = r.get("strong_buy", 0) or 0
        b = r.get("buy", 0) or 0
        h = r.get("hold", 0) or 0
        s = r.get("sell", 0) or 0
        ss = r.get("strong_sell", 0) or 0
        total = sb + b + h + s + ss
        if total == 0:
            return Markup('<span class="no-data">—</span>')
        buy_w = round((sb + b) / total * width, 1)
        hold_w = round(h / total * width, 1)
        sell_w = round((s + ss) / total * width, 1)
        return Markup(
            f'<svg width="{width}" height="{height}" class="sentiment-bar">'
            f'<rect x="0" y="0" width="{buy_w}" height="{height}" rx="2" fill="#238636"/>'
            f'<rect x="{buy_w}" y="0" width="{hold_w}" height="{height}" fill="#d29922"/>'
            f'<rect x="{buy_w + hold_w}" y="0" width="{sell_w}" height="{height}" rx="2" fill="#da3633"/>'
            f'</svg>'
        )

    @app.template_filter('fmt_large')
    def fmt_large(value):
        if not value:
            return '—'
        try:
            v = float(value)
        except (TypeError, ValueError):
            return '—'
        if v >= 1e12:
            return f"${v / 1e12:.1f}T"
        if v >= 1e9:
            return f"${v / 1e9:.1f}B"
        if v >= 1e6:
            return f"${v / 1e6:.1f}M"
        return f"${v:,.0f}"

    @app.template_filter('fmt_pct')
    def fmt_pct(value):
        if not value and value != 0:
            return '—'
        try:
            return f"{float(value) * 100:.2f}%"
        except (TypeError, ValueError):
            return '—'

    @app.template_filter('fmt_val')
    def fmt_val(value):
        if not value and value != 0:
            return '—'
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return '—'

    @app.template_filter('rating_class')
    def rating_class(grade):
        if not grade:
            return 'rating-neutral'
        g = grade.lower()
        if any(w in g for w in ['buy', 'outperform', 'overweight', 'positive', 'accumulate']):
            return 'rating-buy'
        if any(w in g for w in ['sell', 'underperform', 'underweight', 'negative', 'reduce']):
            return 'rating-sell'
        return 'rating-hold'

    # Routes
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
        trends = db.get_dashboard_trends()
        rec_cache = db.get_all_recommendations_cache()
        return render_template("dashboard.html",
                               stocks=data, last_updated=last_updated, refreshing=_refreshing,
                               trends=trends, rec_cache=rec_cache)

    @app.route("/ticker/<symbol>")
    @login_required
    def ticker_detail(symbol):
        symbol = symbol.upper()
        detail = db.get_ticker_detail(symbol)
        if not detail:
            flash(f"Ticker {symbol} not found.", "error")
            return redirect(url_for("dashboard"))
        live = fetch_ticker_detail_live(symbol)
        return render_template("ticker_detail.html", detail=detail, live=live, symbol=symbol)

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
                # Immediately fetch data for the new ticker
                data = fetch_ticker_data(symbol)
                _save_ticker_data(data)
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
