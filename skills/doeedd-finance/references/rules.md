# doeedd-finance: detailed rules

## Confidence policy

| Level | When | Do |
|---|---|---|
| High | amount certain; category and account resolved by the CLI; date not in the future | run `add` directly; relay reply (it ends with "Reply undo to remove") |
| Medium | OCR'd values, or the CLI returned `needs_input` / `possible_duplicate` | one question with 2–4 buttons, best guess first |
| Low | amount unreadable or two totals, not a receipt, future date | ask; write nothing until answered |

Every agent entry starts unreviewed, so the weekly review catches anything auto-recorded wrongly.

## Money semantics

- **Savings are transfers** into the savings account ("nabung 2jt ke Jenius" →
  `--type transfer --account BCA --to-account Jenius`). doeedd counts actual savings from
  transfers into accounts that fund a saving budget line.
- **E-wallet top-ups** are transfers (BCA → GoPay). A top-up admin fee is a separate small expense.
- **Credit card**: a purchase is an expense from the `Credit Card` account; paying the bill is a
  transfer from the bank to `Credit Card`. PayLater works the same way.
- **Refunds**: find the original expense (`find --q <merchant>`), then `edit --amount <net>` for a
  partial refund or `undo --id <id>` for a full refund, and note it with `--notes`.
- **Split bill** ("makan 300rb, bagianku 100rb"): record only the owner's share; mention the rest
  in `--notes`.
- **Income** must use an income category (Monthly Salary, Freelance Fee).

## Amount and date words

Amounts: `25k`, `25rb`, `25 ribu` = 25.000 · `1,5jt` / `1.5jt` = 1.500.000 · `Rp45.000` /
`45.000` / `45,000` = 45.000. Decimals without a unit (`45.5`) are ambiguous — ask.

Dates: `hari ini`, `tadi`, `kemarin`, `kemarin lusa`, `3 hari lalu`, `senin (lalu)`, `3/9`
(day first), `14 Sep`, `2026-09-14`, receipt stamps like `14/09/26 12:31`. `minggu lalu` means
"last week" — ask which day. `$DOEEDD parse amount "<text>"` / `parse date "<text>"` shows how a
value is understood without writing anything.

## OCR

When `vision_analyze` cannot read a local image:

```bash
cd ~/.hermes/cache/images
~/.hermes/hermes-agent/venv/bin/python - <<'PY'
from PIL import Image, ImageEnhance, ImageOps
name = "IMG.jpg"  # the receipt file
image = Image.open(name).convert("L")
image = image.resize((image.width * 2, image.height * 2), Image.LANCZOS)
image = ImageEnhance.Sharpness(ImageOps.autocontrast(image)).enhance(2.0)
image.save("/tmp/enhanced.jpg")
PY
tesseract /tmp/enhanced.jpg stdout --psm 4   # try --psm 6 for block layouts
```

Indonesian receipts: `Rp 16.200` = 16.200 (dot = thousands); dates are day first. Payment method
and account are often not on the image — use the merchant's remembered defaults (the CLI does) or
ask. Delete `/tmp/enhanced.jpg` afterwards.

## Reply style

- Capture: `✅ Rp45.000 · Food · BCA · 10 Sep — Starbucks` + budget line when present.
- Several entries from one message: one line listing them, not one message each.
- Questions: one headline and at most three bullets.
- Mirror the owner's language (Indonesian or English). No judgement about spending.
