# Security

The assistant reads and writes the owner's ledger in doeedd with a bearer token and stores receipts in Google Drive. Keep the token and the Drive and Sheet ids in `~/.hermes/.env` (or the process environment), never in the repository. Do not post receipts, private transactions, account numbers, or credentials in public issues. Report vulnerabilities privately to the repository maintainer and rotate any exposed credential immediately.

An earlier commit exposed private Google resource ids. [docs/SECURITY_CLEANUP.md](docs/SECURITY_CLEANUP.md) has the history purge and access checklist. CI scans every commit with gitleaks (`.gitleaks.toml`).
