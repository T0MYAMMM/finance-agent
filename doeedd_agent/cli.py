"""``doeedd.py``: the command line the Hermes skill drives.

Every command prints one JSON object with ``--json`` (``status`` to branch on, ``reply`` to
relay) or just the reply otherwise. Exit codes: 0 handled (including ``needs_input``,
``possible_duplicate`` and ``queued``), 2 bad input or configuration, 3 authentication,
4 other doeedd errors.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from . import queries
from .aliases import AliasStore
from .capture import CaptureRequest, EditRequest, Outcome, Recorder
from .client import DoeeddClient, DoeeddError
from .config import ConfigError, Settings, load_settings
from .outbox import Outbox
from .parsing import AmbiguousError, ParseError, parse_amount, parse_date, today_in
from .migration import (
    apply_migration,
    expense_total,
    ledger_spreadsheet_id,
    plan_migration,
    read_ledger,
)
from .receipts import ReceiptUploadError, google_service, upload_receipt
from .seed import apply_seed, plan_seed
from .state import StateStore

log = logging.getLogger("doeedd_agent")


@dataclass
class App:
    """Configured collaborators for one command."""

    settings: Settings
    client: DoeeddClient
    aliases: AliasStore
    recorder: Recorder
    today: date


def open_app(args: argparse.Namespace) -> App:
    """Build the client and the local stores from configuration."""
    settings = load_settings()
    client = DoeeddClient(settings.base_url, settings.token)
    state_dir = settings.state_dir
    aliases = AliasStore(state_dir / "aliases.json")
    recorder = Recorder(
        client,
        aliases,
        StateStore(state_dir / "state.json"),
        Outbox(state_dir / "outbox.json"),
        uploader=upload_receipt,
    )
    today = (
        parse_date(args.today, today_in(settings.timezone))
        if getattr(args, "today", None)
        else today_in(settings.timezone)
    )
    return App(settings, client, aliases, recorder, today)


def emit(result: dict[str, Any], as_json: bool) -> None:
    """Print a result for the skill (JSON) or a person (reply text)."""
    if as_json:
        print(json.dumps(result, ensure_ascii=False, default=str))
    else:
        print(result.get("reply") or json.dumps(result, ensure_ascii=False, indent=2, default=str))


def _month(text: str | None, today: date) -> tuple[int, int]:
    if not text:
        return today.year, today.month
    try:
        year, month = (int(part) for part in text.split("-", 1))
        date(year, month, 1)
    except ValueError as exc:
        raise ParseError(f"month must be YYYY-MM, got {text!r}") from exc
    return year, month


def _amount(text: str | None) -> int | None:
    return parse_amount(text) if text is not None else None


# -- command handlers ------------------------------------------------------------------------


def cmd_parse(args: argparse.Namespace) -> dict[str, Any]:
    """Debug helper: how would a word be parsed?"""
    if args.what == "amount":
        value: Any = parse_amount(args.text)
        return {"status": "ok", "value": value, "reply": str(value)}
    base = date.fromisoformat(args.today) if args.today else date.today()
    parsed = parse_date(args.text, base)
    return {"status": "ok", "value": parsed.isoformat(), "reply": parsed.isoformat()}


def cmd_health(app: App, args: argparse.Namespace) -> dict[str, Any]:
    health = app.client.health()
    me = app.client.me()
    reply = f"doeedd {health['status']} (db {health['db']}), signed in as {me['email']}"
    return {"status": "ok", "reply": reply, "user": me["email"], "settings": me["settings"]}


def cmd_context(app: App, args: argparse.Namespace) -> dict[str, Any]:
    return queries.context(app.client, app.aliases, app.today)


def cmd_home(app: App, args: argparse.Namespace) -> dict[str, Any]:
    return queries.home(app.client, app.today)


def cmd_monthly(app: App, args: argparse.Namespace) -> dict[str, Any]:
    year, month = _month(args.month, app.today)
    return queries.monthly(app.client, year, month, args.category)


def cmd_summary(app: App, args: argparse.Namespace) -> dict[str, Any]:
    year, month = _month(args.month, app.today)
    return queries.summary(app.client, year, month)


def cmd_trend(app: App, args: argparse.Namespace) -> dict[str, Any]:
    end = args.to or f"{app.today.year}-{app.today.month:02d}"
    if args.start:
        start = args.start
    else:
        year, month = _month(end, app.today)
        index = year * 12 + month - 1 - 5
        start = f"{index // 12}-{index % 12 + 1:02d}"
    return queries.trend(app.client, start, end)


def cmd_networth(app: App, args: argparse.Namespace) -> dict[str, Any]:
    return queries.net_worth(app.client, app.today)


def cmd_goals(app: App, args: argparse.Namespace) -> dict[str, Any]:
    return queries.goals(app.client, app.today)


def cmd_find(app: App, args: argparse.Namespace) -> dict[str, Any]:
    filters: dict[str, Any] = {"q": args.q, "type": args.type}
    if args.month:
        filters["year"], filters["month"] = _month(args.month, app.today)
    else:
        filters["from"] = parse_date(args.start, app.today) if args.start else None
        filters["to"] = parse_date(args.end, app.today) if args.end else None
    if args.unreviewed:
        filters["reviewed"] = False
    if args.no_receipt:
        filters["has_attachment"] = False
    if args.source:
        filters["source"] = args.source
    return queries.find(
        app.client,
        app.recorder.names(),
        filters,
        category=args.category,
        account=args.account,
        limit=args.limit,
    )


def cmd_add(app: App, args: argparse.Namespace) -> dict[str, Any]:
    try:
        amount = parse_amount(args.amount)
    except AmbiguousError as exc:
        return {"status": "needs_input", "missing": ["amount"], "reply": f"Which amount? {exc}"}
    occurred_on = parse_date(args.date, app.today) if args.date else app.today
    request = CaptureRequest(
        amount=amount,
        occurred_on=occurred_on,
        type=args.type,
        category=args.category,
        account=args.account,
        to_account=args.to_account,
        merchant=args.merchant,
        description=args.description,
        subcategory=args.subcategory,
        payment_method=args.payment_method,
        notes=args.notes,
        key=args.key,
        receipt_path=Path(args.receipt) if args.receipt else None,
        receipt_file_id=args.receipt_id,
        reviewed=args.reviewed,
        allow_duplicate=args.allow_duplicate,
        allow_future=args.allow_future,
    )
    return app.recorder.capture(request, app.today).to_dict()


def cmd_edit(app: App, args: argparse.Namespace) -> dict[str, Any]:
    reviewed = True if args.reviewed else False if args.unreviewed else None
    change = EditRequest(
        amount=_amount(args.amount),
        occurred_on=parse_date(args.date, app.today) if args.date else None,
        category=args.category,
        account=args.account,
        to_account=args.to_account,
        merchant=args.merchant,
        description=args.description,
        subcategory=args.subcategory,
        payment_method=args.payment_method,
        notes=args.notes,
        reviewed=reviewed,
        learn=args.learn,
    )
    return app.recorder.edit(args.id, change).to_dict()


def cmd_undo(app: App, args: argparse.Namespace) -> dict[str, Any]:
    return app.recorder.undo(args.id).to_dict()


def cmd_restore(app: App, args: argparse.Namespace) -> dict[str, Any]:
    return app.recorder.restore().to_dict()


def cmd_attach(app: App, args: argparse.Namespace) -> dict[str, Any]:
    path = Path(args.receipt) if args.receipt else None
    return app.recorder.attach(args.id, path, args.receipt_id).to_dict()


def cmd_flush(app: App, args: argparse.Namespace) -> dict[str, Any]:
    return app.recorder.flush().to_dict()


def cmd_aliases(app: App, args: argparse.Namespace) -> dict[str, Any]:
    if args.action == "show":
        return {
            "status": "ok",
            "aliases": app.aliases.merged(),
            "overrides": app.aliases.overrides(),
        }
    if args.action == "set":
        value = None if args.value in (None, "", "ask") else args.value
        app.aliases.set(args.section, args.key, value)
        return {"status": "ok", "reply": f"Remembered {args.key!r} → {value or 'always ask'}"}
    removed = app.aliases.unset(args.section, args.key)
    return {"status": "ok", "reply": f"{'Forgot' if removed else 'No override for'} {args.key!r}"}


def cmd_remember(app: App, args: argparse.Namespace) -> dict[str, Any]:
    app.aliases.remember_merchant(
        args.merchant,
        category=args.category,
        account=args.account,
        subcategory=args.subcategory,
        payment_method=args.payment_method,
    )
    return {"status": "ok", "reply": f"Remembered defaults for {args.merchant}"}


def cmd_seed(app: App, args: argparse.Namespace) -> dict[str, Any]:
    plan = plan_seed(app.client)
    if not args.apply:
        names = [c["name"] for c in plan["categories"]] + [a["name"] for a in plan["accounts"]]
        reply = "Would create: " + (", ".join(names) if names else "nothing (all present)")
        return {"status": "dry_run", "plan": plan, "reply": reply}
    created = apply_seed(app.client, plan)
    total = len(created["categories"]) + len(created["accounts"])
    return {"status": "seeded", "created": created, "reply": f"Created {total} item(s)"}


def cmd_migrate_sheets(app: App, args: argparse.Namespace) -> dict[str, Any]:
    """Move the Personal Finance Tracker ledger into doeedd (dry run unless --apply)."""
    spreadsheet_id = args.spreadsheet_id or ledger_spreadsheet_id()
    rows = read_ledger(google_service("sheets", "v4"), spreadsheet_id)
    names = app.recorder.names()
    planned, skipped = plan_migration(rows, names, app.aliases.merged())
    report: dict[str, Any] = {
        "ledger_rows": len(rows),
        "ready": [
            {
                "ref": row.ref,
                "line": f"{row.body['occurred_on']} {row.body['amount']} "
                f"{names.name(row.body['category_id']) or 'transfer'} "
                f"{names.name(row.body['account_id'])} {row.body['merchant'] or ''}".rstrip(),
                "receipt": row.attachment is not None,
                "warnings": row.warnings,
            }
            for row in planned
        ],
        "skipped": [
            {"ref": row.ref, "sheet_row": row.sheet_row, "reasons": row.reasons} for row in skipped
        ],
    }
    skipped_lines = [
        f"{row.ref} (row {row.sheet_row}): {'; '.join(row.reasons)}" for row in skipped
    ]
    if not args.apply:
        reply = f"Dry run: {len(planned)} of {len(rows)} ledger rows ready."
        if skipped_lines:
            reply += " Needs a decision: " + " | ".join(skipped_lines)
        return {"status": "dry_run", **report, "reply": reply}

    result = apply_migration(app.client, planned)
    imported = [
        item
        for item in app.client.transactions({"source": "import"})
        if (item.get("external_ref") or "").startswith("sheets:") and item["type"] == "expense"
    ]
    skipped_refs = {row.ref for row in skipped}
    verification = {
        "ledger_expense_total": expense_total(rows),
        "skipped_expense_total": expense_total(
            [row for row in rows if str(row.get("transaction_id") or "") in skipped_refs]
        ),
        "doeedd_imported_expense_total": sum(item["amount"] for item in imported),
    }
    verification["matches"] = (
        verification["ledger_expense_total"] - verification["skipped_expense_total"]
        == verification["doeedd_imported_expense_total"]
    )
    reply = (
        f"Migrated {len(result['created'])} row(s) ({len(result['already_present'])} already there), "
        f"linked {result['receipts_linked']} receipt(s); totals match: {verification['matches']}."
    )
    if skipped_lines:
        reply += " Still needs a decision: " + " | ".join(skipped_lines)
    return {"status": "migrated", **report, **result, "verification": verification, "reply": reply}


# -- parser -----------------------------------------------------------------------------------


def _capture_fields(parser: argparse.ArgumentParser, *, required_amount: bool) -> None:
    parser.add_argument("--amount", required=required_amount, help="e.g. 25rb, 1,5jt, Rp45.000")
    parser.add_argument("--date", help="today, kemarin, 3/9, 2026-09-14 (default today)")
    parser.add_argument("--category")
    parser.add_argument("--account")
    parser.add_argument("--to-account", dest="to_account", help="transfer destination")
    parser.add_argument("--merchant")
    parser.add_argument("--description")
    parser.add_argument("--subcategory")
    parser.add_argument("--payment-method", dest="payment_method")
    parser.add_argument("--notes")


def build_parser() -> argparse.ArgumentParser:
    """All subcommands; each sets ``handler`` (needs doeedd) or ``offline`` (does not)."""
    parser = argparse.ArgumentParser(prog="doeedd.py", description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    parser.add_argument("-v", "--verbose", action="store_true", help="log requests to stderr")
    parser.add_argument("--today", help="override today's date (Asia/Jakarta by default)")
    sub = parser.add_subparsers(dest="command", required=True)

    def command(
        name: str, handler: Callable[..., dict[str, Any]], help_text: str
    ) -> argparse.ArgumentParser:
        child = sub.add_parser(name, help=help_text)
        child.set_defaults(handler=handler)
        return child

    parse = sub.add_parser("parse", help="show how an amount or date is understood")
    parse.add_argument("what", choices=("amount", "date"))
    parse.add_argument("text")
    parse.set_defaults(offline=cmd_parse)

    command("health", cmd_health, "check doeedd and the token")
    command("context", cmd_context, "categories, accounts and habits for interpreting a message")
    command("home", cmd_home, "payday, budget left this month, net worth")
    monthly = command("monthly", cmd_monthly, "plan versus actual per category")
    monthly.add_argument("--month", help="YYYY-MM (default this month)")
    monthly.add_argument("--category")
    summary = command("summary", cmd_summary, "the month's plan")
    summary.add_argument("--month")
    trend = command("trend", cmd_trend, "monthly spending trend (default last 6 months)")
    trend.add_argument("--from", dest="start")
    trend.add_argument("--to")
    command("networth", cmd_networth, "net worth")
    command("goals", cmd_goals, "goal progress")

    find = command("find", cmd_find, "search transactions")
    find.add_argument("--q")
    find.add_argument("--from", dest="start")
    find.add_argument("--to", dest="end")
    find.add_argument("--month")
    find.add_argument("--category")
    find.add_argument("--account")
    find.add_argument("--type", choices=("expense", "income", "transfer"))
    find.add_argument("--source", choices=("app", "agent", "import"))
    find.add_argument("--unreviewed", action="store_true")
    find.add_argument("--no-receipt", dest="no_receipt", action="store_true")
    find.add_argument("--limit", type=int, default=20)

    add = command("add", cmd_add, "record an expense, income or transfer")
    add.add_argument("--type", choices=("expense", "income", "transfer"), default="expense")
    _capture_fields(add, required_amount=True)
    add.add_argument("--key", help="message reference, e.g. telegram:<chat>:<message>:<n>")
    add.add_argument("--receipt", help="local receipt image or PDF to upload to Drive")
    add.add_argument("--receipt-id", dest="receipt_id", help="existing Drive file id")
    add.add_argument("--reviewed", action="store_true")
    add.add_argument("--allow-duplicate", dest="allow_duplicate", action="store_true")
    add.add_argument("--allow-future", dest="allow_future", action="store_true")

    edit = command("edit", cmd_edit, "correct a transaction (the last capture by default)")
    edit.add_argument("--id")
    _capture_fields(edit, required_amount=False)
    edit.add_argument("--reviewed", action="store_true")
    edit.add_argument("--unreviewed", action="store_true")
    edit.add_argument("--learn", action="store_true", help="remember the merchant's new defaults")

    undo = command("undo", cmd_undo, "remove the last capture (restorable)")
    undo.add_argument("--id")
    command("restore", cmd_restore, "bring back what undo removed")
    attach = command("attach", cmd_attach, "add a receipt to a transaction")
    attach.add_argument("--id")
    attach.add_argument("--receipt")
    attach.add_argument("--receipt-id", dest="receipt_id")
    command("flush", cmd_flush, "send writes queued while doeedd was unreachable")

    aliases = command("aliases", cmd_aliases, "show or change the alias memory")
    aliases.add_argument("action", choices=("show", "set", "unset"))
    aliases.add_argument("section", nargs="?", choices=("category", "account", "default_account"))
    aliases.add_argument("key", nargs="?")
    aliases.add_argument("value", nargs="?", help="doeedd name, or 'ask' for always ask")

    remember = command("remember", cmd_remember, "remember a merchant's usual details")
    remember.add_argument("--merchant", required=True)
    remember.add_argument("--category")
    remember.add_argument("--account")
    remember.add_argument("--subcategory")
    remember.add_argument("--payment-method", dest="payment_method")

    seed = command("seed", cmd_seed, "create missing default categories and accounts")
    seed.add_argument("--apply", action="store_true", help="write (default is a dry run)")

    migrate = command("migrate-sheets", cmd_migrate_sheets, "move the Sheets ledger into doeedd")
    migrate.add_argument("--apply", action="store_true", help="write (default is a dry run)")
    migrate.add_argument("--spreadsheet-id", dest="spreadsheet_id")
    return parser


def _error(kind: str, message: str, as_json: bool, code: int, **extra: Any) -> int:
    emit({"status": "error", "kind": kind, "reply": message, **extra}, as_json)
    return code


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run one command, print its result."""
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if (
        args.command == "aliases"
        and args.action in ("set", "unset")
        and not (args.section and args.key)
    ):
        return _error("usage", "aliases set/unset need a section and a key", args.json, 2)
    try:
        if getattr(args, "offline", None):
            result = args.offline(args)
        else:
            app = open_app(args)
            try:
                result = args.handler(app, args)
            finally:
                app.client.close()
    except AmbiguousError as exc:
        return _error("ambiguous", str(exc), args.json, 2)
    except (ParseError, ConfigError, ValueError) as exc:
        return _error("input", str(exc), args.json, 2)
    except ReceiptUploadError as exc:
        emit(
            Outcome(
                "receipt_failed",
                reply="Couldn't store the receipt in Drive, so nothing was logged. "
                "Log it without the receipt?",
            ).to_dict()
            | {"detail": str(exc)},
            args.json,
        )
        return 0
    except DoeeddError as exc:
        code = 3 if exc.kind in ("unauthorized", "forbidden") else 4
        return _error(exc.kind, str(exc), args.json, code, error=exc.to_dict())
    emit(result, args.json)
    return 0
