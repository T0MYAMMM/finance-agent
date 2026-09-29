# Agent guide

This `main` branch is the generic finance agent. Use `finance-agent --json ...` after installation, from any working directory. `python -m finance_agent --json ...` is equivalent. The doeedd adapter and Hermes specific skill live on `feat/doeedd-integration`.

## User workflow

1. Read `README.md` and run `finance-agent --json health` to check the local ledger.
2. Use the user's exact amount/date; ask if ambiguous. Dates accepted by the CLI are `YYYY-MM-DD`. Do not invent a missing category, account, or receipt.
3. For each source message, pass a stable `--key` so a retry returns `replayed` instead of creating another transaction.
4. Branch on `status`. `created` means saved, `replayed` means already saved, `uploaded` means a receipt was archived, and `error` means no new transaction was saved. A created transaction can have `possible_duplicate: true`; bring it to the user's attention.
5. Use `summary` for monthly totals. `find` is a list, not a monthly report. Transfers between own accounts are `Transfer`, not income or expense. Refunds reduce expense totals.
6. Reconcile only after the owner has reviewed the month; `reconcile --month` marks matching transactions reconciled.

## Repository map

- `finance_agent/cli.py`: stable command syntax, JSON and exit codes.
- `finance_agent/service.py`: use cases and finance rules.
- `finance_agent/domain.py`: amount, date, type and category definitions.
- `finance_agent/storage.py`: SQLite ledger adapter.
- `finance_agent/receipts.py`: local receipt archive adapter.
- `finance_agent/config.py`: environment and paths.
- `finance.py`: compatibility launcher.
- `tests/`: behavior and CLI contract tests.

Keep business decisions in `service.py`/`domain.py`, persistence in `storage.py`, and transport in `cli.py`. Preserve command names and JSON statuses when extending the app. Add focused tests for money semantics, idempotency, duplicate handling, and data changes. Run the checks in `README.md` before committing. Do not commit `.env`, the SQLite database, or receipts.
