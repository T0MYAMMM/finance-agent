#!/usr/bin/env python3
"""
finance.py — daily driver for the Personal Finance system.

Subcommands:
    record      Append a validated transaction (with duplicate detection + receipt link)
    upload      Upload a receipt to the correct year/month Drive folder
    missing     List expenses that have no attached receipt
    duplicates  Scan the ledger for possible duplicate transactions
    reconcile   Mark all transactions in a month as Reconciled
    categories  List the valid categories

Reads the spreadsheet/folder IDs from config.json (written by
build_finance_system.py) so it never depends on Drive's slow name-search index.

Examples:
    python finance.py upload ~/receipt.jpg --merchant Starbucks --amount 45000 --category "Food & Dining"
    python finance.py record --date 2026-09-10 --type Expense --merchant Starbucks \
        --category "Food & Dining" --amount 45000 --payment-method "BCA Debit" \
        --account BCA --receipt <file_id_or_path>
    python finance.py missing
    python finance.py duplicates
    python finance.py reconcile --month 2026-09
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

HOME = Path.home()
_SKILL_CANDIDATES = [
    HOME / ".hermes" / "skills" / "productivity" / "google-workspace" / "scripts",
    HOME / ".hermes" / "hermes-agent" / "skills" / "productivity" / "google-workspace" / "scripts",
]
_skill_dir = next((p for p in _SKILL_CANDIDATES if p.exists()), None)
if _skill_dir is None:
    sys.stderr.write("ERROR: google-workspace skill scripts not found.\n")
    sys.exit(2)
sys.path.insert(0, str(_skill_dir))
import google_api  # noqa: E402

ROOT_FOLDER_NAME = "Personal Finance"
SHEET_TITLE = "Personal Finance Tracker"

TRANSACTION_TYPES = ["Expense", "Income", "Transfer", "Refund"]
RECONCILIATION_STATUSES = ["Pending", "Reconciled", "Flagged"]

# 0-based column indexes (A..V)
C = {
    "id": 0, "date": 1, "type": 2, "description": 3, "merchant": 4,
    "category": 5, "subcategory": 6, "amount": 7, "currency": 8,
    "payment_method": 9, "account": 10, "receipt_link": 11,
    "receipt_file_id": 12, "receipt_filename": 13, "has_receipt": 14,
    "duplicate_status": 15, "reconciliation_status": 16, "reconciled_at": 17,
    "source": 18, "notes": 19, "created_at": 20, "updated_at": 21,
}
N_COLS = 22

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"


def _load_config():
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def sanitize(s):
    if not s:
        return ""
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "unknown"


def parse_amount(s):
    s = str(s).replace(",", "").replace(" ", "").strip()
    return float(s)


def _to_float(s):
    try:
        return float(str(s).replace(",", "").replace(" ", ""))
    except (ValueError, TypeError):
        return None


def extract_file_id(value):
    if not value:
        return None
    m = re.search(r"/d/([A-Za-z0-9_-]+)", value)
    if m:
        return m.group(1)
    m = re.search(r"id=([A-Za-z0-9_-]+)", value)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", value):
        return value
    return None


def _services():
    drive = google_api.build_service("drive", "v3")
    sheets = google_api.build_service("sheets", "v4")
    return drive, sheets


def find_spreadsheet(drive, title):
    cfg = _load_config()
    if cfg.get("spreadsheet_id"):
        return cfg["spreadsheet_id"]
    q = f"name = '{title}' and mimeType = 'application/vnd.google-sheets.spreadsheet' and trashed = false"
    res = drive.files().list(q=q, spaces="drive", fields="files(id, name)", pageSize=10).execute()
    files = res.get("files", [])
    if not files:
        sys.stderr.write(f"ERROR: spreadsheet '{title}' not found. Run build_finance_system.py first.\n")
        sys.exit(1)
    return files[0]["id"]


def find_folder(drive, name, parent_id=None):
    q = f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent_id:
        q += f" and '{parent_id}' in parents"
    res = drive.files().list(q=q, spaces="drive", fields="files(id, name)", pageSize=50).execute()
    return res.get("files", [])


def find_or_create_folder(drive, name, parent_id=None):
    existing = find_folder(drive, name, parent_id)
    if existing:
        return existing[0]["id"]
    meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        meta["parents"] = [parent_id]
    return drive.files().create(body=meta, fields="id").execute()["id"]


def find_root(drive):
    cfg = _load_config()
    if cfg.get("root_folder_id"):
        return cfg["root_folder_id"]
    existing = find_folder(drive, ROOT_FOLDER_NAME)
    if not existing:
        sys.stderr.write("ERROR: 'Personal Finance' folder not found. Run build_finance_system.py first.\n")
        sys.exit(1)
    return existing[0]["id"]


def _read_transactions(sheets, ss_id):
    """Return list of (row_number, 22-element values) for REAL transaction rows.

    Real rows are those with a transaction_id (A) or transaction_date (B);
    the pre-populated has_receipt formula rows are ignored.
    """
    res = sheets.spreadsheets().values().get(
        spreadsheetId=ss_id, range="Transactions!A2:V5000",
        valueRenderOption="FORMATTED_VALUE",
    ).execute()
    raw = res.get("values", [])
    out = []
    for i, r in enumerate(raw):
        full = r + [""] * (N_COLS - len(r))
        if full[C["id"]] or full[C["date"]]:
            out.append((i + 2, full))
    return out


def _write_row(sheets, ss_id, row_index, values):
    sheets.spreadsheets().values().update(
        spreadsheetId=ss_id,
        range=f"Transactions!A{row_index}:V{row_index}",
        valueInputOption="USER_ENTERED",
        body={"values": [values]},
    ).execute()


def _upload_receipt(drive, path, date, merchant, amount, category, name=None):
    """Upload a local receipt to the correct year/month folder. Returns dict."""
    d = datetime.strptime(date, "%Y-%m-%d")
    root = find_root(drive)
    receipts = find_or_create_folder(drive, "Receipts", root)
    year_folder = find_or_create_folder(drive, str(d.year), receipts)
    month_folder = find_or_create_folder(drive, f"{d.month:02d}-{d.strftime('%B')}", year_folder)

    if name:
        fname = name
    else:
        amount_str = str(int(amount)) if amount else ""
        base = "_".join(p for p in [date, sanitize(merchant), amount_str, sanitize(category)] if p)
        ext = Path(path).suffix.lower().lstrip(".") or "file"
        fname = f"{base}.{ext}"

    from googleapiclient.http import MediaFileUpload
    media = MediaFileUpload(str(Path(path).expanduser()), mimetype=None, resumable=True)
    created = drive.files().create(
        body={"name": fname, "parents": [month_folder]},
        media_body=media, fields="id, name, webViewLink",
    ).execute()
    return {"id": created["id"], "name": created["name"], "link": created["webViewLink"]}


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------
def cmd_upload(args):
    drive, sheets = _services()
    date = args.date or datetime.now().strftime("%Y-%m-%d")
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        sys.stderr.write("ERROR: --date must be YYYY-MM-DD\n")
        sys.exit(1)

    path = Path(args.file).expanduser()
    if not path.exists():
        sys.stderr.write(f"ERROR: file not found: {path}\n")
        sys.exit(1)

    amount = parse_amount(args.amount) if args.amount else None
    info = _upload_receipt(drive, args.file, date, args.merchant, amount, args.category, name=args.name)
    d = datetime.strptime(date, "%Y-%m-%d")
    print(f"Uploaded: {info['name']}")
    print(f"  file_id: {info['id']}")
    print(f"  folder:  Personal Finance/Receipts/{d.year}/{d.month:02d}-{d.strftime('%B')}")
    print(f"  link:    {info['link']}")


def cmd_record(args):
    drive, sheets = _services()
    ss_id = find_spreadsheet(drive, SHEET_TITLE)

    if args.type not in TRANSACTION_TYPES:
        sys.stderr.write(f"ERROR: --type must be one of {TRANSACTION_TYPES}\n")
        sys.exit(1)

    date = args.date or datetime.now().strftime("%Y-%m-%d")
    try:
        d = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        sys.stderr.write("ERROR: --date must be YYYY-MM-DD\n")
        sys.exit(1)

    amount = parse_amount(args.amount)
    if amount <= 0:
        sys.stderr.write("ERROR: --amount must be > 0 (direction comes from --type)\n")
        sys.exit(1)

    currency = args.currency or "IDR"
    merchant = (args.merchant or args.description or "").strip()
    category = args.category or ""
    payment = args.payment_method or ""
    account = args.account or ""

    # Receipt: local path -> upload; file id / url -> link; else none.
    receipt_file_id = None
    receipt_link = ""
    receipt_filename = ""
    if args.receipt:
        fid = extract_file_id(args.receipt)
        if fid:
            receipt_file_id = fid
            receipt_link = f"https://drive.google.com/file/d/{fid}/view"
        elif Path(args.receipt).expanduser().exists():
            info = _upload_receipt(drive, args.receipt, date, args.merchant, amount, args.category)
            receipt_file_id = info["id"]
            receipt_filename = info["name"]
            receipt_link = f"https://drive.google.com/file/d/{receipt_file_id}/view"
            print(f"  (uploaded receipt -> {info['name']})")

    # Duplicate detection against existing rows.
    existing = _read_transactions(sheets, ss_id)
    dup_status = ""
    for _rn, r in existing:
        rdate = r[C["date"]].strip() if r[C["date"]] else ""
        rmerchant = (r[C["merchant"]] or "").strip().lower()
        ramount = _to_float(r[C["amount"]])
        rpay = (r[C["payment_method"]] or "").strip().lower()
        if (rdate == date and rmerchant == merchant.lower()
                and ramount is not None and abs(ramount - amount) < 0.001
                and rpay == payment.lower()):
            dup_status = "POSSIBLE_DUPLICATE"
            break

    next_row = (existing[-1][0] + 1) if existing else 2
    seq = len(existing) + 1
    tx_id = f"TXN-{d.strftime('%Y%m%d')}-{seq:03d}"

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    row = [""] * N_COLS
    row[C["id"]] = tx_id
    row[C["date"]] = date
    row[C["type"]] = args.type
    row[C["description"]] = args.description or ""
    row[C["merchant"]] = merchant
    row[C["category"]] = category
    row[C["subcategory"]] = args.subcategory or ""
    row[C["amount"]] = amount
    row[C["currency"]] = currency
    row[C["payment_method"]] = payment
    row[C["account"]] = account
    row[C["receipt_link"]] = receipt_link
    row[C["receipt_file_id"]] = receipt_file_id or ""
    row[C["receipt_filename"]] = receipt_filename
    row[C["has_receipt"]] = f'=IF(OR($L{next_row}<>"",$M{next_row}<>""),TRUE,FALSE)'
    row[C["duplicate_status"]] = dup_status
    row[C["reconciliation_status"]] = args.status or "Pending"
    row[C["reconciled_at"]] = ""
    row[C["source"]] = args.source or "Manual"
    row[C["notes"]] = args.notes or ""
    row[C["created_at"]] = now
    row[C["updated_at"]] = now

    _write_row(sheets, ss_id, next_row, row)

    print(f"Recorded transaction {tx_id}")
    print(f"  {date}  {args.type}  {merchant}  {currency} {amount:,.0f}  {category}")
    if dup_status:
        print(f"  ⚠ {dup_status} — review against existing entries.")
    if receipt_link:
        print(f"  receipt: {receipt_link}")


def cmd_missing(args):
    drive, sheets = _services()
    ss_id = find_spreadsheet(drive, SHEET_TITLE)
    rows = _read_transactions(sheets, ss_id)

    missing = [
        (rn, r) for rn, r in rows
        if r[C["type"]] == "Expense" and not r[C["receipt_link"]] and not r[C["receipt_file_id"]]
    ]
    if not missing:
        print("No expenses missing receipts.")
        return
    print(f"{len(missing)} expense(s) without receipts:\n")
    print(f"{'Date':<12}{'Merchant':<22}{'Category':<16}{'Amount':>12}")
    print("-" * 62)
    for _rn, r in missing:
        print(f"{r[C['date']]:<12}{(r[C['merchant']] or '')[:20]:<22}"
              f"{(r[C['category']] or '')[:14]:<16}{r[C['amount']]:>12}")


def cmd_duplicates(args):
    drive, sheets = _services()
    ss_id = find_spreadsheet(drive, SHEET_TITLE)
    rows = _read_transactions(sheets, ss_id)

    groups = {}
    for _rn, r in rows:
        key = (r[C["date"]].strip(), (r[C["merchant"]] or "").strip().lower(),
               (r[C["amount"]] or "").strip(), (r[C["payment_method"]] or "").strip().lower())
        groups.setdefault(key, []).append(r)

    found = False
    for key, rs in groups.items():
        if len(rs) > 1:
            found = True
            date, merchant, amount, pay = key
            print(f"Possible duplicate: {date} | {merchant} | {amount} | {pay or '-'}")
            for r in rs:
                print(f"    {r[C['id']]}  status={r[C['duplicate_status']] or 'clear'}")
    if not found:
        print("No duplicate transactions found.")


def cmd_reconcile(args):
    drive, sheets = _services()
    ss_id = find_spreadsheet(drive, SHEET_TITLE)
    month = args.month
    try:
        m_start = datetime.strptime(month, "%Y-%m")
    except ValueError:
        sys.stderr.write("ERROR: --month must be YYYY-MM\n")
        sys.exit(1)
    m_end = datetime(m_start.year + (m_start.month // 12), (m_start.month % 12) + 1, 1)

    rows = _read_transactions(sheets, ss_id)
    today = datetime.now().strftime("%Y-%m-%d")
    changed = 0
    for rn, r in rows:
        dstr = r[C["date"]].strip()
        try:
            d = datetime.strptime(dstr, "%Y-%m-%d")
        except ValueError:
            continue
        if m_start <= d < m_end:
            sheets.spreadsheets().values().update(
                spreadsheetId=ss_id, range=f"Transactions!Q{rn}:R{rn}",
                valueInputOption="USER_ENTERED",
                body={"values": [["Reconciled", today]]},
            ).execute()
            changed += 1

    print(f"Reconciled {changed} transaction(s) for {month}.")


def cmd_categories(args):
    drive, sheets = _services()
    ss_id = find_spreadsheet(drive, SHEET_TITLE)
    res = sheets.spreadsheets().values().get(
        spreadsheetId=ss_id, range="Categories!A2:B100", valueRenderOption="FORMATTED_VALUE",
    ).execute()
    print("Valid categories (type — category):")
    for r in res.get("values", []):
        if len(r) >= 2 and r[0] and r[1]:
            print(f"  {r[0]:<9} {r[1]}")


def main():
    p = argparse.ArgumentParser(prog="finance.py", description="Personal Finance daily helper")
    sub = p.add_subparsers(dest="cmd", required=True)

    up = sub.add_parser("upload", help="Upload a receipt to Drive")
    up.add_argument("file")
    up.add_argument("--date")
    up.add_argument("--merchant")
    up.add_argument("--amount")
    up.add_argument("--category")
    up.add_argument("--name", help="Override the auto-generated filename")
    up.set_defaults(func=cmd_upload)

    rec = sub.add_parser("record", help="Record a transaction")
    rec.add_argument("--date")
    rec.add_argument("--type", required=True)
    rec.add_argument("--description")
    rec.add_argument("--merchant")
    rec.add_argument("--category")
    rec.add_argument("--subcategory")
    rec.add_argument("--amount", required=True)
    rec.add_argument("--currency", default="IDR")
    rec.add_argument("--payment-method")
    rec.add_argument("--account")
    rec.add_argument("--receipt", help="Drive file ID, URL, or local path (auto-uploaded)")
    rec.add_argument("--notes")
    rec.add_argument("--source", choices=["Manual", "Receipt", "Import"], default="Manual")
    rec.add_argument("--status", choices=RECONCILIATION_STATUSES, default="Pending")
    rec.set_defaults(func=cmd_record)

    sub.add_parser("missing", help="List expenses without receipts").set_defaults(func=cmd_missing)

    sub.add_parser("duplicates", help="Scan for possible duplicates").set_defaults(func=cmd_duplicates)

    rc = sub.add_parser("reconcile", help="Mark a month as reconciled")
    rc.add_argument("--month", required=True)
    rc.set_defaults(func=cmd_reconcile)

    sub.add_parser("categories", help="List valid categories").set_defaults(func=cmd_categories)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
