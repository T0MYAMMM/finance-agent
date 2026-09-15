# Finance Agent

The Hermes assistant for the owner's money. Hermes (Telegram) understands messages, receipts and
questions; this repo gives it one command line, `doeedd.py`, that records and reads everything in
**doeedd**, the owner's budgeting platform. Receipts stay in Google Drive and are linked to
transactions in doeedd.

Plan and decisions: `../doeedd-agent/` (start at its `README.md`).

## How it fits together

```
Telegram ─► Hermes gateway ─► skill doeedd-finance ─► doeedd.py ─► doeedd API (Railway)
                                                          └────► Google Drive (receipts)
```

- **doeedd is the ledger.** Totals, budget usage, payday and net worth come from its reports;
  the CLI never recomputes them.
- **The Google Sheet is retired.** `finance.py` and `build_finance_system.py` are kept until the
  migration is verified, then removed.

## Files

| Path | Purpose |
|---|---|
| `doeedd.py` | CLI entry point used by the skill |
| `doeedd_agent/client.py` | doeedd API client (bearer token, retries for safe calls only) |
| `doeedd_agent/parsing.py` | Indonesian amounts (`25rb`, `1,5jt`, `Rp45.000`) and dates (`kemarin`, `3/9`) |
| `doeedd_agent/capture.py` | Record, edit, undo, restore, attach; duplicate check; offline outbox |
| `doeedd_agent/queries.py` | Answers from doeedd reports (home, monthly, trend, find…) |
| `doeedd_agent/aliases.py` | Word → category/account memory and merchant defaults |
| `doeedd_agent/receipts.py` | Drive upload into `Personal Finance/Receipts/YYYY/MM-Month/` |
| `doeedd_agent/migration.py` | One-time move of the Sheets ledger into doeedd |
| `doeedd_agent/seed.py` | Default categories and accounts |
| `skills/doeedd-finance/` | The Hermes skill (`SKILL.md`, `references/rules.md`) |
| `tests/` | pytest suite (no network) |
| `finance.py`, `build_finance_system.py`, `gws_auth.py`, `DESIGN.md`, `MANUAL.md` | Legacy Sheets system |

## Setup on the Hermes VM

```bash
cd ~/finance-system                                     # this repo
PY=~/.hermes/hermes-agent/venv/bin/python               # Python 3.11 with httpx + Google client
ln -sfn ~/finance-system/skills/doeedd-finance ~/.hermes/skills/finance/doeedd-finance
```

Configuration lives in `~/.hermes/.env` (never in git):

```
DOEEDD_BASE_URL=https://<doeedd host>/api/v1
DOEEDD_TOKEN=doe_…            # create with doeedd's `pnpm cli create-token --name hermes-prod`
DOEEDD_TIMEZONE=Asia/Jakarta
```

Local state (alias overrides, last capture for undo, offline outbox) is kept in
`~/.hermes/doeedd/`, readable by the owner only.

## Everyday commands

```bash
$PY doeedd.py health                                   # token and API check
$PY doeedd.py add --amount 25rb --merchant "Tomoro Coffee" --key telegram:<chat>:<msg>:1
$PY doeedd.py add --type transfer --amount 500rb --account BCA --to-account GoPay
$PY doeedd.py add --amount "Rp45.000" --category food --receipt ~/.hermes/cache/images/x.jpg
$PY doeedd.py undo | restore | edit --category shopping --learn
$PY doeedd.py home | monthly [--category food] | summary | trend | networth | goals
$PY doeedd.py find --q grab --from senin [--no-receipt]
$PY doeedd.py flush                                    # send writes queued while offline
$PY doeedd.py migrate-sheets [--apply]                 # one-time ledger move (dry run by default)
```

Add `--json` for machine-readable output: every command returns `status` and `reply`.

## Development

```bash
python -m pip install httpx pytest ruff
ruff format doeedd_agent tests && ruff check doeedd_agent tests
python -m pytest -q
```

Deploy: commit, then update `~/finance-system` on the VM (`git pull`, or a git bundle when the
branch is not pushed). Skill files are picked up automatically; restart the gateway
(`systemctl --user restart hermes-gateway`) only if a new skill does not appear.
