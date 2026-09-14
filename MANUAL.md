# Personal Finance Tracker — User Manual

Your personal system for tracking money, built on **Google Sheets** (the ledger)
and **Google Drive** (receipt storage). This manual is written for you, the main
user — it covers everything you need day-to-day, nothing you don't.

---

## 1. What the system is

- **Google Sheets** = your financial ledger. Every transaction lives here.
- **Google Drive** = your receipt/document filing cabinet.
- A small helper program (`finance.py`) does the repetitive work — recording
  transactions, filing receipts, flagging duplicates, and producing reports.

You record a transaction once, and the monthly totals + dashboard update
themselves automatically.

### Where everything lives

```
Finance/                         ← your Google Drive
└── Personal Finance/
    ├── Personal Finance Tracker  ← the Google Sheet (your ledger)
    ├── Receipts/2026/09-September/  ← receipts, filed by month
    ├── Statements/               ← bank/e-wallet PDF statements
    ├── Income/                   ← payslips, invoices
    └── Other/
```

On this computer, the helper tools live in `~/finance-system/`:

| File | What it's for |
|------|---------------|
| `finance.py` | The daily tool — record, upload, reports |
| `build_finance_system.py` | One-time builder (you normally won't touch this) |
| `gws_auth.py` | Google sign-in helper (only for re-authorizing) |
| `DESIGN.md` | Technical design (optional reading) |
| `MANUAL.md` | This file |

---

## 2. Quick start — the four things you'll do most

```bash
cd ~/finance-system

# 1. Record a simple expense
python finance.py record --type Expense --date 2026-09-10 --merchant Starbucks \
  --category "Food & Dining" --amount 45000 --payment-method "BCA Debit" --account BCA

# 2. Upload a receipt, then link it to a transaction
python finance.py upload ~/receipt.jpg --merchant Starbucks --amount 45000 --category "Food & Dining"
python finance.py record --type Expense --date 2026-09-10 --merchant Starbucks \
  --category "Food & Dining" --amount 45000 --payment-method "BCA Debit" --account BCA \
  --receipt <the file_id it printed>

# 3. See expenses missing receipts
python finance.py missing

# 4. Check for accidental double-entries
python finance.py duplicates
```

> Tip: you can combine steps 2's two commands into one by passing the receipt's
> local file path directly to `record` — it will upload and link automatically:
> `python finance.py record ... --receipt ~/receipt.jpg`

---

## 3. Recording transactions

### The four transaction types

| Type | When to use it | How it affects your numbers |
|------|---------------|------------------------------|
| **Expense** | You spent money | Adds to your expenses |
| **Income** | You earned money (salary, freelance…) | Adds to your income |
| **Transfer** | Moving money between **your own** accounts | Neutral — never income or expense |
| **Refund** | Money returned for something you bought | Reduces your expenses |

> **Rule of thumb:** if money moved between two of *your own* accounts (e.g.
> BCA → GoPay), that's a **Transfer**, not income or expense.

### The `record` command

```
python finance.py record \
  --type <Expense|Income|Transfer|Refund> \
  --date YYYY-MM-DD \
  --merchant "Name" \
  --category "Category" \
  --amount 45000 \
  --payment-method "BCA Debit" \
  --account BCA \
  [--subcategory "Coffee"] \
  [--description "client lunch"] \
  [--receipt <file-id | URL | /path/to/file>] \
  [--notes "anything"] \
  [--currency IDR]
```

All fields except `--type` and `--amount` are optional (but fill in as much as
you can — the more complete, the better your reports).

### Worked examples

```bash
# Lunch on a card
python finance.py record --type Expense --date 2026-09-10 --merchant "Warung Padang" \
  --category "Food & Dining" --amount 35000 --payment-method "BCA Debit" --account BCA

# Ride-hailing with a receipt
python finance.py record --type Expense --date 2026-09-10 --merchant Grab \
  --category Transportation --amount 32000 --payment-method GoPay --account GoPay \
  --receipt ~/grab-receipt.png

# Monthly salary
python finance.py record --type Income --date 2026-09-01 --merchant "PT Employer" \
  --category Salary --amount 15000000 --payment-method "Bank Transfer" --account BCA

# Moving money from BCA to your e-wallet (NOT income/expense)
python finance.py record --type Transfer --date 2026-09-11 --merchant "Top-up" \
  --category Other --amount 500000 --payment-method "Bank Transfer" --account BCA \
  --notes "BCA -> GoPay"

# Getting a refund on a purchase
python finance.py record --type Refund --date 2026-09-12 --merchant Tokopedia \
  --category Shopping --amount 200000 --payment-method OVO --account OVO
```

---

## 4. Receipts

### Uploading a receipt

```bash
python finance.py upload ~/receipt.jpg --merchant Starbucks --amount 45000 --category "Food & Dining"
```

This:
1. Files it into `Receipts/<year>/<month>/` automatically.
2. Renames it to a consistent format: `YYYY-MM-DD_merchant_amount_category.ext`
   (e.g. `2026-09-10_starbucks_45000_food-dining.jpg`).
3. Prints a `file_id` you can use to link it to a transaction.

### Naming convention

```
YYYY-MM-DD_merchant_amount_category.extension
```

- Sanitized automatically (spaces → dashes, no special characters).
- If you don't know the merchant/category, leave it out — the tool keeps the
  original rather than guessing.
- Never put card numbers or account numbers in a receipt name.

### Linking a receipt to a transaction

Pass `--receipt` with either the `file_id`, a Drive URL, or a local file path:

```bash
--receipt 1tjtbdt3O6CoNyjlZ3LegL3rolk_YJdbr          # file id
--receipt https://drive.google.com/file/d/..../view   # URL
--receipt ~/receipt.jpg                               # local file (auto-uploads)
```

---

## 5. Your Google Sheet, tab by tab

Open **Personal Finance Tracker** in Google Sheets. You'll see these tabs:

| Tab | What it holds |
|-----|---------------|
| **Transactions** | Every transaction. This is the source of truth. |
| **Categories** | The list of valid categories (edit here → dropdowns update). |
| **Accounts** | Your accounts (BCA, GoPay, Cash…). |
| **Payment Methods** | How you paid (BCA Debit, QRIS…). |
| **Monthly Summary** | Auto-calculated income/expense/net per month + category breakdown. |
| **Dashboard** | Headline numbers: totals, top categories, recent activity. |
| **Configuration** | Defaults (base currency, default account…). |

### The Transactions columns

| Column | Meaning |
|--------|---------|
| transaction_id | Auto-generated unique ID (e.g. `TXN-20260910-001`) |
| transaction_date | `YYYY-MM-DD` |
| transaction_type | Expense / Income / Transfer / Refund |
| description / merchant | What it was, and who |
| category / subcategory | What kind of spending |
| amount | Always positive (direction comes from type) |
| currency | Usually IDR |
| payment_method / account | How and from where |
| receipt_link / receipt_file_id | Link to the receipt in Drive |
| has_receipt | Auto (TRUE/FALSE) |
| duplicate_status | Blank, or `POSSIBLE_DUPLICATE` (auto-flagged) |
| reconciliation_status | Pending / Reconciled / Flagged |
| source | Manual / Receipt / Import |
| notes | Anything else |

### Adding/editing by hand (optional)

You can also type directly into the sheet. Dropdowns enforce valid categories,
payment methods, and accounts. Amounts must be positive numbers, and dates use
`YYYY-MM-DD`. To add a new category, just add it to the **Categories** tab.

---

## 6. Reading your reports

### Monthly Summary
Each row is a month. `Income`, `Expenses`, `Net Cash Flow`, and transaction
count are calculated automatically. Below the table, pick a month to see how
much went to each category.

### Dashboard
- **YTD Income / Expenses / Net Cash Flow** — your year so far.
- **This Month** — same numbers for the current month.
- **Expenses Missing Receipts** — how many expenses lack a receipt.
- **Top 5 Expense Categories** — where your money actually goes.
- **Recent Transactions** — the last 10 entries.

---

## 7. Monthly reconciliation (once a month)

The goal: make sure your ledger matches your bank/e-wallet statements.

1. Open your bank/e-wallet statement.
2. Go through it line by line and make sure every transaction is in the sheet.
   - Missing something? → `python finance.py record ...`
   - Entered twice? → `python finance.py duplicates`
3. Attach any missing receipts → `python finance.py missing` then upload/link.
4. Fix any wrong categories directly in the sheet.
5. Mark the month done:

```bash
python finance.py reconcile --month 2026-09
```

This sets every transaction in that month to `Reconciled` and stamps the date.

---

## 8. Duplicates & missing receipts

- **Duplicates are never auto-deleted.** If a new entry looks identical to an
  existing one (same date, merchant, amount, payment method), it's flagged
  `POSSIBLE_DUPLICATE` and highlighted yellow. Review it, then clear the flag
  or delete the extra row.
- **Missing receipts** are surfaced in the Dashboard and via
  `python finance.py missing`. Rows with an expense but no receipt are tinted
  red in the receipt columns.

---

## 9. Security & privacy

- The system uses **Google Drive + Sheets access only** — nothing else.
- Your spreadsheet and folders are **private by default**.
- **Never** store passwords, card numbers, or account numbers in the sheet.
- The Google sign-in token lives only on this computer (`~/.hermes/`).

---

## 10. Troubleshooting

| Problem | Fix |
|---------|-----|
| `NOT_AUTHENTICATED` / token expired | `python gws_auth.py --check`; if it fails, re-authorize with `gws_auth.py` (see README) |
| `spreadsheet ... not found` | Make sure `config.json` exists in `~/finance-system/` (it stores the sheet ID) |
| Date error | Use `YYYY-MM-DD` format |
| Amount error | Amount must be a positive number |
| Category not in list | Add it to the **Categories** tab first |
| A command fails mid-way | Nothing was half-written — re-run it; the system fails safe |

---

## 11. Cheat sheet

```bash
cd ~/finance-system

# Record
python finance.py record --type Expense --date 2026-09-10 --merchant "X" \
  --category "Food & Dining" --amount 45000 --payment-method "BCA Debit" --account BCA

# Upload a receipt
python finance.py upload ~/receipt.jpg --merchant X --amount 45000 --category "Food & Dining"

# Reports
python finance.py missing        # expenses without receipts
python finance.py duplicates     # possible double-entries
python finance.py categories     # list valid categories
python finance.py reconcile --month 2026-09   # mark a month done

# Edit lists: open the sheet and edit Categories / Accounts / Payment Methods tabs.
```

---

**That's it.** Record as you go, reconcile once a month, and your questions —
*how much did I spend, on what, and where's the receipt?* — answer themselves.
