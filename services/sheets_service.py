import os
import re
import time
import random
import socket
import threading
from datetime import datetime

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from services.gmail_service import credentials_from_row

LEGACY_HEADERS = [
    "MARKETING PERSON", "PRICE DATE", "IMAGE", "IMAGE LINK", "PRODUCT NAME",
    "MODEL", "SPECIFICATIONS", "PURCHASE PRICE", "PURCHASE OWNER", "SUPPLIER NAME",
]

# V21.4 two-price Sheet schema. The internal purchase_price remains the final
# discounted procurement unit price for backward compatibility. Google Sheets
# now exposes the requested Original Price and Discounted Price separately.
HEADERS = [
    "PRICE DATE", "PRODUCT NAME", "MODEL",
    "SPECIFICATIONS", "ORIGINAL PRICE", "DISCOUNTED PRICE", "SUPPLIER NAME",
]
SHEET_SCHEMA_VERSION = "V214_7COL_TWO_PRICE_R1"

# Google Sheets is deliberately isolated from procurement extraction. Temporary
# Google/network outages must never stop Gmail processing, and non-idempotent
# appends are reconciled before a retry to avoid duplicate product rows.
SHEETS_TRANSIENT_HTTP = {429, 500, 502, 503, 504}
SHEETS_API_MAX_ATTEMPTS = max(1, int(os.getenv("SHEETS_API_MAX_ATTEMPTS", "4")))
SHEETS_API_RETRY_BASE_SECONDS = max(0.1, float(os.getenv("SHEETS_API_RETRY_BASE_SECONDS", "1.0")))
SHEETS_API_RETRY_MAX_SECONDS = max(1.0, float(os.getenv("SHEETS_API_RETRY_MAX_SECONDS", "12.0")))


def _http_status(exc):
    try:
        return int(getattr(getattr(exc, "resp", None), "status", 0) or 0)
    except Exception:
        return 0


def is_transient_sheets_error(exc) -> bool:
    """True only for retry-safe temporary Google/network failures."""
    if isinstance(exc, HttpError):
        return _http_status(exc) in SHEETS_TRANSIENT_HTTP
    if isinstance(exc, (TimeoutError, ConnectionError, socket.timeout, OSError)):
        text = str(exc).lower()
        # Authentication/permission failures are not transient even when surfaced
        # through a generic transport exception.
        if any(x in text for x in ("invalid_grant", "unauthorized", "forbidden", "permission")):
            return False
        return True
    text = str(exc).lower()
    return any(x in text for x in (
        "http 429", "http 500", "http 502", "http 503", "http 504",
        "service unavailable", "temporarily unavailable", "timed out", "timeout",
        "connection reset", "connection aborted", "remote end closed connection",
    ))


def _retry_delay(attempt: int) -> float:
    # bounded exponential backoff + small jitter prevents synchronized retries
    # across Gmail tabs without making Sheet export hold the extraction engine.
    base = min(SHEETS_API_RETRY_MAX_SECONDS, SHEETS_API_RETRY_BASE_SECONDS * (2 ** max(0, attempt - 1)))
    return base + random.uniform(0.0, min(0.35, base * 0.15))


def _execute_retry(request, *, operation="Google Sheets request", attempts=None):
    """Execute an idempotent Sheets request with bounded transient retry."""
    attempts = max(1, int(attempts or SHEETS_API_MAX_ATTEMPTS))
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return request.execute()
        except Exception as exc:
            last = exc
            if not is_transient_sheets_error(exc) or attempt >= attempts:
                raise
            time.sleep(_retry_delay(attempt))
    raise last


_TAB_LOCKS = {}
_TAB_LOCKS_GUARD = threading.Lock()


def _tab_lock(spreadsheet_id, tab):
    key = (str(spreadsheet_id), str(tab))
    with _TAB_LOCKS_GUARD:
        lock = _TAB_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _TAB_LOCKS[key] = lock
        return lock


def _write_rows_at_tail_resilient(service, spreadsheet_id, tab, rows):
    """Idempotent batch append using an explicit A:G row range.

    Google values.append has an ambiguity window: a 503 can happen after Google
    committed the rows, so retrying append can duplicate procurement records.
    V21.4 instead reads the current tail row once and writes the entire batch to
    one explicit range with values.update. update is idempotent, therefore the
    exact same request can be retried safely after 429/5xx/timeouts.

    A per-tab in-process lock also prevents the background Sheet worker and a
    manual Export All request from racing each other on the same Gmail tab.
    """
    if not rows:
        return {"updatedRows": 0}
    quoted = tab.replace("'", "''")
    with _tab_lock(spreadsheet_id, tab):
        current = _execute_retry(
            service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range=f"'{quoted}'!A:G",
                majorDimension="ROWS",
            ),
            operation="Sheets tail lookup",
        ) or {}
        existing = current.get("values", []) or []
        start_row = max(2, len(existing) + 1)  # row 1 is the header
        end_row = start_row + len(rows) - 1
        req = service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{quoted}'!A{start_row}:G{end_row}",
            valueInputOption="USER_ENTERED",
            body={"values": rows},
        )
        result = _execute_retry(req, operation="Sheets product batch write") or {}
        result.setdefault("updatedRows", len(rows))
        result["startRow"] = start_row
        result["endRow"] = end_row
        return result


