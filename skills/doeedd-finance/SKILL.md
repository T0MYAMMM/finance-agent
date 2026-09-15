---
name: doeedd-finance
description: Use for any message about the owner's money — receipts, spending, income, transfers, savings, budgets, payday, balances, net worth or goals. Records and answers through doeedd (the owner's budgeting system) with one short reply, asking only when a field is truly ambiguous.
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [finance, budgeting, receipts, doeedd, idr, indonesia]
    related_skills: [receipt-ocr-entry]
---

# doeedd finance assistant

## Overview

doeedd is the owner's budgeting platform and the **only** source of truth for money. Every read
and write goes through one CLI, which resolves names, checks duplicates, uploads receipts to
Google Drive, and returns JSON with a `status` to branch on and a `reply` to relay:

```bash
DOEEDD="$HOME/.hermes/hermes-agent/venv/bin/python $HOME/finance-system/doeedd.py --json"
```

Never add up transactions yourself and never edit the Google Sheet: it is being retired.
Totals, budget usage, payday and net worth come from doeedd reports only.

## When to Use

- The owner sends a receipt, order screenshot, bank transfer proof or balance screenshot.
- The owner says they spent, received, moved, saved or topped up money ("kopi 25rb gopay").
- The owner asks about budget, spending, payday, savings, net worth, goals or past entries.
- The owner says undo / hapus / salah / restore about an entry.

Don't use for: investment advice, other people's finances, or editing budget plans without an
explicit request (plans and categories always need a yes first).

## Always

1. **Today** is Asia/Jakarta. The CLI knows; don't compute dates from system time.
2. **Amounts**: pass the owner's text as-is to `--amount` (`25rb`, `1,5jt`, `Rp45.000`). If the
   CLI says `ambiguous`, ask which amount. Never change an amount to make a request pass.
3. **Reply** with the `reply` field, in the owner's language, one or two lines. Don't moralise.
4. **Never** put card numbers, OTPs, PINs or passwords into merchant, description or notes.
5. **Key**: when the Telegram chat id and message id are visible, pass
   `--key telegram:<chat_id>:<message_id>:<n>` (n = 1, 2… for several entries in one message).
   Otherwise omit `--key`; the CLI makes one.

## Capture: text or voice

1. Split the message into entries (one per amount). For each, decide `--type`:
   - spent / bayar / beli → `expense`
   - gaji / gajian / dapat / terima → `income` (needs an income category)
   - topup / pindah / transfer ke rekening sendiri / **nabung** / bayar kartu kredit → `transfer`
     with `--account <from> --to-account <to>` (savings are transfers, never expenses)
2. Run `$DOEEDD add --amount "<text>" [--date "<text>"] [--merchant "<who>"]`
   `[--description "<what>"] [--category "<word>"] [--account "<word>"]`
   `[--payment-method "<QRIS|BCA Debit|…>"] [--subcategory "<Coffee…>"] [--notes "<where>"] [--key …]`.
   Give the owner's own words for category/account; the CLI maps them.
3. Branch on `status` (done when every entry has a final status):

| status | do |
|---|---|
| `created` | relay `reply` |
| `replayed` | relay `reply` (it was already logged; nothing new written) |
| `needs_input` | ask ONE question covering every item in `missing`, offering `options` as buttons (clarify); then rerun `add` with the answers |
| `possible_duplicate` | ask "already logged … log again?"; on yes rerun with `--allow-duplicate`, on no stop |
| `queued` | relay `reply`; later run `$DOEEDD flush` |
| `receipt_failed` | ask whether to log without the receipt |
| `error` + `kind: validation` | read `error.errors[].path`, ask the one question that fixes it |
| `error` + `kind: unauthorized` | tell the owner the doeedd token needs renewal; stop |

## Capture: receipt photo, screenshot or PDF

1. Find the file: Telegram images are in `~/.hermes/cache/images/` (newest first).
2. Read it. `vision_analyze` often fails on local files — then OCR with tesseract exactly as in
   `references/rules.md#ocr`. OCR text is a draft: treat it as medium confidence.
3. Extract merchant, date, total and order lines. One screenshot may hold several orders: one
   `add` per order, all with the same `--receipt` path. Skip orders already logged
   (the CLI's duplicate check will catch overlaps between screenshots).
4. Unreadable or two possible totals → ask; never guess the amount. Not a receipt → say so.
5. Record with `--receipt <path>` (the CLI uploads to Drive first, then records and links).

## Corrections

| owner says | run |
|---|---|
| undo / hapus yang tadi | `$DOEEDD undo` (removes every entry of the last message) |
| balikin / restore | `$DOEEDD restore` |
| salah, 35rb | `$DOEEDD edit --amount 35rb` |
| masuk shopping aja / pakai BCA | `$DOEEDD edit --category shopping --learn` / `--account BCA --learn` |
| this receipt is for that entry | `$DOEEDD attach --receipt <path>` (or `--id <id>`) |

`--learn` makes the merchant remember the corrected category/account, so the same mistake
happens once. If `edit` says `needs_input` with `id`, find the entry with `find` and pass `--id`.

## Questions

| asked | run |
|---|---|
| sisa budget, kapan gajian | `$DOEEDD home` |
| food udah berapa / pengeluaran terbesar | `$DOEEDD monthly [--category food] [--month YYYY-MM]` |
| dibanding bulan lalu | `$DOEEDD trend` |
| plan bulan ini | `$DOEEDD summary` |
| transaksi grab minggu ini / struk tanggal 3 | `$DOEEDD find --q grab --from "senin" [--no-receipt]` |
| net worth / progress target | `$DOEEDD networth` / `$DOEEDD goals` |

Relay `reply`; offer more detail only if asked. For `find`, a total is only "the sum of the listed
transactions", never a monthly figure.

## Always ask first

New categories or accounts, budget or goal changes, deleting anything other than the last
capture, and anything dated in the future (`missing: date` → confirm, then `--allow-future`).

## Common Pitfalls

1. Recording savings or top-ups as expenses — they are transfers; expenses would inflate spending.
2. Guessing a category for an unknown merchant — ask once, then `edit --learn` or
   `$DOEEDD remember --merchant "<m>" --category <c> --account <a>` so it is known next time.
3. Summing `find` results and calling it "spending this month" — use `monthly`.
4. Re-sending a corrected entry with the same `--key` — use `edit`, not `add`.
5. Writing to the old Google Sheet or running `finance.py record` — doeedd is the ledger now.

## Verification Checklist

- [ ] Every entry in the message ended `created`, `replayed`, `queued`, or was explicitly skipped.
- [ ] Every receipt sent was passed as `--receipt` (or the owner declined).
- [ ] The reply shown is the CLI's `reply`, not a recomputed number.
