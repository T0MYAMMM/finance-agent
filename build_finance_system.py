#!/usr/bin/env python3
"""
build_finance_system.py — one-shot builder for the Personal Finance system.

Creates (or reuses) the Google Drive folder tree and the "Personal Finance
Tracker" Google Sheet with all tabs, reference data, data validation,
conditional formatting, and formula-driven Monthly Summary + Dashboard.

Idempotent: it reuses an existing "Personal Finance" folder and an existing
"Personal Finance Tracker" spreadsheet instead of duplicating them.

Requires Google OAuth to be set up first (the google-workspace skill's
setup.py). Run `python build_finance_system.py --check` to verify auth.

Usage:
    python build_finance_system.py            # build everything
    python build_finance_system.py --check    # verify OAuth only
"""

import argparse
import json
import sys
import os
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Locate the google-workspace skill's scripts dir and reuse its auth helpers.
# ---------------------------------------------------------------------------
HOME = Path.home()
_SKILL_CANDIDATES = [
    HOME / ".hermes" / "skills" / "productivity" / "google-workspace" / "scripts",
    HOME / ".hermes" / "hermes-agent" / "skills" / "productivity" / "google-workspace" / "scripts",
]

_skill_dir = next((p for p in _SKILL_CANDIDATES if p.exists()), None)
if _skill_dir is None:
    sys.stderr.write(
        "ERROR: google-workspace skill scripts not found.\n"
        "Expected under ~/.hermes/skills/productivity/google-workspace/scripts\n"
    )
    sys.exit(2)

sys.path.insert(0, str(_skill_dir))
import google_api  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ROOT_FOLDER_NAME = "Personal Finance"
SHEET_TITLE = "Personal Finance Tracker"

TAB_ORDER = [
    "Transactions",
    "Categories",
    "Accounts",
    "Payment Methods",
    "Monthly Summary",
    "Dashboard",
    "Configuration",
]

TRANSACTION_HEADERS = [
    "transaction_id",
    "transaction_date",
    "transaction_type",
    "description",
    "merchant",
    "category",
    "subcategory",
    "amount",
    "currency",
    "payment_method",
    "account",
    "receipt_link",
    "receipt_file_id",
    "receipt_filename",
    "has_receipt",
    "duplicate_status",
    "reconciliation_status",
    "reconciled_at",
    "source",
    "notes",
    "created_at",
    "updated_at",
]

TRANSACTION_TYPES = ["Expense", "Income", "Transfer", "Refund"]
CURRENCIES = ["IDR", "USD", "SGD", "EUR", "MYR"]
RECONCILIATION_STATUSES = ["Pending", "Reconciled", "Flagged"]
SOURCES = ["Manual", "Receipt", "Import"]

# Categories: (type, category, subcategory examples, active)
CATEGORIES = [
    ("Expense", "Food & Dining", "Restaurant, Groceries, Coffee", True),
    ("Expense", "Transportation", "Fuel, Ride-hailing, Public Transit, Parking", True),
    ("Expense", "Housing", "Rent, Maintenance", True),
    ("Expense", "Utilities", "Electricity, Water, Internet, Phone", True),
    ("Expense", "Shopping", "", True),
    ("Expense", "Health", "Pharmacy, Doctor, Insurance", True),
    ("Expense", "Entertainment", "", True),
    ("Expense", "Education", "", True),
    ("Expense", "Travel", "", True),
    ("Expense", "Subscriptions", "", True),
    ("Expense", "Personal Care", "", True),
    ("Expense", "Gifts", "", True),
    ("Expense", "Other", "", True),
    ("Income", "Salary", "", True),
    ("Income", "Freelance", "", True),
    ("Income", "Business", "", True),
    ("Income", "Investment", "", True),
    ("Income", "Refund", "", True),
    ("Income", "Other", "", True),
]

