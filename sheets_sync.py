import json
import logging
import os
from datetime import datetime

log = logging.getLogger(__name__)

SERVICE_ACCOUNT_FILE = "service_account.json"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADERS = [
    "Ticker", "Current Price", "Price Target (mean)", "Price Target (low)",
    "Price Target (high)", "Upside %", "# Analysts", "Strong Buy", "Buy",
    "Hold", "Sell", "Strong Sell", "Updated", "Status",
]


def _ensure_service_account_file():
    if os.path.exists(SERVICE_ACCOUNT_FILE):
        return True
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if sa_json:
        with open(SERVICE_ACCOUNT_FILE, "w") as f:
            f.write(sa_json)
        return True
    return False


def is_sheets_configured():
    sheet_id = os.environ.get("SHEET_ID", "")
    if not sheet_id or sheet_id == "TWÓJ_GOOGLE_SHEET_ID":
        return False
    return _ensure_service_account_file()


def _get_worksheet():
    from google.oauth2.service_account import Credentials
    import gspread

    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet_id = os.environ.get("SHEET_ID")
    worksheet_name = os.environ.get("WORKSHEET_NAME", "Sheet1")
    return client.open_by_key(sheet_id).worksheet(worksheet_name)


def _col_letter(n: int) -> str:
    result = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def sync_to_sheets(results: list[dict]):
    if not is_sheets_configured():
        return

    try:
        ws = _get_worksheet()

        ws.update(f"A1:{_col_letter(len(HEADERS))}1", [HEADERS])
        try:
            ws.format("A1:N1", {
                "textFormat": {"bold": True},
                "backgroundColor": {"red": 0.2, "green": 0.2, "blue": 0.2},
            })
            ws.freeze(rows=1)
        except Exception:
            pass

        rows = []
        for data in results:
            rows.append([
                data["ticker"],
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

        start_row = 2
        end_row = start_row + len(rows) - 1
        end_col = _col_letter(len(HEADERS))
        ws.update(f"A{start_row}:{end_col}{end_row}", rows)
        log.info(f"Synced {len(rows)} rows to Google Sheets.")
    except Exception as e:
        log.warning(f"Sheets sync error: {e}")


def import_tickers_from_sheet() -> list[str]:
    if not is_sheets_configured():
        return []

    try:
        ws = _get_worksheet()
        all_values = ws.col_values(1)
        tickers = [v.strip() for v in all_values[1:] if v.strip()]
        log.info(f"Imported {len(tickers)} tickers from Google Sheets.")
        return tickers
    except Exception as e:
        log.warning(f"Could not import from Sheets: {e}")
        return []
