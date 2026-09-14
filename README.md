# Personal Finance Tracking System

Google Sheets (ledger) + Google Drive (receipts), connected by `receipt_file_id`.

## Files

| File | Purpose |
|------|---------|
| `DESIGN.md` | Full system design & documentation (architecture, schema, workflows). |
| `build_finance_system.py` | One-shot builder: Drive tree + multi-tab spreadsheet + validation + dashboard. |
| `finance.py` | Daily helper: `record`, `upload`, `missing`, `duplicates`, `reconcile`, `categories`. |

## Quick start

### 1. Authorize Google (one-time)

Google OAuth is not yet set up on this machine. The only prerequisite is a
Google Cloud **OAuth 2.0 Client (Desktop app)** with **Drive API** and
**Sheets API** enabled. Then:

```bash
GSETUP="python ~/.hermes/hermes-agent/skills/productivity/google-workspace/scripts/setup.py"
$GSETUP --client-secret /path/to/client_secret.json
$GSETUP --auth-url --services drive,sheets --format json   # → open URL, paste redirect
$GSETUP --auth-code "<pasted URL or code>" --format json
$GSETUP --check                                             # → AUTHENTICATED
```

### 2. Build the system

```bash
cd ~/finance-system
python build_finance_system.py --check   # verify auth
python build_finance_system.py           # create Drive tree + spreadsheet
```

### 3. Daily use

```bash
# Upload a receipt (auto-names + routes to the right month folder)
python finance.py upload ~/receipt.jpg --merchant Starbucks --amount 45000 --category "Food & Dining"

# Record a transaction (link a receipt by file ID, URL, or local path)
python finance.py record --type Expense --date 2026-09-10 --merchant Starbucks \
    --category "Food & Dining" --amount 45000 --payment-method "BCA Debit" \
    --account BCA --receipt <file_id>

# Reports
python finance.py missing      # expenses without receipts
python finance.py duplicates   # possible duplicates
python finance.py reconcile --month 2026-09
python finance.py categories
```

The **Monthly Summary** and **Dashboard** tabs recalculate automatically via
formulas — no code needed.

## Notes

- All scripts are **idempotent** — they reuse the existing `Personal Finance`
  folder and `Personal Finance Tracker` spreadsheet instead of duplicating.
- Amounts are always **positive**; direction comes from `transaction_type`.
- Transfers between your own accounts are `Transfer` (never Income/Expense).
- Duplicates are **flagged**, never auto-deleted.
