# Contributing

Install with `python -m pip install -e '.[dev]'` on Python 3.11+. Run `ruff check finance_agent tests`, `ruff format --check finance_agent tests`, and `python -m pytest -q` before a pull request. Put finance rules in `service.py` or `domain.py`, SQL in `storage.py`, file handling in `receipts.py`, and command syntax in `cli.py`. Keep JSON statuses stable and test changed financial behavior.

Use sample data in tests and issues. Do not commit real receipts, account details, `.env`, or database files.
