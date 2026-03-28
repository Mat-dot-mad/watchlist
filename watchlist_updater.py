"""
watchlist_updater.py
--------------------
Pobiera dane analityczne (price target, current price, rekomendacje)
dla listy spółek z Google Sheets i aktualizuje arkusz.

Źródło danych: Yahoo Finance (yfinance)
Wymagania: pip install yfinance gspread google-auth schedule
"""

import logging
import os
import time
from datetime import datetime

import gspread
import schedule
import yfinance as yf
from google.oauth2.service_account import Credentials

# ─────────────────────────────────────────────
# KONFIGURACJA — dostosuj do swojego arkusza
# ─────────────────────────────────────────────

# SHEET_ID: możesz wpisać bezpośrednio ALBO ustawić jako GitHub Secret (SHEET_ID)
SHEET_ID = os.environ.get("SHEET_ID", "TWÓJ_GOOGLE_SHEET_ID")
WORKSHEET_NAME = "Sheet1"                   # nazwa zakładki
SERVICE_ACCOUNT_FILE = "service_account.json"

TICKER_COLUMN_LETTER = "A"                  # kolumna z tickerami
DATA_START_ROW = 2                          # wiersz 1 = nagłówki

RUN_AT_TIME = "08:00"                       # godzina uruchomienia (format HH:MM)

# Opóźnienie między requestami (sekundy) — zapobiega throttlingowi Yahoo
REQUEST_DELAY = 1.5

# ─────────────────────────────────────────────
# SETUP LOGOWANIA
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("watchlist_updater.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# GOOGLE SHEETS
# ─────────────────────────────────────────────

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

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


def get_worksheet():
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID).worksheet(WORKSHEET_NAME)


# ─────────────────────────────────────────────
# POBIERANIE DANYCH
# ─────────────────────────────────────────────

def fetch_ticker_data(ticker: str) -> dict:
    """
    Pobiera dane analityczne dla jednego tickera z Yahoo Finance.

    Ważna uwaga dla spółek europejskich — Yahoo Finance wymaga sufiksu giełdy:
      Polska (GPW):    CDR.WA, PKN.WA, PKO.WA
      Niemcy (XETRA):  ALV.DE, SAP.DE, BMW.DE
      Francja:         MC.PA, AIR.PA
      Londyn:          SHEL.L, AZN.L
      Amsterdam:       ASML.AS, HEIA.AS
      Mediolan:        ENI.MI, ENEL.MI
    """
    try:
        t = yf.Ticker(ticker)
        info = t.info

        # Cena bieżąca
        current_price = (
            info.get("currentPrice")
            or info.get("regularMarketPrice")
            or info.get("previousClose")
        )

        # Price target
        target_mean = info.get("targetMeanPrice")
        target_low  = info.get("targetLowPrice")
        target_high = info.get("targetHighPrice")
        n_analysts  = info.get("numberOfAnalystOpinions")

        # Upside %
        upside = None
        if current_price and target_mean and current_price > 0:
            upside = round((target_mean - current_price) / current_price * 100, 1)

        # Breakdown rekomendacji (Strong Buy / Buy / Hold / Sell / Strong Sell)
        strong_buy = buy = hold = sell = strong_sell = ""
        try:
            rec_df = t.recommendations_summary
            if rec_df is not None and not rec_df.empty:
                latest = rec_df.iloc[0]
                strong_buy   = int(latest.get("strongBuy",   0))
                buy          = int(latest.get("buy",         0))
                hold         = int(latest.get("hold",        0))
                sell         = int(latest.get("sell",        0))
                strong_sell  = int(latest.get("strongSell",  0))
        except Exception:
            pass  # brak danych o rekomendacjach — pozostaw puste

        return {
            "current_price": _fmt(current_price),
            "target_mean":   _fmt(target_mean),
            "target_low":    _fmt(target_low),
            "target_high":   _fmt(target_high),
            "upside":        upside if upside is not None else "",
            "n_analysts":    n_analysts or "",
            "strong_buy":    strong_buy,
            "buy":           buy,
            "hold":          hold,
            "sell":          sell,
            "strong_sell":   strong_sell,
            "updated":       datetime.now().strftime("%Y-%m-%d %H:%M"),
            "status":        "OK",
        }

    except Exception as e:
        log.warning(f"  [{ticker}] Błąd: {e}")
        return {
            "current_price": "", "target_mean": "", "target_low": "",
            "target_high": "", "upside": "", "n_analysts": "",
            "strong_buy": "", "buy": "", "hold": "", "sell": "",
            "strong_sell": "", "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "status": f"ERROR: {e}",
        }


