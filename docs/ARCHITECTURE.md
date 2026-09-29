# Architecture and restructuring plan

## Branch boundary

| Branch | Purpose | Source of truth |
| --- | --- | --- |
| `main` | Generic agent application, runnable without external services | Local SQLite ledger |
| `feat/doeedd-integration` | Full doeedd and Hermes adapter, including its existing commands | doeedd API |

The prior Google Sheets scripts are available in Git history before the `main` refactor. A direct migration from that Sheet into this local ledger is not provided. Export and validate the source data before any import is added.

## Application flow

```text
Agent or human
    │ JSON CLI
    ▼
cli.py ──► service.py ──► domain.py
                │
                ├──► storage.py ──► SQLite
                └──► receipts.py ──► local files
```

The CLI parses arguments and formats one response. The service owns transaction rules, idempotency, duplicate marking, queries, summaries, and reconciliation. The storage adapter owns SQL and transactions. Receipt storage owns file validation and archiving. Configuration resolves defaults and environment settings without relying on the working directory.

## Stable behavior to preserve

- `record`/`add`, `upload`, `missing`, `duplicates`, `reconcile`, and `categories` cover the earlier generic helper capabilities. `find`, `summary`, `attach`, `health`, and `create-category` support agent workflows.
- Every amount is positive. Type controls direction. Transfer has distinct source and destination accounts and is excluded from income and expense. Refund reduces expense. Currencies are never summed together.
- `--key` is unique per source event. Retrying it returns `replayed`. Similar entries are flagged, never auto-deleted.
- Receipt files are copied to the data directory; a failed validation does not create a transaction. A receipt ID can be reused in a later `record` or `attach` call.
- CLI responses contain `status` and `reply`; invalid input uses exit code 2.

## Extension path

1. Add import/export services with explicit schema mapping and dry-run validation for legacy Sheets data.
2. Define a storage protocol at the service boundary when a second generic ledger adapter is needed; keep transaction and receipt identifiers stable.
3. Add budget, asset, and notification modules as separate services, each with its own persistence tables and CLI registration.
4. Add schema migrations before changing stored columns. Keep backups and test upgrades against a copy of existing data.
5. Let provider branches implement adapters. Do not put provider names, URLs, or credentials in `main`'s finance rules.

The current SQLite schema is initialized on first use. There is no schema migration framework yet, so stored schema changes require a migration in the next release.
