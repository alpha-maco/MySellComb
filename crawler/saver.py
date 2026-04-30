import os
import time
from datetime import datetime
from pathlib import Path

import gspread
from oauth2client.service_account import ServiceAccountCredentials

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SPREADSHEET_NAME = "Reference1"
DEFAULT_CREDENTIALS_PATH = PROJECT_ROOT / "credentials.json"
DEFAULT_COLUMNS = ["name", "price", "link", "review", "rating", "source_url"]
TIKTOK_COLUMNS = [
    "rownum",
    "saved_at",
    "keyword",
    "name",
    "link",
    "view_count",
    "like_count",
    "like_view_ratio (%)",
    "comment_count",
    "share_count",
    "author",
    "description",
    "publish_date",
    "collected_at",
]
LEGACY_TIKTOK_COLUMNS = [
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


def _parse_number(value) -> float:
    if value is None:
        return 0

    text = str(value).replace(",", "").strip().upper()
    try:
        if text.endswith("K"):
            return float(text[:-1]) * 1000
        if text.endswith("M"):
            return float(text[:-1]) * 1000000
        if text.endswith("B"):
            return float(text[:-1]) * 1000000000
        return float(text)
    except Exception:
        return 0


def _sort_tiktok_products(products: list[dict]) -> list[dict]:
    return sorted(
        products,
        key=lambda product: _parse_number(product.get("view_count")),
        reverse=True,
    )


def _calculate_like_view_ratio(product: dict) -> str:
    views = _parse_number(product.get("view_count"))
    likes = _parse_number(product.get("like_count"))
    if views <= 0:
        return "0"
    return str(int((likes / views) * 100))


def _pad_row(row: list[str], size: int) -> list[str]:
    values = list(row[:size])
    if len(values) < size:
        values.extend([""] * (size - len(values)))
    return values


def _looks_like_tiktok_video_url(value: str) -> bool:
    text = str(value or "").strip().lower()
    return text.startswith("https://www.tiktok.com/") and "/video/" in text


def _looks_like_saved_at(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    normalized = text.replace(" UTC", "")
    try:
        datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
        return True
    except Exception:
        return False


def _normalize_saved_at(value: str, fallback: str = "") -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    return text.replace(" UTC", "")


def _extract_tiktok_row_data(row: list[str]) -> dict | None:
    current = _pad_row(row, len(TIKTOK_COLUMNS))
    legacy = _pad_row(row, len(LEGACY_TIKTOK_COLUMNS))

    if _looks_like_tiktok_video_url(current[4]) or _looks_like_saved_at(current[1]) or str(current[0]).strip().isdigit():
        return {
            "saved_at": _normalize_saved_at(current[1], _normalize_saved_at(current[13])),
            "keyword": current[2],
            "name": current[3],
            "link": current[4],
            "view_count": current[5],
            "like_count": current[6],
            "comment_count": current[8],
            "share_count": current[9],
            "author": current[10],
            "description": current[11],
            "publish_date": current[12],
            "collected_at": current[13],
        }

    if _looks_like_tiktok_video_url(legacy[2]):
        return {
            "saved_at": _normalize_saved_at(legacy[10]),
            "keyword": legacy[0],
            "name": legacy[1],
            "link": legacy[2],
            "view_count": legacy[3],
            "like_count": legacy[4],
            "comment_count": legacy[5],
            "share_count": legacy[6],
            "author": legacy[7],
            "description": legacy[8],
            "publish_date": legacy[9],
            "collected_at": legacy[10],
        }

    return None


def _canonical_tiktok_row(row_data: dict, rownum: int) -> list[str]:
    product = {
        "view_count": row_data.get("view_count"),
        "like_count": row_data.get("like_count"),
    }
    return [
        str(rownum),
        _normalize_saved_at(row_data.get("saved_at"), _normalize_saved_at(row_data.get("collected_at"))),
        "" if row_data.get("keyword") is None else str(row_data.get("keyword")),
        "" if row_data.get("name") is None else str(row_data.get("name")),
        "" if row_data.get("link") is None else str(row_data.get("link")),
        "" if row_data.get("view_count") is None else str(row_data.get("view_count")),
        "" if row_data.get("like_count") is None else str(row_data.get("like_count")),
        _calculate_like_view_ratio(product),
        "" if row_data.get("comment_count") is None else str(row_data.get("comment_count")),
        "" if row_data.get("share_count") is None else str(row_data.get("share_count")),
        "" if row_data.get("author") is None else str(row_data.get("author")),
        "" if row_data.get("description") is None else str(row_data.get("description")),
        "" if row_data.get("publish_date") is None else str(row_data.get("publish_date")),
        "" if row_data.get("collected_at") is None else str(row_data.get("collected_at")),
    ]


def _repair_tiktok_sheet_layout(sheet) -> int:
    existing_values = sheet.get_all_values()
    if not existing_values:
        return 0

    header = existing_values[0]
    body = existing_values[1:]
    repaired_rows: list[list[str]] = []
    changed = header != TIKTOK_COLUMNS

    for row in body:
        if not any(str(cell).strip() for cell in row):
            continue

        row_data = _extract_tiktok_row_data(row)
        if not row_data:
            continue

        canonical_row = _canonical_tiktok_row(row_data, len(repaired_rows) + 1)
        repaired_rows.append(canonical_row)
        if canonical_row != _pad_row(row, len(TIKTOK_COLUMNS)):
            changed = True

    if not changed:
        return 0

    sheet.clear()
    sheet.append_rows([TIKTOK_COLUMNS, *repaired_rows], value_input_option="USER_ENTERED")
    return len(repaired_rows)


def _extract_saved_date(value: str) -> str:
    normalized = _normalize_saved_at(value)
    return normalized[:10] if len(normalized) >= 10 else ""


def _extract_tiktok_keyword(row: list[str]) -> str:
    if len(row) <= 2:
        return ""
    return str(row[2] or "").strip()


def _next_tiktok_rownum(existing_rows: list[list[str]]) -> int:
    numeric_values: list[int] = []
    for row in existing_rows:
        if not row:
            continue
        first = str(row[0]).strip() if len(row) > 0 else ""
        if first.isdigit():
            numeric_values.append(int(first))
    return (max(numeric_values) + 1) if numeric_values else 1


def _build_tiktok_rows(products: list[dict], start_rownum: int, saved_at: str) -> list[list[str]]:
    rows: list[list[str]] = []

    for offset, product in enumerate(_sort_tiktok_products(products)):
        rows.append(
            [
                str(start_rownum + offset),
                saved_at,
                "" if product.get("keyword") is None else str(product.get("keyword")),
                "" if product.get("name") is None else str(product.get("name")),
                "" if product.get("link") is None else str(product.get("link")),
                "" if product.get("view_count") is None else str(product.get("view_count")),
                "" if product.get("like_count") is None else str(product.get("like_count")),
                _calculate_like_view_ratio(product),
                "" if product.get("comment_count") is None else str(product.get("comment_count")),
                "" if product.get("share_count") is None else str(product.get("share_count")),
                "" if product.get("author") is None else str(product.get("author")),
                "" if product.get("description") is None else str(product.get("description")),
                "" if product.get("publish_date") is None else str(product.get("publish_date")),
                "" if product.get("collected_at") is None else str(product.get("collected_at")),
            ]
        )

    return rows


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

            if columns == TIKTOK_COLUMNS:
                _ensure_header(sheet, columns)
                save_mode = str(products[0].get("_save_mode", "overwrite")).strip().lower() if products else "overwrite"
                save_keyword = str(products[0].get("keyword", "")).strip() if products else ""
                saved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                saved_date = _extract_saved_date(saved_at)
                existing_values = sheet.get_all_values()
                existing_body = existing_values[1:] if len(existing_values) > 1 else []

                if save_mode == "append":
                    start_rownum = _next_tiktok_rownum(existing_body)
                    rows = _build_tiktok_rows(products, start_rownum, saved_at)
                    sheet.append_rows(rows, value_input_option="USER_ENTERED")
                else:
                    keep_rows = [
                        row for row in existing_body
                        if not (
                            _extract_saved_date(row[1] if len(row) > 1 else "") == saved_date
                            and _extract_tiktok_keyword(row) == save_keyword
                        )
                    ]
                    start_rownum = _next_tiktok_rownum(keep_rows)
                    rows = _build_tiktok_rows(products, start_rownum, saved_at)
                    sheet.clear()
                    sheet.append_rows([columns, *keep_rows, *rows], value_input_option="USER_ENTERED")
            else:
                _ensure_header(sheet, columns)
                rows = [_normalize_row(product, columns, source_url) for product in products]
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
