import logging
import time
from datetime import datetime, timedelta

import yfinance as yf

log = logging.getLogger(__name__)

REQUEST_DELAY = 1.5


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
        rec_summary = []
        try:
            rec_df = t.recommendations_summary
            if rec_df is not None and not rec_df.empty:
                latest = rec_df.iloc[0]
                strong_buy = int(latest.get("strongBuy", 0))
                buy = int(latest.get("buy", 0))
                hold = int(latest.get("hold", 0))
                sell = int(latest.get("sell", 0))
                strong_sell = int(latest.get("strongSell", 0))
                # Cache all months for dashboard sentiment bars
                for _, row in rec_df.iterrows():
                    rec_summary.append({
                        "period": str(row.name) if hasattr(row, 'name') else "",
                        "strong_buy": int(row.get("strongBuy", 0)),
                        "buy": int(row.get("buy", 0)),
                        "hold": int(row.get("hold", 0)),
                        "sell": int(row.get("sell", 0)),
                        "strong_sell": int(row.get("strongSell", 0)),
                    })
        except Exception:
            pass

        # Recent analyst actions (last 90 days) for dashboard trends
        recent_actions = []
        try:
            ud = t.upgrades_downgrades
            if ud is not None and not ud.empty:
                cutoff = datetime.now() - timedelta(days=90)
                for date_idx, row in ud.iterrows():
                    try:
                        action_date = date_idx.strftime("%Y-%m-%d") if hasattr(date_idx, 'strftime') else str(date_idx)[:10]
                        if hasattr(date_idx, 'timestamp') and date_idx.timestamp() < cutoff.timestamp():
                            continue
                    except Exception:
                        action_date = str(date_idx)[:10]
                    recent_actions.append({
                        "date": action_date,
                        "firm": row.get("Firm", ""),
                        "to_grade": row.get("ToGrade", ""),
                        "from_grade": row.get("FromGrade", ""),
                        "action_type": row.get("Action", ""),
                        "price_target": row.get("currentPriceTarget") if row.get("currentPriceTarget") else None,
                        "prior_target": row.get("priorPriceTarget") if row.get("priorPriceTarget") else None,
                    })
                recent_actions = recent_actions[:10]  # Keep last 10 for dashboard
        except Exception:
            pass

        # 30-day sparkline
        sparkline = []
        try:
            hist = t.history(period="1mo")
            if hist is not None and not hist.empty:
                sparkline = [round(float(row["Close"]), 2) for _, row in hist.iterrows()]
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
            "market_cap": info.get("marketCap") or "",
            "trailing_pe": _fmt(info.get("trailingPE")),
            "forward_pe": _fmt(info.get("forwardPE")),
            "price_to_book": _fmt(info.get("priceToBook")),
            "fifty_day_avg": _fmt(info.get("fiftyDayAverage")),
            "two_hundred_day_avg": _fmt(info.get("twoHundredDayAverage")),
            "fifty_two_week_high": _fmt(info.get("fiftyTwoWeekHigh")),
            "fifty_two_week_low": _fmt(info.get("fiftyTwoWeekLow")),
            "beta": _fmt(info.get("beta")),
            "dividend_yield": info.get("dividendYield") or "",
            "currency": info.get("currency") or "USD",
            "sector": info.get("sector") or "",
            "long_name": info.get("longName") or info.get("shortName") or "",
            "recent_actions": recent_actions,
            "rec_summary": rec_summary,
            "sparkline": sparkline,
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
            "market_cap": "", "trailing_pe": "", "forward_pe": "",
            "price_to_book": "", "fifty_day_avg": "", "two_hundred_day_avg": "",
            "fifty_two_week_high": "", "fifty_two_week_low": "",
            "beta": "", "dividend_yield": "", "currency": "", "sector": "", "long_name": "",
            "recent_actions": [], "rec_summary": [], "sparkline": [],
        }