def normalize_sheet_id(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", value)
    return m.group(1) if m else value


def sheet_url(sheet_id: str) -> str:
    sheet_id = normalize_sheet_id(sheet_id)
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit" if sheet_id else ""


def _service(mailbox_row):
    return build("sheets", "v4", credentials=credentials_from_row(mailbox_row), cache_discovery=False)


def validate_tab_name(tab: str) -> str:
    tab = (tab or "").strip()
    if not tab:
        raise ValueError("Tab name cannot be empty")
    if len(tab) > 100:
        raise ValueError("Tab name must be 100 characters or less")
    if any(ch in tab for ch in "[]:*?/\\"):
        raise ValueError("Tab name cannot contain [ ] : * ? / or backslash")
    return tab


def _header_cells():
    return [{
        "userEnteredValue": {"stringValue": h},
        "userEnteredFormat": {
            "textFormat": {"bold": True},
            "borders": {"bottom": {"style": "SOLID", "color": {"red": 0, "green": 0, "blue": 0}}},
        },
    } for h in HEADERS]


def _sheet_spec(title):
    title = validate_tab_name(title)
    return {
        "properties": {"title": title, "gridProperties": {"frozenRowCount": 1}},
        "data": [{"startRow": 0, "startColumn": 0, "rowData": [{"values": _header_cells()}]}],
    }


def create_product_spreadsheet_fast(mailbox_row, tabs):
    """Create ONE spreadsheet and ALL Gmail tabs in a single Google API call."""
    tabs = [validate_tab_name(t) for t in tabs]
    if not tabs:
        tabs = ["PRODUCT_DATA"]
    email = (mailbox_row["email"] or "Mailbox").strip()
    title = f"NUNES Product Intelligence - {datetime.now().strftime('%Y-%m-%d')}"
    service = _service(mailbox_row)
    result = service.spreadsheets().create(
        body={"properties": {"title": title}, "sheets": [_sheet_spec(t) for t in tabs]},
        fields="spreadsheetId,spreadsheetUrl,sheets.properties(sheetId,title)",
    ).execute()
    mappings = {
        sh["properties"]["title"]: int(sh["properties"]["sheetId"])
        for sh in result.get("sheets", []) or []
    }
    sid = result["spreadsheetId"]
    return {"sheet_id": sid, "url": result.get("spreadsheetUrl") or sheet_url(sid), "title": title, "tabs": mappings}


def _sheet_meta(service, spreadsheet_id):
    req = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets.properties(sheetId,title,gridProperties)"
    )
    return _execute_retry(req, operation="Sheets metadata").get("sheets", [])


def _migrate_legacy_tab(service, spreadsheet_id, tab):
    """Repair legacy/R1 NUNES tabs into the precise two-price semantics.

    Old PURCHASE PRICE values were ordinary effective unit prices, so they become
    ORIGINAL PRICE. The earlier R1 seven-column migration is also repaired: when
    Original is blank and Discounted contains the only value, that value moves to
    Original. Rows that already contain both columns are preserved.
    """
    quoted = tab.replace("'", "''")
    data = _execute_retry(
        service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=f"'{quoted}'!A1:K"
        ),
        operation="Sheets legacy-tab read",
    ).get("values", []) or []
    if not data:
        return {"migrated": False, "rows": 0}
    header = [str(x).strip().upper() for x in data[0]]
    required = ("PRODUCT NAME", "SUPPLIER NAME")
    has_old_price = "PURCHASE PRICE" in header
    has_original = "ORIGINAL PRICE" in header
    has_discounted = "DISCOUNTED PRICE" in header
    if not all(x in header for x in required) or not (has_old_price or has_original or has_discounted):
        raise ValueError(
            f"Sheet tab '{tab}' has a custom layout. V21.4 did not modify it. "
            "Use a new/empty NUNES tab or restore the NUNES headers before connecting."
        )
    index = {name: i for i, name in enumerate(header)}

    def cell(row, heading):
        i = index.get(heading)
        return row[i] if i is not None and i < len(row) else ""

    converted = [HEADERS]
    changed = header[:len(HEADERS)] != HEADERS
    for old in data[1:]:
        original = cell(old, "ORIGINAL PRICE")
        discounted = cell(old, "DISCOUNTED PRICE")
        legacy_price = cell(old, "PURCHASE PRICE")
        if legacy_price and not original and not discounted:
            original = legacy_price
            changed = True
        elif discounted and not original:
            # R1 placed every one-price record in Discounted. With no Original
            # column value there is no two-price pair, so repair it as a single quote.
            original, discounted = discounted, ""
            changed = True
        converted.append([
            cell(old, "PRICE DATE"),
            cell(old, "PRODUCT NAME"),
            cell(old, "MODEL"),
            cell(old, "SPECIFICATIONS"),
            original,
            discounted,
            cell(old, "SUPPLIER NAME"),
        ])
    if not changed:
        return {"migrated": False, "rows": max(0, len(data)-1)}
    _execute_retry(
        service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id, range=f"'{quoted}'!A:K", body={}
        ), operation="Sheets legacy-tab clear"
    )
    _execute_retry(
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=f"'{quoted}'!A1",
            valueInputOption="USER_ENTERED", body={"values": converted}
        ), operation="Sheets legacy-tab rewrite"
    )
    return {"migrated": True, "rows": max(0, len(converted)-1)}

