# Finance Agent Toolkit

A local, provider neutral finance application built for agents and people. `main` has no doeedd dependency. The optional doeedd integration is maintained on the [`feat/doeedd-integration` branch](../../tree/feat/doeedd-integration).

The application records expenses, income, transfers and refunds; archives receipts; finds missing receipts and possible duplicates; reports monthly totals; and marks a month reconciled. Data stays in a local SQLite ledger. Its CLI returns one JSON object per call for agent use.

Google Drive receipts and Google Sheets migration are on `feat/doeedd-integration`. That branch includes `docs/GOOGLE_CLOUD_SETUP.md` with the Google Cloud and OAuth setup steps.

## Quick start

Requires Python 3.11+. No cloud account is needed.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env                    # optional; defaults work without it
finance-agent --json health
finance-agent --json record --type Expense --amount 45000 --merchant "Cafe" --category "Food & Dining" --key chat:123:1
finance-agent --json summary
```

The installed `finance-agent` command and `python -m finance_agent` work from any directory. `python finance.py` is a compatibility entry point when run from this checkout. CLI help: `finance-agent --help` and `finance-agent record --help`.

## Everyday commands

```bash
finance-agent --json categories
finance-agent --json record --amount 45000 --merchant Cafe --category "Food & Dining" --account BCA
finance-agent --json record --type Transfer --amount 500000 --account BCA --to-account GoPay
finance-agent --json upload /path/to/receipt.jpg --merchant Cafe --amount 45000
finance-agent --json record --amount 45000 --receipt <receipt_id> --key chat:123:2
finance-agent --json find --q Cafe --month 2026-09
finance-agent --json missing --month 2026-09
finance-agent --json duplicates --month 2026-09
finance-agent --json summary --month 2026-09
finance-agent --json reconcile --month 2026-09
finance-agent --json attach --id <transaction_id> --receipt /path/to/receipt.pdf
```

`record` is also available as `add`. Use a stable `--key` from the source message to avoid double entry on retries. A potential duplicate is **recorded and flagged** for review; nothing is silently deleted. `summary` separates currencies and treats transfers as neutral and refunds as reduced expenses. The command reports `status` and `reply`; use the structured fields for decisions. Invalid input returns exit code 2 and `status: error`.

## Configuration and files

Defaults are `~/.local/share/finance-agent`, `IDR`, and `Asia/Jakarta`. Copy [.env.example](.env.example) to `.env` in this checkout, put it at `~/.config/finance-agent/.env` for an installed command, or set environment variables in the process. Precedence: process environment, checkout `.env`, user config `.env`, defaults. Set `FINANCE_DATA_DIR` to an absolute path if changing storage. Never commit private data.

The data directory contains `finance.sqlite3` and `receipts/YYYY/MM/`. Keep a backup of the **whole directory**. Receipt filenames use sanitized merchant and amount plus a content hash. The original receipt file is left untouched. No network request is made by the generic app.

## For agents and maintainers

Read [AGENTS.md](AGENTS.md) before using this repository from an agent. [Architecture and restructuring plan](docs/ARCHITECTURE.md) maps modules, extension points, and the branch split. Finance data is personal: keep tokens, account numbers, receipts, and real transaction examples out of GitHub issues.

```bash
python -m pip install -e '.[dev]'
ruff check finance_agent tests
ruff format --check finance_agent tests
python -m pytest -q
```
