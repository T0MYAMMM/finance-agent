# Contributing

Install with `python -m pip install -e '.[dev]'` on Python 3.11+ (add `.[drive]` for receipt uploads). Run `ruff check .`, `ruff format --check .`, and `python -m pytest -q` before a pull request. Put API calls in `client.py`, amount and date parsing in `parsing.py`, recording and edits in `capture.py`, report answers in `queries.py`, Drive uploads in `receipts.py`, and command syntax in `cli.py`. Keep CLI command names and JSON `status` values stable and test changed financial behavior.

Use sample data in tests and issues. Tests must not reach the network; use the fake doeedd, Drive and Sheets clients. Do not commit real receipts, account details, `.env`, `config.json`, or local state files.