def ensure_tabs_bulk(mailbox_row, spreadsheet_id, tabs):
    """Ensure Gmail tabs, migrate legacy columns safely, then enforce V21.4 headers."""
    spreadsheet_id = normalize_sheet_id(spreadsheet_id)
    if not spreadsheet_id:
        raise ValueError("Google Sheet ID is empty")
    tabs = [validate_tab_name(t) for t in tabs]
    service = _service(mailbox_row)
    existing = _sheet_meta(service, spreadsheet_id)
    by_title = {s.get("properties", {}).get("title"): int(s.get("properties", {}).get("sheetId")) for s in existing}
    existing_before = set(by_title)
    requests = []
    # Add missing sheets. Headers/freeze are added for all sheets below.
    for tab in tabs:
        if tab not in by_title:
            requests.append({"addSheet": {"properties": {"title": tab, "gridProperties": {"frozenRowCount": 1}}}})
    if requests:
        # addSheet is not idempotent. On a transient response, reconcile metadata
        # before issuing another add request so a committed-but-unacknowledged
        # tab is never created twice.
        pending = list(requests)
        last_exc = None
        for attempt in range(1, SHEETS_API_MAX_ATTEMPTS + 1):
            try:
                service.spreadsheets().batchUpdate(
                    spreadsheetId=spreadsheet_id, body={"requests": pending}
                ).execute()
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                if not is_transient_sheets_error(exc):
                    raise
                if attempt < SHEETS_API_MAX_ATTEMPTS:
                    time.sleep(_retry_delay(attempt))
                existing_now = _sheet_meta(service, spreadsheet_id)
                titles_now = {x.get("properties", {}).get("title") for x in existing_now}
                pending = [r for r in pending if r.get("addSheet", {}).get("properties", {}).get("title") not in titles_now]
                if not pending:
                    last_exc = None
                    break
        if last_exc is not None:
            raise last_exc
        existing = _sheet_meta(service, spreadsheet_id)
        by_title = {s.get("properties", {}).get("title"): int(s.get("properties", {}).get("sheetId")) for s in existing}

    # Existing NUNES tabs may use 10 or 8 columns. Migrate rows by header name
    # before overwriting the header. Unknown custom layouts are never rewritten.
    for tab in tabs:
        if tab in existing_before:
            _migrate_legacy_tab(service, spreadsheet_id, tab)

    # One values batch writes all V21.4 headers. Default Google format is black
    # text / white background, intentionally no decorative colours.
    data = [{"range": f"'{tab}'!A1:G1", "values": [HEADERS]} for tab in tabs]
    if data:
        _execute_retry(
            service.spreadsheets().values().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"valueInputOption": "RAW", "data": data},
            ),
            operation="Sheets header update"
        )

    # Freeze first row + bold header in one batch request for all tabs.
    fmt = []
    for tab in tabs:
        sid = by_title[tab]
        fmt.extend([
            {"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": 1}}, "fields": "gridProperties.frozenRowCount"}},
            {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": len(HEADERS)}, "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}}, "fields": "userEnteredFormat.textFormat.bold"}},
        ])
    if fmt:
        _execute_retry(
            service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": fmt}),
            operation="Sheets formatting"
        )
    return {tab: by_title[tab] for tab in tabs}


def ensure_sheet_ready(mailbox_row, spreadsheet_id: str, tab: str = "PRODUCT_DATA"):
    tab = validate_tab_name(tab or "PRODUCT_DATA")
    ids = ensure_tabs_bulk(mailbox_row, spreadsheet_id, [tab])
    spreadsheet_id = normalize_sheet_id(spreadsheet_id)
    return {"sheet_id": spreadsheet_id, "tab": tab, "tab_id": ids[tab], "url": sheet_url(spreadsheet_id)}


