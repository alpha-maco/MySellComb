import os
import time
from pathlib import Path

import gspread
from oauth2client.service_account import ServiceAccountCredentials

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SPREADSHEET_NAME = "Reference1"
DEFAULT_CREDENTIALS_PATH = PROJECT_ROOT / "credentials.json"
DEFAULT_COLUMNS = ["name", "price", "link", "review", "rating", "source_url"]
TIKTOK_COLUMNS = [
    "keyword",
    "name",
    "link",
    "view_count",
    "like_count",
    "comment_count",
    "share_count",
    "author",
    "description",
    "publish_date",
    "collected_at",
    "source_url",
]


def _resolve_credentials_path() -> Path:
    raw_path = Path(str((os.environ.get("GOOGLE_SHEET_CREDENTIALS") or DEFAULT_CREDENTIALS_PATH)))
    return raw_path if raw_path.is_absolute() else (PROJECT_ROOT / raw_path).resolve()


def _resolve_spreadsheet_name() -> str:
    return os.environ.get("GOOGLE_SHEET_NAME", DEFAULT_SPREADSHEET_NAME)


def _is_tiktok_product(product: dict) -> bool:
    tiktok_keys = {"view_count", "like_count", "comment_count", "author", "publish_date", "keyword", "collected_at"}
    return any(key in product for key in tiktok_keys)


def _pick_columns(products: list[dict]) -> list[str]:
    if products and any(_is_tiktok_product(product) for product in products):
        return TIKTOK_COLUMNS
    return DEFAULT_COLUMNS


def _normalize_row(product: dict, columns: list[str], source_url: str):
    values = []
    for column in columns:
        if column == "source_url":
            values.append(source_url)
            continue
        values.append(product.get(column))
    return ["" if value is None else str(value) for value in values]


def _ensure_header(sheet, columns: list[str]):
    try:
        header = sheet.row_values(1)
    except Exception:
        header = []

    if header == columns:
        return

    if not header:
        sheet.append_row(columns)


def save_to_sheet(products, sheet_name, source_url):
    if not products:
        print("No rows to save.")
        return {"saved_count": 0, "sheet_name": sheet_name, "spreadsheet_name": _resolve_spreadsheet_name()}

    credentials_path = _resolve_credentials_path()
    if not credentials_path.exists():
        raise FileNotFoundError(f"Google Sheet credentials not found: {credentials_path}")

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]

    spreadsheet_name = _resolve_spreadsheet_name()
    columns = _pick_columns(products)
    rows = [_normalize_row(product, columns, source_url) for product in products]
    last_error = None

    for attempt in range(3):
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_name(str(credentials_path), scope)
            client = gspread.authorize(creds)
            spreadsheet = client.open(spreadsheet_name)

            try:
                sheet = spreadsheet.worksheet(sheet_name)
            except gspread.WorksheetNotFound:
                sheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=max(26, len(columns) + 4))

            _ensure_header(sheet, columns)
            sheet.append_rows(rows, value_input_option="USER_ENTERED")
            print(f"Saved {len(rows)} rows to Google Sheet '{spreadsheet_name}' / worksheet '{sheet_name}'.")
            return {
                "saved_count": len(rows),
                "sheet_name": sheet_name,
                "spreadsheet_name": spreadsheet_name,
            }
        except Exception as exc:
            last_error = exc
            print(f"Google Sheet save failed ({attempt + 1}/3): {exc}")
            time.sleep(2)

    raise last_error