def fetch_ticker_detail_live(ticker: str) -> dict:
    try:
        t = yf.Ticker(ticker)

        # Analyst history (upgrades/downgrades)
        analyst_history = []
        try:
            ud = t.upgrades_downgrades
            if ud is not None and not ud.empty:
                for date_idx, row in ud.head(50).iterrows():
                    try:
                        action_date = date_idx.strftime("%Y-%m-%d") if hasattr(date_idx, 'strftime') else str(date_idx)[:10]
                    except Exception:
                        action_date = str(date_idx)[:10]
                    price_target = row.get("currentPriceTarget")
                    prior_target = row.get("priorPriceTarget")
                    # Determine price action
                    price_action = ""
                    if price_target and prior_target:
                        if price_target > prior_target:
                            price_action = "Raises"
                        elif price_target < prior_target:
                            price_action = "Lowers"
                        else:
                            price_action = "Maintains"
                    elif price_target:
                        price_action = "Sets"
                    analyst_history.append({
                        "date": action_date,
                        "firm": row.get("Firm", ""),
                        "to_grade": row.get("ToGrade", ""),
                        "from_grade": row.get("FromGrade", ""),
                        "action": row.get("Action", ""),
                        "price_action": price_action,
                        "price_target": round(float(price_target), 2) if price_target else None,
                        "prior_target": round(float(prior_target), 2) if prior_target else None,
                    })
        except Exception as e:
            log.warning(f"[{ticker}] upgrades_downgrades error: {e}")

        # Recommendations summary (4 months)
        recommendations_monthly = []
        try:
            rs = t.recommendations_summary
            if rs is not None and not rs.empty:
                period_labels = ["Current", "-1M", "-2M", "-3M"]
                for i, (_, row) in enumerate(rs.iterrows()):
                    label = period_labels[i] if i < len(period_labels) else f"-{i}M"
                    recommendations_monthly.append({
                        "period": label,
                        "strong_buy": int(row.get("strongBuy", 0)),
                        "buy": int(row.get("buy", 0)),
                        "hold": int(row.get("hold", 0)),
                        "sell": int(row.get("sell", 0)),
                        "strong_sell": int(row.get("strongSell", 0)),
                    })
        except Exception as e:
            log.warning(f"[{ticker}] recommendations_summary error: {e}")

        # Price history (1 year)
        price_history = []
        try:
            hist = t.history(period="1y")
            if hist is not None and not hist.empty:
                for date_idx, row in hist.iterrows():
                    price_history.append({
                        "date": date_idx.strftime("%Y-%m-%d"),
                        "close": round(float(row["Close"]), 2),
                        "volume": int(row["Volume"]),
                    })
        except Exception as e:
            log.warning(f"[{ticker}] history error: {e}")

        # Price targets
        price_targets = {}
        try:
            apt = t.analyst_price_targets
            if apt:
                price_targets = {
                    "current": round(float(apt.get("current", 0)), 2) if apt.get("current") else None,
                    "high": round(float(apt.get("high", 0)), 2) if apt.get("high") else None,
                    "low": round(float(apt.get("low", 0)), 2) if apt.get("low") else None,
                    "mean": round(float(apt.get("mean", 0)), 2) if apt.get("mean") else None,
                    "median": round(float(apt.get("median", 0)), 2) if apt.get("median") else None,
                }
        except Exception as e:
            log.warning(f"[{ticker}] analyst_price_targets error: {e}")

        return {
            "analyst_history": analyst_history,
            "recommendations_monthly": recommendations_monthly,
            "price_history": price_history,
            "price_targets": price_targets,
        }

    except Exception as e:
        log.error(f"[{ticker}] Live fetch error: {e}")
        return {
            "analyst_history": [],
            "recommendations_monthly": [],
            "price_history": [],
            "price_targets": {},
        }


def fetch_all_tickers(tickers: list[str]) -> list[dict]:
    results = []
    for i, ticker in enumerate(tickers, start=1):
        log.info(f"  ({i}/{len(tickers)}) {ticker}...")
        results.append(fetch_ticker_data(ticker))
        if i < len(tickers):
            time.sleep(REQUEST_DELAY)
    return results
