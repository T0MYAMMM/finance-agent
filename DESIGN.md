# Personal Finance Tracking System — Design & Documentation

A structured personal finance system where **Google Sheets** is the financial ledger
(source of truth) and **Google Drive** is the receipt/document repository.

> **Status note:** Google integration is *available* in this environment (the
> `google-workspace` skill ships with full Drive + Sheets API support via OAuth2),
> but it is **not yet authorized** — no OAuth token exists. Everything in this
> document is implementable the moment authorization is completed. See
> `README.md` / "Unblocking Google Access" at the end.

---

## 1. Architecture

```
┌─────────────────┐
│      User       │
└────────┬────────┘
         │  transaction / receipt
         │
  ┌──────┴───────┐                    ┌──────────────────┐
  │   finance.py │  (helper CLI)      │  Google Drive    │
  │  record /    │───────────────────▶│  Receipt storage │
  │  upload /    │                    │  (Personal       │
  │  reconcile   │                    │   Finance/…)     │
  └──────┬───────┘                    └────────┬─────────┘
         │  validated row + receipt_file_id    │ file_id
         ▼                                     ▼
  ┌──────────────────┐              ┌─────────────────────┐
  │  Google Sheets   │◀───link─────│  receipt_file_id    │
  │  Personal Finance│              │  (stored in sheet)  │
  │  Tracker         │              └─────────────────────┘
  │  (ledger)        │
  └────────┬─────────┘
           │  formulas (SUMIFS / QUERY)
           ▼
  ┌──────────────────────┐
  │ Monthly Summary +    │
  │ Dashboard            │
  └──────────────────────┘
```

