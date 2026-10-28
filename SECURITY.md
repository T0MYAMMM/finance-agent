# Security

The generic application stores its ledger and receipts locally. Back up and protect the data directory; limit access to the computer account that runs the agent. Do not post receipts, private transactions, account numbers, or credentials in public issues. Report vulnerabilities privately to the repository maintainer and rotate any exposed credential immediately.

An earlier commit exposed private Google resource ids. [docs/SECURITY_CLEANUP.md](docs/SECURITY_CLEANUP.md) has the history purge and access checklist. CI scans every commit with gitleaks (`.gitleaks.toml`).
