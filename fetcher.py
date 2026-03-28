import logging
import time
from datetime import datetime

import yfinance as yf

log = logging.getLogger(__name__)

REQUEST_DELAY = 1.5

HEADERS = [
    "Ticker",
    "Current Price",
    "Price Target (mean)",
    "Price Target (low)",
    "Price Target (high)",
    "Upside %",
    "# Analysts",
    "Strong Buy",
    "Buy",
    "Hold",
    "Sell",
    "Strong Sell",
    "Updated",
    "Status",
]


def _fmt(val):
    if val is None:
        return ""
    try:
        return round(float(val), 2)
    except (TypeError, ValueError):
        return ""


def fetch_ticker_data(ticker: str) -> dict:
    try:
        t = yf.Ticker(ticker)
        info = t.info

        current_price = (
            info.get("currentPrice")
            or info.get("regularMarketPrice")
            or info.get("previousClose")
        )

        target_mean = info.get("targetMeanPrice")
        target_low = info.get("targetLowPrice")
        target_high = info.get("targetHighPrice")
        n_analysts = info.get("numberOfAnalystOpinions")

        upside = None
        if current_price and target_mean and current_price > 0:
            upside = round((target_mean - current_price) / current_price * 100, 1)

        strong_buy = buy = hold = sell = strong_sell = ""
        try:
            rec_df = t.recommendations_summary
            if rec_df is not None and not rec_df.empty:
                latest = rec_df.iloc[0]
                strong_buy = int(latest.get("strongBuy", 0))
                buy = int(latest.get("buy", 0))
                hold = int(latest.get("hold", 0))
                sell = int(latest.get("sell", 0))
                strong_sell = int(latest.get("strongSell", 0))
        except Exception:
            pass

        return {
            "ticker": ticker,
            "current_price": _fmt(current_price),
            "target_mean": _fmt(target_mean),
            "target_low": _fmt(target_low),
            "target_high": _fmt(target_high),
            "upside": upside if upside is not None else "",
            "n_analysts": n_analysts or "",
            "strong_buy": strong_buy,
            "buy": buy,
            "hold": hold,
            "sell": sell,
            "strong_sell": strong_sell,
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "status": "OK",
        }

    except Exception as e:
        log.warning(f"  [{ticker}] Error: {e}")
        return {
            "ticker": ticker,
            "current_price": "", "target_mean": "", "target_low": "",
            "target_high": "", "upside": "", "n_analysts": "",
            "strong_buy": "", "buy": "", "hold": "", "sell": "",
            "strong_sell": "", "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "status": f"ERROR: {e}",
        }


def fetch_all_tickers(tickers: list[str]) -> list[dict]:
    results = []
    for i, ticker in enumerate(tickers, start=1):
        log.info(f"  ({i}/{len(tickers)}) {ticker}...")
        results.append(fetch_ticker_data(ticker))
        if i < len(tickers):
            time.sleep(REQUEST_DELAY)
    return results