**Principle:** Sheets = structured ledger. Drive = document repository. The
relationship key is `receipt_file_id` (Drive's immutable file ID), never the
filename. `receipt_link` (a URL) is a convenience so the user can click straight
through to the document.

---

## 2. Google Drive Structure

```
Personal Finance/                      ← root (single source, never duplicated)
├── Receipts/
│   └── 2026/
│       ├── 01-January/
│       ├── 02-February/
│       ├── 03-March/
│       ├── ...
│       └── 12-December/
├── Statements/                        ← bank/e-wallet PDF statements
├── Income/                            ← payslips, invoices, income docs
└── Other/                             ← uncategorized supporting docs
```

Rationale:
- **Year/month folders** keep any single folder from growing past a few hundred
  files, so Drive UI stays fast and archival (archive a whole year) is trivial.
- **Flat top level** — only 4 folders; no deep nesting to hunt through.
- **Predictable paths** make automated receipt upload deterministic:
  `Personal Finance/Receipts/2026/09-September/<filename>`.
- Year/month folders are **created lazily** by the helper (so you never end up
  with 60 empty folders for years you don't use), but the current year is
  pre-created during setup.

---

## 3. Receipt File Naming Convention

```
YYYY-MM-DD_<merchant>_<amount>_<category>.<ext>
```

Examples:
```
2026-09-10_Starbucks_45000_Food.jpg
2026-09-10_Grab_32000_Transport.pdf
```

Rules:
- **Sanitize** merchant/category: lowercase, strip characters outside
  `a-z0-9`, replace spaces with `-`. No emoji, no `/`, no `:`, no `&`.
- **Amount is integer in the base currency** (IDR has no decimals in practice);
  if you use a currency with decimals, replace `.` with `-`.
- If a field (merchant/category) is **unknown**, omit it rather than invent it
  — preserve the original filename and mark the transaction for manual review.
- Never put sensitive info (card numbers, account numbers) in the filename.

---

## 4. Google Sheets Structure

Seven tabs:

| Tab | Purpose |
|-----|---------|
| **Transactions** | Raw financial records (the ledger). |
| **Categories** | Reference table of valid categories/subcategories. |
| **Accounts** | Financial accounts (banks, e-wallets, cash, cards). |
| **Payment Methods** | Standardized payment methods. |
| **Monthly Summary** | Auto-aggregated per-month totals + category spend. |
| **Dashboard** | High-level totals, trends, top categories, flags. |
| **Configuration** | Configurable values (currency, defaults) — no hardcoding. |

---

## 5. Transaction Schema (Transactions tab)

| # | Column | Letter | Type | Purpose |
|---|--------|--------|------|---------|
| 1 | transaction_id | A | text | Stable, unique ID (`TXN-20260910-001`). Primary key. |
| 2 | transaction_date | B | date | `YYYY-MM-DD`. |
| 3 | transaction_type | C | list | `Expense / Income / Transfer / Refund`. |
| 4 | description | D | text | Human-readable note. |
| 5 | merchant | E | text | Who you paid / who paid you. |
| 6 | category | F | list | From **Categories** (single source of truth). |
| 7 | subcategory | G | text | Optional finer granularity. |
| 8 | amount | H | number | **Always positive**; direction comes from `transaction_type`. |
| 9 | currency | I | list | Default `IDR`. |
| 10 | payment_method | J | list | From **Payment Methods**. |
| 11 | account | K | list | From **Accounts**. |
| 12 | receipt_link | L | url | Clickable Drive URL to the receipt. |
| 13 | receipt_file_id | M | text | Drive file ID — the **canonical** receipt↔transaction key. |
| 14 | receipt_filename | N | text | Sanitized filename (human-readable backup reference). |
| 15 | has_receipt | O | formula | `=IF(OR(L2<>"",M2<>""),TRUE,FALSE)` |
| 16 | duplicate_status | P | text | `""` or `POSSIBLE_DUPLICATE` (flagged, never auto-deleted). |
| 17 | reconciliation_status | Q | list | `Pending / Reconciled / Flagged`. |
| 18 | reconciled_at | R | date | When the month was marked reconciled. |
| 19 | source | S | list | `Manual / Receipt / Import`. |
| 20 | notes | T | text | Free text (OCR confidence notes, etc.). |
| 21 | created_at | U | timestamp | When the row was created. |
| 22 | updated_at | V | timestamp | Last modified (audit trail). |

**Why `amount` is always positive:** financial sign is a *type* property, not a
magnitude property. This prevents double-negative errors and makes SUMIFS clean:
`SUMIFS(amount, type, "Expense")`.

---

## 6. Transaction Types & Financial Semantics

| Type | Meaning | Effect on summaries |
|------|---------|---------------------|
| **Expense** | Money out (goods/services). | Counts in *Expenses*. |
| **Income** | Money in (salary, freelance…). | Counts in *Income*. |
| **Transfer** | Moving money between **your own** accounts. | **Excluded** from income/expense. Net-neutral. |
| **Refund** | Money returned for a prior expense. | Counts as *negative expense* (reduces Expenses), **not** income. |

Key rule: **Transfers between your own accounts are never income or expense.**
A transfer is recorded once with `account` = source and a `notes`/description
indicating the destination (or a second account column if you add one later).

---

## 7. Category System

```
Expense
  Food & Dining      (Restaurant, Groceries, Coffee)
  Transportation     (Fuel, Ride-hailing, Public Transit, Parking)
  Housing            (Rent, Maintenance)
  Utilities          (Electricity, Water, Internet, Phone)
  Shopping
  Health             (Pharmacy, Doctor, Insurance)
  Entertainment
  Education
  Travel
  Subscriptions
  Personal Care
  Gifts
  Other

Income
  Salary
  Freelance
  Business
  Investment
  Refund
  Other
```

Subcategories are optional and **not** validated (to avoid brittle dependent
dropdowns). Categories live in the **Categories** tab — edit there and every
dropdown updates automatically.

---

## 8. Receipt ↔ Transaction Relationship

- `receipt_file_id` (Drive file ID) is the **canonical link**. It survives file
  renames and folder moves.
- `receipt_link` = `https://drive.google.com/file/d/<file_id>/view` for one-click open.
- `receipt_filename` is stored only for human convenience; **never** used as the
  relationship key.

```
transaction_id
      └── receipt_file_id ──▶ Google Drive file (immutable ID)
      └── receipt_link    ──▶ clickable URL (derived from file_id)
      └── receipt_filename ──▶ human-readable reference only
```

---

## 9. Recording Workflow

```
User makes a purchase
        ↓
Receipt obtained (photo / PDF)
        ↓
finance.py upload <file>            → uploads to correct year/month folder,
        ↓                              sanitizes name, returns file_id
finance.py record … --receipt <id>  → appends validated row + links receipt
        ↓
duplicate check + data validation   → flag, never silently drop
        ↓
Google Sheets updated
        ↓
Monthly Summary + Dashboard auto-recalculate (formulas)
```

---

## 10. Receipt Processing (OCR)

If OCR is used (the environment has an `ocr-and-documents` skill; `marker-pdf`/
`pymupdf` can extract text, and receipt text → fields via simple parsing):

```
Receipt → OCR/extraction → structured candidate fields
        → validation (confidence gate)
        → user confirmation
        → Google Sheets
```

**Never treat OCR output as authoritative.** Fields with low confidence are
marked `source=Receipt` + `notes="needs review: <reason>"` and the user confirms
before the row is trusted. No field is written with `source` claiming a certainty
the extractor doesn't have.

---

## 11. Duplicate Detection

Potential duplicate = **same date + same merchant + same amount + same payment
method** (and/or same `receipt_file_id`).

Behavior:
- On `record`, if an existing row matches, the new row is still added but
  `duplicate_status = POSSIBLE_DUPLICATE` and the helper prints a warning.
- `finance.py duplicates` scans the whole sheet and flags/relists matches.
- **Nothing is auto-deleted.** Review and manually clear `duplicate_status`.

---

## 12. Data Validation (enforced in-sheet)

| Field | Validation |
|-------|-----------|
| transaction_date | Must be a valid date (date format). |
| transaction_type | Dropdown: Expense/Income/Transfer/Refund. |
| category | Dropdown ← `Categories!B2:B` (range, single source of truth). |
| amount | Number, **> 0** (sign is carried by type). |
| currency | Dropdown (IDR, USD, …). |
| payment_method | Dropdown ← `Payment Methods`. |
| account | Dropdown ← `Accounts`. |
| reconciliation_status | Dropdown: Pending/Reconciled/Flagged. |
| receipt_link | Hyperlink format (when present). |

Conditional formatting:
- `duplicate_status = POSSIBLE_DUPLICATE` → yellow highlight.
- Expense with no receipt (`has_receipt = FALSE`) → receipt columns tinted red.

---

## 13. Financial Calculations

- **Monthly Income** = `SUMIFS(amount, type,"Income", date within month)`
- **Monthly Expenses** = `SUMIFS(amount, type,"Expense", date within month)`
- **Net Cash Flow** = Income − Expenses
- **Category spending** = `SUMIFS(amount, type,"Expense", category, X, month…)`
- **YTD Income/Expenses/Net** = same without the month window.
- **Month-over-month** = this month vs previous month (in Monthly Summary).
- **Transfers/Refunds** are excluded from Income and Expenses (Refund reduces
  Expenses when you choose to model it that way).

---

## 14. Dashboard (formula-driven, not decorative)

| Block | Source |
|-------|--------|
| Total Income / Expenses / Net (YTD) | `SUMIFS` over Transactions |
| This month vs last month | Monthly Summary deltas |
| Top 5 expense categories | `QUERY(... group by F order by sum(H) desc limit 5)` |
| Recent transactions | `QUERY(... order by B desc limit 10)` |
| Expenses missing receipts | `COUNTIFS(type,"Expense", has_receipt, FALSE)` + list |

---

## 15. Missing-Receipt Tracking

`has_receipt` is derived (`O`), so "expenses without receipts" is a live filter:
`COUNTIFS(C:C,"Expense", O:O, FALSE)`. The Dashboard surfaces the count and the
list, making monthly reconciliation easy.

---

## 16. Monthly Reconciliation Process

1. Get bank/e-wallet statement.
2. Compare against **Transactions**.
3. Identify missing transactions → add them.
4. Identify duplicates → `finance.py duplicates` → clear flags.
5. Attach missing receipts → `finance.py upload` + link.
6. Correct categorization.
7. Mark month `reconciliation_status = Reconciled`, set `reconciled_at`.

---

## 17. Security & Privacy

- **No credentials, API keys, passwords, or tokens** are ever stored in the sheet.
- OAuth token lives only at `~/.hermes/google_token.json` (local, `0600`).
- Spreadsheet and folders are **private by default** (never `--type anyone`).
- Minimum scopes: Drive + Sheets only.
- Filenames avoid sensitive data; OCR output is not logged verbatim.
- If a secret is encountered in input, it is **redacted** before touching Drive/Sheets.

---

## 18. Automation Opportunities (ranked by reliability)

1. **Receipt upload + rename + folder routing** — deterministic, implemented.
2. **Transaction append with validation + duplicate flag** — implemented.
3. **Missing-receipt report** — implemented (query).
4. **Duplicate scan** — implemented.
5. **Monthly summary / dashboard** — implemented as native formulas (zero code).
6. Optional later: OCR field extraction (gated by confidence), monthly
   reconciliation reminders, category auto-suggestion.

Only #1–#5 are shipped; OCR is opt-in because it is the only step that can
produce *wrong* data if unvalidated.

---

## 19. Error Handling (safe-failure default)

| Failure | Behavior |
|---------|----------|
| Receipt upload fails | No transaction is written; error reported, original file untouched. |
| Drive unavailable | Abort before writing anything; clear error. |
| Sheets update fails | Abort; no partial row (append is atomic). |
| Receipt parsing/OCR fails | Fall back to manual fields; mark `needs review`. |
| Duplicate detected | Row added but flagged `POSSIBLE_DUPLICATE`; user reviews. |
| Category/amount/date undetermined | Row **not** written with guessed values — helper prompts or leaves blank + flags. |
| Drive file not found | `receipt_link` renders but points to a dead ID; helper warns on reconcile. |

The invariant: **fail safe — never silently create an incorrect financial record.**

---

## 20. Auditability

- `transaction_id`, `created_at`, `updated_at`, `source` maintained per row.
- Rows are **appended**, never overwritten in place by the helper (append is
  reversible and traceable).
- `updated_at` is bumped on any edit; original `created_at` is preserved.
- `receipt_file_id` ties every receipt-backed row to an immutable Drive object.

---

## 21. Maintenance & Troubleshooting

- **Add a category** → edit `Categories` tab; dropdowns update automatically.
- **New account/method** → edit `Accounts` / `Payment Methods`.
- **Re-auth expired** → `setup.py --check`; if `REFRESH_FAILED`, redo OAuth steps.
- **403 Insufficient Permission** → re-authorize with broader scopes
  (`setup.py --revoke` then re-auth).
- **Wrong folder** → `finance.py` resolves `Personal Finance/Receipts/YYYY/MM-…`
  deterministically; verify the root folder name is exactly `Personal Finance`.

---

## 22. Unblocking Google Access (one-time)

The only missing piece is OAuth authorization. It requires the user to:

1. Create a Google Cloud project + **OAuth 2.0 Client (Desktop app)**.
2. Enable **Google Drive API** and **Google Sheets API**.
3. Download `client_secret.json` and run the skill's `setup.py`.

The `google-workspace` skill's `setup.py` then completes a fully non-interactive
OAuth flow (auth URL → paste redirect → token saved at `~/.hermes/google_token.json`),
after which `build_finance_system.py` can create everything automatically.

---

## 23. Expected End State

Record:
```
2026-09-10 · Lunch · Rp45,000 · Food & Dining · BCA · receipt attached
```
and the system maintains:

```
Transactions row:
  Date  Merchant   Category  Amount   receipt_link (→ Drive)
  2026-09-10  Restaurant  Food  45000  [open]

Monthly Summary (Sep 2026):
  Income   Rp X
  Expenses Rp Y
  Net      Rp Z
  Food     Rp A   Transport Rp B  Shopping Rp C  …
```

Answering, instantly:
> How much did I spend this month? · What on? · What did I earn? · Net cash
> flow? · Top categories? · Missing receipts? · Where's this receipt? ·
> Reconciled? · Change vs last month?