def _fmt(val):
    """Zaokrągla float do 2 miejsc lub zwraca pusty string."""
    if val is None:
        return ""
    try:
        return round(float(val), 2)
    except (TypeError, ValueError):
        return ""


# ─────────────────────────────────────────────
# GŁÓWNA LOGIKA AKTUALIZACJI
# ─────────────────────────────────────────────

def update_sheet():
    log.info("═" * 50)
    log.info(f"Start aktualizacji: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    try:
        ws = get_worksheet()
    except Exception as e:
        log.error(f"Nie można połączyć z Google Sheets: {e}")
        return

    # Wstaw / odśwież nagłówki
    ws.update(f"A1:{_col_letter(len(HEADERS))}1", [HEADERS])
    _format_header(ws)

    # Pobierz tickery z kolumny A (od DATA_START_ROW)
    all_values = ws.col_values(1)
    tickers = [v.strip() for v in all_values[DATA_START_ROW - 1:] if v.strip()]

    if not tickers:
        log.warning("Nie znaleziono żadnych tickerów w kolumnie A.")
        return

    log.info(f"Znaleziono {len(tickers)} tickerów: {', '.join(tickers)}")

    # Zbierz dane dla wszystkich tickerów
    rows = []
    for i, ticker in enumerate(tickers, start=1):
        log.info(f"  ({i}/{len(tickers)}) {ticker}...")
        data = fetch_ticker_data(ticker)
        rows.append([
            ticker,
            data["current_price"],
            data["target_mean"],
            data["target_low"],
            data["target_high"],
            data["upside"],
            data["n_analysts"],
            data["strong_buy"],
            data["buy"],
            data["hold"],
            data["sell"],
            data["strong_sell"],
            data["updated"],
            data["status"],
        ])
        time.sleep(REQUEST_DELAY)

    # Zapisz wszystko jednym batchem (wydajniejsze niż wiersz po wierszu)
    end_row = DATA_START_ROW + len(rows) - 1
    end_col = _col_letter(len(HEADERS))
    ws.update(f"A{DATA_START_ROW}:{end_col}{end_row}", rows)

    ok_count = sum(1 for r in rows if r[-1] == "OK")
    log.info(f"Gotowe. OK: {ok_count}/{len(rows)}")
    log.info("═" * 50)


def _col_letter(n: int) -> str:
    """Konwertuje numer kolumny (1-based) na literę (A, B, ..., Z, AA, ...)."""
    result = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def _format_header(ws):
    """Pogrubienie nagłówków i zamrożenie pierwszego wiersza."""
    try:
        ws.format("A1:N1", {
            "textFormat": {"bold": True},
            "backgroundColor": {"red": 0.2, "green": 0.2, "blue": 0.2},
        })
        ws.freeze(rows=1)
    except Exception:
        pass  # formatowanie opcjonalne, nie blokuj działania


# ─────────────────────────────────────────────
# URUCHOMIENIE
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if "--now" in sys.argv:
        # Tryb jednorazowy: python watchlist_updater.py --now
        update_sheet()
    else:
        # Tryb dzienny: uruchamia się automatycznie o RUN_AT_TIME
        log.info(f"Scheduler uruchomiony. Aktualizacja codziennie o {RUN_AT_TIME}.")
        log.info("Aby uruchomić teraz: python watchlist_updater.py --now")

        schedule.every().day.at(RUN_AT_TIME).do(update_sheet)

        # Uruchom raz od razu przy starcie
        update_sheet()

        while True:
            schedule.run_pending()
            time.sleep(60)
