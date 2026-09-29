"""Stable, cwd-independent JSON CLI for agents and humans."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .config import ConfigError, load_settings
from .domain import InputError
from .service import FinanceService
from .storage import Ledger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="finance-agent")
    parser.add_argument("--json", action="store_true", help="one JSON object for agents")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("health", help="check local ledger and configuration")
    record = sub.add_parser("record", aliases=["add"], help="record a transaction")
    record.add_argument(
        "--type",
        default="Expense",
        choices=[
            "Expense",
            "Income",
            "Transfer",
            "Refund",
            "expense",
            "income",
            "transfer",
            "refund",
        ],
    )
    record.add_argument("--amount", required=True)
    record.add_argument("--date")
    record.add_argument("--merchant", default="")
    record.add_argument("--description", default="")
    record.add_argument("--category", default="")
    record.add_argument("--account", default="")
    record.add_argument("--to-account", default="")
    record.add_argument("--payment-method", default="")
    record.add_argument("--notes", default="")
    record.add_argument("--currency")
    record.add_argument("--receipt", help="local file path or uploaded receipt ID")
    record.add_argument("--key", help="source message reference for idempotency")
    upload = sub.add_parser("upload", help="archive a PDF or image and get its receipt ID")
    upload.add_argument("file")
    upload.add_argument("--date")
    upload.add_argument("--merchant", default="")
    upload.add_argument("--amount", default="")
    upload.add_argument("--category", default="", help="accepted for legacy CLI compatibility")
    find = sub.add_parser("find", help="search local transactions")
    find.add_argument("--q", default="")
    find.add_argument("--month")
    find.add_argument("--no-receipt", action="store_true")
    find.add_argument("--limit", type=int, default=50)
    missing = sub.add_parser("missing", help="expenses without receipts")
    missing.add_argument("--month")
    duplicates = sub.add_parser("duplicates", help="possible duplicate entries")
    duplicates.add_argument("--month")
    summary = sub.add_parser("summary", help="monthly totals grouped by currency")
    summary.add_argument("--month")
    reconcile = sub.add_parser("reconcile", help="mark a month reconciled")
    reconcile.add_argument("--month", required=True)
    attach = sub.add_parser("attach", help="attach a receipt to an existing transaction")
    attach.add_argument("--id", required=True)
    attach.add_argument("--receipt", required=True)
    sub.add_parser("categories", help="available categories")
    create = sub.add_parser("create-category", help="add a category")
    create.add_argument("--name", required=True)
    return parser


def dispatch(service: FinanceService, args: argparse.Namespace) -> dict[str, Any]:
    command = args.command
    if command == "health":
        return {
            "status": "ok",
            "data_dir": str(service.settings.data_dir),
            "reply": "Local finance ledger is ready.",
        }
    if command in {"record", "add"}:
        return service.record(
            amount_text=args.amount,
            type_text=args.type,
            date_text=args.date,
            merchant=args.merchant,
            description=args.description,
            category=args.category,
            account=args.account,
            to_account=args.to_account,
            payment_method=args.payment_method,
            notes=args.notes,
            currency=args.currency,
            receipt=args.receipt,
            external_ref=args.key,
        )
    if command == "upload":
        return service.upload(
            args.file, date_text=args.date, merchant=args.merchant, amount_text=args.amount
        )
    if command == "find":
        return service.find(
            query=args.q, month_text=args.month, missing_receipt=args.no_receipt, limit=args.limit
        )
    if command == "missing":
        return service.find(month_text=args.month, missing_receipt=True)
    if command == "duplicates":
        return service.duplicates(args.month)
    if command == "summary":
        return service.summary(args.month)
    if command == "reconcile":
        return service.reconcile(args.month)
    if command == "attach":
        return service.attach(args.id, args.receipt)
    if command == "categories":
        return service.categories()
    if command == "create-category":
        return service.create_category(args.name)
    raise InputError(f"Unknown command: {command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        ledger = Ledger(settings.data_dir / "finance.sqlite3")
        try:
            result = dispatch(FinanceService(settings, ledger), args)
        finally:
            ledger.close()
    except (ConfigError, InputError) as exc:
        result = {"status": "error", "kind": "input", "reply": str(exc)}
        code = 2
    except OSError as exc:
        result = {"status": "error", "kind": "storage", "reply": str(exc)}
        code = 4
    else:
        code = 0
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(result["reply"])
    return code


if __name__ == "__main__":
    sys.exit(main())