ACCOUNTS = [
    ("BCA", "Bank", "BCA", "IDR", True),
    ("Mandiri", "Bank", "Mandiri", "IDR", True),
    ("Cash", "Cash", "", "IDR", True),
    ("GoPay", "E-wallet", "Gojek", "IDR", True),
    ("OVO", "E-wallet", "OVO", "IDR", True),
    ("DANA", "E-wallet", "DANA", "IDR", True),
    ("Credit Card", "Credit Card", "BCA", "IDR", True),
]

PAYMENT_METHODS = [
    "BCA Debit",
    "BCA Credit Card",
    "Cash",
    "GoPay",
    "OVO",
    "DANA",
    "QRIS",
    "Bank Transfer",
]

CONFIG = [
    ("base_currency", "IDR"),
    ("default_account", "BCA"),
    ("default_payment_method", "BCA Debit"),
    ("currency_list", "IDR, USD, SGD, EUR, MYR"),
    ("receipt_root_folder", "Personal Finance/Receipts"),
    ("month_format", "yyyy-mm"),
    ("year", str(datetime.now().year)),
]

# Number of transaction rows to provision (validation + formulas).
TXN_ROWS = 1000

# Column letters for the Transactions tab (1-indexed).
def col(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _services():
    creds = google_api.get_credentials()
    drive = google_api.build_service("drive", "v3")
    sheets = google_api.build_service("sheets", "v4")
    return drive, sheets, creds


# ---------------------------------------------------------------------------
# Drive helpers
# ---------------------------------------------------------------------------
def find_folder(drive, name, parent_id=None):
    q = f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent_id:
        q += f" and '{parent_id}' in parents"
    res = drive.files().list(q=q, spaces="drive", fields="files(id, name)", pageSize=50).execute()
    return res.get("files", [])


def create_folder(drive, name, parent_id=None):
    meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        meta["parents"] = [parent_id]
    return drive.files().create(body=meta, fields="id, name").execute()


def find_or_create_folder(drive, name, parent_id=None):
    existing = find_folder(drive, name, parent_id)
    if existing:
        return existing[0]["id"]
    return create_folder(drive, name, parent_id)["id"]


def find_spreadsheet(drive, title):
    q = f"name = '{title}' and mimeType = 'application/vnd.google-sheets.spreadsheet' and trashed = false"
    res = drive.files().list(
        q=q, spaces="drive", fields="files(id, name, modifiedTime)",
        pageSize=50, orderBy="modifiedTime desc",
    ).execute()
    return res.get("files", [])


def build_drive_tree(drive):
    root = find_or_create_folder(drive, ROOT_FOLDER_NAME)
    receipts = find_or_create_folder(drive, "Receipts", root)
    statements = find_or_create_folder(drive, "Statements", root)
    income = find_or_create_folder(drive, "Income", root)
    other = find_or_create_folder(drive, "Other", root)

    year = str(datetime.now().year)
    year_folder = find_or_create_folder(drive, year, receipts)
    for m in range(1, 13):
        find_or_create_folder(drive, f"{m:02d}-{datetime(2000, m, 1).strftime('%B')}", year_folder)

    return {
        "root": root,
        "receipts": receipts,
        "statements": statements,
        "income": income,
        "other": other,
        "year": year_folder,
    }


# ---------------------------------------------------------------------------
# Sheets helpers
# ---------------------------------------------------------------------------
def write_range(sheets, ss_id, sheet, a1, rows):
    body = {"values": rows}
    sheets.spreadsheets().values().update(
        spreadsheetId=ss_id,
        range=f"'{sheet}'!{a1}",
        valueInputOption="USER_ENTERED",
        body=body,
    ).execute()


def get_sheet_ids(sheets, ss_id):
    meta = sheets.spreadsheets().get(spreadsheetId=ss_id).execute()
    return {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}


def build_sheet_tabs(sheets, ss_id):
    ids = get_sheet_ids(sheets, ss_id)
    requests = []

    # If the default Sheet1 is present and "Transactions" isn't, produce
    # "Transactions" by renaming Sheet1 rather than adding a duplicate.
    rename_sheet1 = "Sheet1" in ids and "Transactions" not in ids

    if rename_sheet1:
        requests.append({
            "updateSheetProperties": {
                "properties": {"sheetId": ids["Sheet1"], "title": "Transactions"},
                "fields": "title",
            }
        })

    for name in TAB_ORDER:
        if name == "Transactions" and rename_sheet1:
            continue  # already produced via the rename above
        if name not in ids and name != "Sheet1":
            requests.append({"addSheet": {"properties": {"title": name}}})

    if requests:
        sheets.spreadsheets().batchUpdate(spreadsheetId=ss_id, body={"requests": requests}).execute()

    return get_sheet_ids(sheets, ss_id)


def populate_transactions(sheets, ss_id, sheet_id):
    write_range(sheets, ss_id, "Transactions", "A1", [TRANSACTION_HEADERS])

    # has_receipt formula column (O), rows 2..TXN_ROWS+1
    formulas = [
        [f'=IF(OR($L{r}<>"",$M{r}<>""),TRUE,FALSE)'] for r in range(2, TXN_ROWS + 2)
    ]
    write_range(sheets, ss_id, "Transactions", f"O2", formulas)


def populate_references(sheets, ss_id):
    cat_rows = [["type", "category", "subcategories (examples)", "active"]]
    cat_rows += [[t, c, s, "TRUE" if a else "FALSE"] for (t, c, s, a) in CATEGORIES]
    write_range(sheets, ss_id, "Categories", "A1", cat_rows)

    acc_rows = [["account_name", "account_type", "institution", "currency", "active"]]
    acc_rows += [[n, t, i, cur, "TRUE" if a else "FALSE"] for (n, t, i, cur, a) in ACCOUNTS]
    write_range(sheets, ss_id, "Accounts", "A1", acc_rows)

    pm_rows = [["payment_method", "active"]]
    pm_rows += [[m, "TRUE"] for m in PAYMENT_METHODS]
    write_range(sheets, ss_id, "Payment Methods", "A1", pm_rows)

    cfg_rows = [["key", "value"]]
    cfg_rows += [[k, v] for (k, v) in CONFIG]
    write_range(sheets, ss_id, "Configuration", "A1", cfg_rows)


def populate_monthly_summary(sheets, ss_id):
    year = datetime.now().year
    headers = ["Month", "Income", "Expenses", "Net Cash Flow", "Txns", "Reconciled"]
    write_range(sheets, ss_id, "Monthly Summary", "A1", [headers])

    rows = []
    for m in range(1, 13):
        month_start = datetime(year, m, 1)
        a = month_start.strftime("%Y-%m-%d")  # written as date (USER_ENTERED)
        b = f'=SUMIFS(Transactions!$H:$H,Transactions!$C:$C,"Income",Transactions!$B:$B,">="&$A{m+1},Transactions!$B:$B,"<"&EDATE($A{m+1},1))'
        c = f'=SUMIFS(Transactions!$H:$H,Transactions!$C:$C,"Expense",Transactions!$B:$B,">="&$A{m+1},Transactions!$B:$B,"<"&EDATE($A{m+1},1))'
        d = f'=IFERROR($B{m+1}-$C{m+1},"")'
        e = f'=COUNTIFS(Transactions!$B:$B,">="&$A{m+1},Transactions!$B:$B,"<"&EDATE($A{m+1},1))'
        rows.append([a, b, c, d, e, ""])
    write_range(sheets, ss_id, "Monthly Summary", "A2", rows)

    # Category spending block.
    base = 16  # leave a gap
    write_range(sheets, ss_id, "Monthly Summary", f"A{base}", [["Category Spending (select month):", ""]])
    write_range(sheets, ss_id, "Monthly Summary", f"A{base+1}", [["Month:", "Select month below"]])
    write_range(sheets, ss_id, "Monthly Summary", f"A{base+2}", [["Category", "Amount"]])

    expense_categories = [c for (t, c, s, a) in CATEGORIES if t == "Expense"]
    cat_rows = []
    for i, cat in enumerate(expense_categories):
        r = base + 3 + i
        amount = (
            f'=SUMIFS(Transactions!$H:$H,Transactions!$C:$C,"Expense",'
            f'Transactions!$F:$F,$A{r},Transactions!$B:$B,">="&$B{base+1},'
            f'Transactions!$B:$B,"<"&EDATE($B{base+1},1))'
        )
        cat_rows.append([cat, amount])
    write_range(sheets, ss_id, "Monthly Summary", f"A{base+3}", cat_rows)


def populate_dashboard(sheets, ss_id):
    labels = [
        ["PERSONAL FINANCE DASHBOARD", ""],
        ["", ""],
        ["YTD Income", '=SUMIFS(Transactions!$H:$H,Transactions!$C:$C,"Income")'],
        ["YTD Expenses", '=SUMIFS(Transactions!$H:$H,Transactions!$C:$C,"Expense")'],
        ["YTD Net Cash Flow", "=B3-B4"],
        ["", ""],
        ["This Month Income", '=SUMIFS(Transactions!$H:$H,Transactions!$C:$C,"Income",Transactions!$B:$B,">="&EOMONTH(TODAY(),-1)+1,Transactions!$B:$B,"<"&EOMONTH(TODAY(),0)+1)'],
        ["This Month Expenses", '=SUMIFS(Transactions!$H:$H,Transactions!$C:$C,"Expense",Transactions!$B:$B,">="&EOMONTH(TODAY(),-1)+1,Transactions!$B:$B,"<"&EOMONTH(TODAY(),0)+1)'],
        ["This Month Net", "=B7-B8"],
        ["", ""],
        ["Expenses Missing Receipts", '=COUNTIFS(Transactions!$C:$C,"Expense",Transactions!$O:$O,FALSE)'],
        ["", ""],
        ["Top 5 Expense Categories", ""],
    ]
    write_range(sheets, ss_id, "Dashboard", "A1", labels)

    top_cat = (
        '=QUERY(Transactions!A1:V,"select F, sum(H) where C = \'Expense\' '
        'group by F order by sum(H) desc limit 5 '
        'label F \'Category\', sum(H) \'Spend\'",1)'
    )
    write_range(sheets, ss_id, "Dashboard", "A15", [[top_cat]])

    recent = (
        '=QUERY(Transactions!A1:V,"select A, B, E, F, H, C where B is not null '
        'order by B desc limit 10 '
        'label A \'ID\', B \'Date\', E \'Merchant\', F \'Category\', H \'Amount\', C \'Type\'",1)'
    )
    write_range(sheets, ss_id, "Dashboard", "A24", [[recent]])

    missing = (
        '=QUERY(Transactions!A1:V,"select A, B, E, F, H where C = \'Expense\' and O = FALSE '
        'order by B desc '
        'label A \'ID\', B \'Date\', E \'Merchant\', F \'Category\', H \'Amount\'",1)'
    )
    write_range(sheets, ss_id, "Dashboard", "A40", [[missing]])


def _val_list(condition_type, values):
    return {"condition": {"type": condition_type, "values": [{"userEnteredValue": v} for v in values]},
            "strict": True, "showCustomUi": True}


def _val_range(source_range):
    if not source_range.startswith("="):
        source_range = "=" + source_range
    return {"condition": {"type": "ONE_OF_RANGE", "values": [{"userEnteredValue": source_range}]},
            "strict": True, "showCustomUi": True}


def apply_validation_and_formatting(sheets, ss_id):
    ids = get_sheet_ids(sheets, ss_id)
    tx = ids["Transactions"]

    requests = []

    def dv(sheet_id, start_col, end_col, rule):
        requests.append({
            "setDataValidation": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": TXN_ROWS + 1,
                    "startColumnIndex": start_col,
                    "endColumnIndex": end_col,
                },
                "rule": rule,
            }
        })

    # Column indexes (0-based) in Transactions:
    # A=0 B=1 C=2 D=3 E=4 F=5 G=6 H=7 I=8 J=9 K=10 L=11 M=12 N=13 O=14
    # P=15 Q=16 R=17 S=18 T=19 U=20 V=21
    dv(tx, 2, 3, _val_list("ONE_OF_LIST", TRANSACTION_TYPES))           # transaction_type
    dv(tx, 5, 6, _val_range("Categories!B2:B100"))                      # category
    dv(tx, 8, 9, _val_list("ONE_OF_LIST", CURRENCIES))                  # currency
    dv(tx, 9, 10, _val_range("'Payment Methods'!A2:A50"))               # payment_method
    dv(tx, 10, 11, _val_range("Accounts!A2:A50"))                       # account
    dv(tx, 16, 17, _val_list("ONE_OF_LIST", RECONCILIATION_STATUSES))   # reconciliation_status
    dv(tx, 18, 19, _val_list("ONE_OF_LIST", SOURCES))                   # source
    dv(tx, 7, 8, {"condition": {"type": "NUMBER_GREATER", "values": [{"userEnteredValue": "0"}]},
                  "strict": True, "showCustomUi": True})                # amount > 0

    # Monthly Summary month dropdown (for category-spend selector).
    ms = ids["Monthly Summary"]
    requests.append({
        "setDataValidation": {
            "range": {"sheetId": ms, "startRowIndex": 16, "endRowIndex": 17,
                      "startColumnIndex": 1, "endColumnIndex": 2},
            "rule": _val_range("'Monthly Summary'!A2:A13"),
        }
    })

    # Number / date formats.
    def fmt(sheet_id, start_col, end_col, pattern, type_):
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": TXN_ROWS + 1,
                    "startColumnIndex": start_col,
                    "endColumnIndex": end_col,
                },
                "cell": {"userEnteredFormat": {"numberFormat": {"type": type_, "pattern": pattern}}},
                "fields": "userEnteredFormat.numberFormat",
            }
        })

    fmt(tx, 1, 2, "yyyy-mm-dd", "DATE")       # transaction_date
    fmt(tx, 7, 8, "#,##0", "NUMBER")          # amount
    fmt(tx, 17, 18, "yyyy-mm-dd", "DATE")     # reconciled_at
    fmt(tx, 20, 21, "yyyy-mm-dd hh:mm", "DATE_TIME")  # created_at
    fmt(tx, 21, 22, "yyyy-mm-dd hh:mm", "DATE_TIME")  # updated_at

    # Monthly Summary amount + date formats.
    requests.append({
        "repeatCell": {
            "range": {"sheetId": ms, "startRowIndex": 0, "endRowIndex": 13,
                      "startColumnIndex": 0, "endColumnIndex": 1},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm"}}},
            "fields": "userEnteredFormat.numberFormat",
        }
    })
    for col_idx in (1, 2, 3):  # Income, Expenses, Net
        requests.append({
            "repeatCell": {
                "range": {"sheetId": ms, "startRowIndex": 1, "endRowIndex": 13,
                          "startColumnIndex": col_idx, "endColumnIndex": col_idx + 1},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
                "fields": "userEnteredFormat.numberFormat",
            }
        })

    # Freeze header row on Transactions.
    requests.append({
        "updateSheetProperties": {
            "properties": {"sheetId": tx, "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount",
        }
    })

    # Conditional formatting: duplicate flag (whole row yellow).
    requests.append({
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [{"sheetId": tx, "startRowIndex": 1, "endRowIndex": TXN_ROWS + 1,
                            "startColumnIndex": 0, "endColumnIndex": 22}],
                "booleanRule": {
                    "condition": {"type": "CUSTOM_FORMULA",
                                  "values": [{"userEnteredValue": '=$P2="POSSIBLE_DUPLICATE"'}]},
                    "format": {"backgroundColor": {"red": 1.0, "green": 0.95, "blue": 0.75}},
                },
            }
        }
    })

    # Conditional formatting: expense missing receipt (receipt columns red tint).
    requests.append({
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [{"sheetId": tx, "startRowIndex": 1, "endRowIndex": TXN_ROWS + 1,
                            "startColumnIndex": 11, "endColumnIndex": 14}],
                "booleanRule": {
                    "condition": {"type": "CUSTOM_FORMULA",
                                  "values": [{"userEnteredValue": '=AND($C2="Expense",$O2=FALSE)'}]},
                    "format": {"backgroundColor": {"red": 1.0, "green": 0.88, "blue": 0.88}},
                },
            }
        }
    })

    # Column widths (Transactions).
    widths = {0: 160, 1: 110, 2: 90, 4: 180, 5: 120, 7: 100, 8: 80, 9: 120,
              11: 220, 12: 200, 19: 200}
    for col_idx, px in widths.items():
        requests.append({
            "updateDimensionProperties": {
                "range": {"sheetId": tx, "dimension": "COLUMNS",
                          "startIndex": col_idx, "endIndex": col_idx + 1},
                "properties": {"pixelSize": px},
                "fields": "pixelSize",
            }
        })

    sheets.spreadsheets().batchUpdate(spreadsheetId=ss_id, body={"requests": requests}).execute()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Only verify OAuth auth status")
    parser.add_argument("--spreadsheet-id", help="Target a specific existing spreadsheet ID")
    args = parser.parse_args()

    if args.check:
        try:
            _services()
            print("AUTHENTICATED — Google OAuth is ready.")
            return
        except SystemExit as e:
            print("NOT_AUTHENTICATED — run the google-workspace setup.py first.")
            sys.exit(e.code or 1)

    print("Authenticating…")
    drive, sheets, creds = _services()

    print("Building Drive folder tree…")
    folders = build_drive_tree(drive)
    print(f"  Root folder: https://drive.google.com/drive/folders/{folders['root']}")

    existing = find_spreadsheet(drive, SHEET_TITLE)
    if args.spreadsheet_id:
        ss_id = args.spreadsheet_id
        print(f"Using existing spreadsheet: {SHEET_TITLE} ({ss_id})")
    elif existing:
        ss_id = existing[0]["id"]
        print(f"Reusing existing spreadsheet: {SHEET_TITLE} ({ss_id})")
    else:
        res = sheets.spreadsheets().create(body={"properties": {"title": SHEET_TITLE}}).execute()
        ss_id = res["spreadsheetId"]
        print(f"Created spreadsheet: {SHEET_TITLE} ({ss_id})")

    print("Building sheet tabs…")
    build_sheet_tabs(sheets, ss_id)

    print("Populating Transactions, references, Monthly Summary, Dashboard…")
    populate_transactions(sheets, ss_id, None)
    populate_references(sheets, ss_id)
    populate_monthly_summary(sheets, ss_id)
    populate_dashboard(sheets, ss_id)

    print("Applying data validation, conditional formatting, formats…")
    apply_validation_and_formatting(sheets, ss_id)

    url = f"https://docs.google.com/spreadsheets/d/{ss_id}"
    print("\nDone. Summary:")
    print(f"  Spreadsheet: {url}")
    print(f"  Drive root:  https://drive.google.com/drive/folders/{folders['root']}")

    cfg_path = Path(__file__).resolve().parent / "config.json"
    cfg_path.write_text(json.dumps({
        "spreadsheet_id": ss_id,
        "spreadsheet_url": url,
        "root_folder_id": folders["root"],
        "receipts_folder_id": folders["receipts"],
        "year_folder_id": folders["year"],
    }, indent=2))
    print(f"Saved config: {cfg_path}")
    print("\nNext: use finance.py to record transactions and upload receipts.")


if __name__ == "__main__":
    main()