def create_product_spreadsheet(mailbox_row, tab: str = "PRODUCT_DATA"):
    result = create_product_spreadsheet_fast(mailbox_row, [tab])
    return {"sheet_id": result["sheet_id"], "tab": tab, "tab_id": result["tabs"][tab], "url": result["url"], "title": result["title"]}


def _price_pair(product):
    original = product.get("original_price", "")
    final = product.get("purchase_price", "")
    try:
        discount = float(product.get("discount_percent")) if product.get("discount_percent") not in (None, "") else 0.0
    except Exception:
        discount = 0.0
    basis = str(product.get("price_basis") or "").upper()
    has_discount = bool(discount > 0 or "DISCOUNT" in basis)
    if not has_discount:
        try:
            if original not in (None, "") and final not in (None, "") and abs(float(original) - float(final)) > max(0.01, abs(float(final)) * 1e-6):
                has_discount = True
        except Exception:
            pass
    if has_discount:
        return original or "", final or ""
    return original or final or "", ""


def _product_row(product):
    original, discounted = _price_pair(product)
    return [
        product.get("price_date", ""),
        product.get("product_name", ""),
        product.get("model", ""),
        product.get("specifications", ""),
        original,
        discounted,
        product.get("supplier_name", ""),
    ]


def append_products_batch(mailbox_row, products, spreadsheet_id, tab):
    """Write many products to one Gmail tab as one idempotent batch range."""
    spreadsheet_id = normalize_sheet_id(spreadsheet_id)
    tab = validate_tab_name(tab)
    rows = [_product_row(p) for p in products]
    if not spreadsheet_id or not rows:
        return 0
    service = _service(mailbox_row)
    _write_rows_at_tail_resilient(service, spreadsheet_id, tab, rows)
    return len(rows)


def append_product(mailbox_row, product, spreadsheet_id=None, tab=None):
    spreadsheet_id = normalize_sheet_id(spreadsheet_id or os.getenv("GOOGLE_SHEET_ID", ""))
    tab = validate_tab_name(tab or os.getenv("GOOGLE_SHEET_TAB", "PRODUCT_DATA") or "PRODUCT_DATA")
    return bool(append_products_batch(mailbox_row, [product], spreadsheet_id, tab))


def rename_tab(mailbox_row, spreadsheet_id: str, old_tab: str, new_tab: str, tab_id=None):
    """Fast rename: stable sheetId uses one batchUpdate with no metadata lookup."""
    spreadsheet_id = normalize_sheet_id(spreadsheet_id)
    old_tab = validate_tab_name(old_tab)
    new_tab = validate_tab_name(new_tab)
    service = _service(mailbox_row)
    target_id = int(tab_id) if str(tab_id or "").strip() else None
    if target_id is None:
        for sh in _sheet_meta(service, spreadsheet_id):
            props = sh.get("properties", {})
            if props.get("title") == old_tab:
                target_id = int(props.get("sheetId")); break
    if target_id is None:
        raise ValueError(f"Could not find the assigned Google Sheet tab '{old_tab}'.")
    _execute_retry(
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"updateSheetProperties": {"properties": {"sheetId": target_id, "title": new_tab}, "fields": "title"}}]},
        ),
        operation="Sheets tab rename"
    )
    return {"sheet_id": spreadsheet_id, "tab": new_tab, "tab_id": target_id, "url": sheet_url(spreadsheet_id)}


def rebuild_tab_from_products(mailbox_row, products, spreadsheet_id, tab):
    """Replace a NUNES tab with exactly the current verified product set.

    This is used after an accuracy re-audit so legacy/wrong rows disappear instead
    of remaining beside corrected rows. The operation is header-aware and touches
    only the assigned NUNES tab.
    """
    spreadsheet_id = normalize_sheet_id(spreadsheet_id)
    tab = validate_tab_name(tab)
    if not spreadsheet_id:
        raise ValueError("Google Sheet is not configured")
    service = _service(mailbox_row)
    quoted = tab.replace("'", "''")
    rows = [HEADERS] + [_product_row(p) for p in products]
    _execute_retry(
        service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id,
            range=f"'{quoted}'!A:Z",
            body={},
        ), operation="Sheets rebuild clear"
    )
    # One update is materially faster and safer than row-by-row appends.
    _execute_retry(
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{quoted}'!A1",
            valueInputOption="USER_ENTERED",
            body={"values": rows},
        ), operation="Sheets rebuild write"
    )
    return max(0, len(rows) - 1)
