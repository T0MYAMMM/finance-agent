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
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import queries
from .aliases import AliasStore, resolve
from .capture import CaptureRequest, EditRequest, Outcome, Recorder
from .client import DoeeddClient, DoeeddError
from .config import ConfigError, Settings, load_settings
from .formatting import idr, short_date
from .holdings import account_balances, create_asset, list_assets, record_valuation
from .migration import (
    apply_migration,
    expense_total,
    ledger_spreadsheet_id,
    plan_migration,
    read_ledger,
)
from .notify import SILENT, commit_notification, plan_notification
from .notify_state import NotifyState
from .outbox import Outbox
from .parsing import AmbiguousError, ParseError, parse_amount, parse_date, today_in
from .planning import move_budget, set_budget, show_plan
from .receipts import ReceiptUploadError, google_service, upload_receipt
from .reconcile import load_statement, reconcile, statement_window
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
    notify: NotifyState


def open_app(args: argparse.Namespace) -> App:
    """Build the client and the local stores from configuration."""
    settings = load_settings()
    client = DoeeddClient(settings.base_url, settings.token)
    state_dir = settings.state_dir
    aliases = AliasStore(state_dir / "aliases.json")
    notify = NotifyState(state_dir / "notify_state.json")
    recorder = Recorder(
        client,
        aliases,
        StateStore(state_dir / "state.json"),
        Outbox(state_dir / "outbox.json"),
        uploader=upload_receipt,
        notify=notify,
    )
    today = (
        parse_date(args.today, today_in(settings.timezone))
        if getattr(args, "today", None)
        else today_in(settings.timezone)
    )
    return App(settings, client, aliases, recorder, today, notify)


def emit(result: dict[str, Any], as_json: bool) -> None:
    """Print a result for the skill (JSON) or a person (reply text)."""
    if as_json:
        print(json.dumps(result, ensure_ascii=False, default=str))
    elif "reply" in result:
        if result["reply"]:
            print(result["reply"])
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


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
    if args.ref:
        filters["external_ref"] = args.ref
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


def cmd_review(app: App, args: argparse.Namespace) -> dict[str, Any]:
    return queries.review(app.client, app.recorder.names(), args.limit)


def cmd_approve(app: App, args: argparse.Namespace) -> dict[str, Any]:
    if args.all:
        ids = [row["id"] for row in app.client.transactions({"reviewed": False}, max_items=500)]
    else:
        ids = args.id or []
    return app.recorder.approve(ids).to_dict()


def cmd_receipts(app: App, args: argparse.Namespace) -> dict[str, Any]:
    ids = app.recorder.target_ids(args.id)
    if len(ids) != 1:
        return {"status": "needs_input", "missing": ["id"], "reply": "Which entry? (pass --id)"}
    transaction = app.client.get_transaction(ids[0])
    return queries.receipts(app.client, app.recorder.names(), transaction)


def cmd_create_category(app: App, args: argparse.Namespace) -> dict[str, Any]:
    created = app.client.create_category({"name": args.name, "kind": args.kind})
    reply = f"Added category {created['name']} ({created['kind']})."
    return {"status": "created", "category": created, "reply": reply}


def cmd_create_account(app: App, args: argparse.Namespace) -> dict[str, Any]:
    body = {"name": args.name, "type": args.type, "is_liquid": not args.not_liquid}
    created = app.client.create_account(body)
    reply = f"Added account {created['name']} ({created['type']})."
    return {"status": "created", "account": created, "reply": reply}


def cmd_assets(app: App, args: argparse.Namespace) -> dict[str, Any]:
    return list_assets(app.client)


def cmd_valuation(app: App, args: argparse.Namespace) -> dict[str, Any]:
    valued_on = parse_date(args.date, app.today) if args.date else app.today
    return record_valuation(app.client, args.asset, parse_amount(args.amount), valued_on)


def cmd_create_asset(app: App, args: argparse.Namespace) -> dict[str, Any]:
    valued_on = parse_date(args.date, app.today) if args.date else app.today
    return create_asset(
        app.client,
        app.recorder.names(),
        args.name,
        parse_amount(args.amount),
        valued_on,
        account=args.account,
        liquid=not args.not_liquid,
    )


FAILURE_ALERT_AFTER = 3


def cmd_notify(app: App, args: argparse.Namespace) -> dict[str, Any]:
    now = datetime.now(app.settings.timezone)
    if args.at:
        now = datetime.fromisoformat(args.at).replace(tzinfo=app.settings.timezone)
    silent = "" if args.empty_when_silent else SILENT
    try:
        notification = plan_notification(app.client, app.notify, now)
    except DoeeddError as error:
        if not args.empty_when_silent:
            raise
        failures = app.notify.record_failure(now.date())
        log.warning("notify: doeedd check failed (%s), %d in a row", error.kind, failures)
        if failures == FAILURE_ALERT_AFTER:
            reply = (
                "⚠️ I couldn't reach doeedd for the last 3 scheduled checks. "
                "New entries still queue safely and will sync when it is back."
            )
            return {"status": "send", "kind": "doeedd_unreachable", "reply": reply}
        return {"status": "silent", "reply": silent}
    app.notify.reset_failures(now.date())
    if notification is None:
        return {"status": "silent", "reply": silent}
    if args.dry_run:
        return {"status": "would_send", "kind": notification.kind, "reply": notification.message}
    commit_notification(app.notify, notification, now.date())
    return {"status": "send", "kind": notification.kind, "reply": notification.message}


def cmd_snooze(app: App, args: argparse.Namespace) -> dict[str, Any]:
    until = app.today + timedelta(days=args.days - 1)
    app.notify.snooze(until, app.today)
    reply = f'🔕 No proactive messages until {short_date(until)}. Say "unsnooze" to resume.'
    return {"status": "snoozed", "until": until.isoformat(), "reply": reply}


def cmd_unsnooze(app: App, args: argparse.Namespace) -> dict[str, Any]:
    app.notify.snooze(None, app.today)
    return {"status": "resumed", "reply": "🔔 Proactive messages are back on."}


def cmd_copy_plan(app: App, args: argparse.Namespace) -> dict[str, Any]:
    year, month = _month(args.month, app.today)
    try:
        result = app.client.copy_budget(year, month, overwrite=args.overwrite)
    except DoeeddError as error:
        if error.kind != "not_found":
            raise
        return {"status": "nothing", "reply": f"No budget plan for {year}-{month:02d} to copy."}
    copied = sum(target["lines_copied"] for target in result["targets"])
    kept = sum(target["lines_skipped"] for target in result["targets"])
    reply = f"📋 Copied {copied} budget line(s) from {year}-{month:02d} to the next month."
    if kept:
        reply += f" Kept {kept} line(s) that were already planned there."
    return {"status": "copied", "reply": reply, "result": result}


EXPORT_DIR = Path.home() / ".hermes" / "cache" / "documents"


def _line_change(text: str) -> tuple[str, int]:
    """``Food=1,5jt`` -> ``("Food", 1500000)``."""
    name, separator, amount = text.rpartition("=")
    if not separator or not name.strip():
        raise ParseError(f"expected CATEGORY=AMOUNT, got {text!r}")
    return name.strip(), parse_amount(amount)


def cmd_plan(app: App, args: argparse.Namespace) -> dict[str, Any]:
    year, month = _month(args.month, app.today)
    return show_plan(app.client, year, month)


def cmd_plan_set(app: App, args: argparse.Namespace) -> dict[str, Any]:
    year, month = _month(args.month, app.today)
    changes = [_line_change(text) for text in args.line]
    aliases = app.aliases.merged()["category"]
    return set_budget(app.client, app.recorder.names(), aliases, year, month, changes)


def cmd_plan_move(app: App, args: argparse.Namespace) -> dict[str, Any]:
    year, month = _month(args.month, app.today)
    aliases = app.aliases.merged()["category"]
    return move_budget(
        app.client,
        app.recorder.names(),
        aliases,
        year,
        month,
        args.source,
        args.target,
        parse_amount(args.amount),
    )


def cmd_export(app: App, args: argparse.Namespace) -> dict[str, Any]:
    text = app.client.export_csv(args.entity)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORT_DIR / f"doeedd-{args.entity}-{app.today.isoformat()}.csv"
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    rows = max(len(text.splitlines()) - 1, 0)
    reply = f"📄 {args.entity} export ({rows} rows)\nMEDIA:{path}"
    return {"status": "exported", "path": str(path), "rows": rows, "reply": reply}


def cmd_reconcile(app: App, args: argparse.Namespace) -> dict[str, Any]:
    names = app.recorder.names()
    match = resolve(args.account, [item["name"] for item in names.accounts], {})
    account = next((item for item in names.accounts if item["name"] == match.name), None)
    if account is None:
        options = {"account": [item["name"] for item in names.accounts]}
        reply = f"No account called {args.account!r}."
        return {"status": "needs_input", "missing": ["account"], "options": options, "reply": reply}
    lines = load_statement(Path(args.file), app.today)
    start, end = statement_window(lines)
    filters = {"account_id": account["id"], "from": start, "to": end}
    transactions = app.client.transactions(filters, max_items=2000)
    result = reconcile(lines, transactions, account["id"])

    report = [
        f"🧾 {account['name']} statement {start.isoformat()}–{end.isoformat()}: {len(lines)} "
        f"line(s), {len(result.matched)} match doeedd."
    ]
    if result.missing:
        report.append(f"Missing in doeedd ({len(result.missing)}):")
        report.extend(
            f"{line.number}. {line.occurred_on.isoformat()} {idr(line.amount)} "
            f"{line.direction} {line.description}".rstrip()
            for line in result.missing
        )
    if result.extra:
        report.append(f"In doeedd but not on the statement ({len(result.extra)}):")
        report.extend(f"• {queries.row_line(names.compact(item))}" for item in result.extra)
    if not result.missing and not result.extra:
        report.append("✅ Everything matches.")
    if args.closing_balance:
        closing = parse_amount(args.closing_balance)
        last_day = max(line.occurred_on for line in lines)
        balances = app.client.account_balances(last_day, include_archived=True)["items"]
        ours = next(
            (item["balance"] for item in balances if item["account"]["id"] == account["id"]), None
        )
        if ours is not None:
            gap = closing - ours
            verdict = "matches" if gap == 0 else f"differs by {idr(abs(gap))}"
            report.append(
                f"Closing balance {last_day.isoformat()}: statement {idr(closing)}, "
                f"doeedd {idr(ours)} ({verdict})."
            )
    return {
        "status": "ok",
        "reply": "\n".join(report),
        "account": account["name"],
        "matched": len(result.matched),
        "missing": [
            {
                "number": line.number,
                "date": line.occurred_on.isoformat(),
                "amount": line.amount,
                "direction": line.direction,
                "description": line.description,
            }
            for line in result.missing
        ],
        "extra": [names.compact(item) for item in result.extra],
    }


def cmd_balances(app: App, args: argparse.Namespace) -> dict[str, Any]:
    at = parse_date(args.at, app.today) if args.at else app.today
    return account_balances(app.client, at)


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
        f"Migrated {len(result['created'])} row(s) "
        f"({len(result['already_present'])} already there), "
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
    find.add_argument("--ref", help="external reference, e.g. telegram:<chat>:<message>:1")

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

    review = command("review", cmd_review, "entries nobody has reviewed yet")
    review.add_argument("--limit", type=int, default=30)
    approve = command("approve", cmd_approve, "mark entries as reviewed")
    approve.add_argument("--all", action="store_true", help="every unreviewed entry")
    approve.add_argument("--id", action="append", help="repeat for several entries")
    receipts_command = command("receipts", cmd_receipts, "receipts linked to an entry")
    receipts_command.add_argument("--id", help="default: the last capture")
    new_category = command("create-category", cmd_create_category, "add a category (ask first)")
    new_category.add_argument("--name", required=True)
    new_category.add_argument("--kind", required=True, choices=("income", "need", "want", "saving"))
    new_account = command("create-account", cmd_create_account, "add an account (ask first)")
    new_account.add_argument("--name", required=True)
    new_account.add_argument(
        "--type", required=True, choices=("bank", "cash", "ewallet", "investment", "other")
    )
    new_account.add_argument("--not-liquid", dest="not_liquid", action="store_true")

    command("assets", cmd_assets, "assets with their latest values")
    valuation = command(
        "valuation", cmd_valuation, "record an asset's value, e.g. saldo BCA 12,5jt"
    )
    valuation.add_argument("--asset", required=True)
    valuation.add_argument("--amount", required=True)
    valuation.add_argument("--date")
    new_asset = command("create-asset", cmd_create_asset, "start tracking an asset (ask first)")
    new_asset.add_argument("--name", required=True)
    new_asset.add_argument("--amount", required=True, help="current value")
    new_asset.add_argument("--account")
    new_asset.add_argument("--not-liquid", dest="not_liquid", action="store_true")
    new_asset.add_argument("--date")

    notify = command("notify", cmd_notify, "the one proactive message due now, or [SILENT]")
    notify.add_argument("--dry-run", dest="dry_run", action="store_true")
    notify.add_argument(
        "--empty-when-silent",
        dest="empty_when_silent",
        action="store_true",
        help="print nothing instead of [SILENT] (Hermes no-agent cron delivers stdout verbatim)",
    )
    notify.add_argument("--at", help="pretend it is this local time, e.g. 2026-09-20T19:00")
    snooze = command("snooze", cmd_snooze, "pause proactive messages")
    snooze.add_argument("--days", type=int, default=7)
    command("unsnooze", cmd_unsnooze, "resume proactive messages")
    copy_plan = command("copy-plan", cmd_copy_plan, "copy a month's budget plan to the next month")
    copy_plan.add_argument("--month", help="YYYY-MM to copy from (default this month)")
    copy_plan.add_argument("--overwrite", action="store_true")

    plan = command("plan", cmd_plan, "a month's budget plan")
    plan.add_argument("--month", help="YYYY-MM (default this month)")
    plan_set = command("plan-set", cmd_plan_set, "set budget lines (ask the owner first)")
    plan_set.add_argument(
        "--line", action="append", required=True, help='CATEGORY=AMOUNT, e.g. "Food=1,5jt"; repeat'
    )
    plan_set.add_argument("--month")
    plan_move = command("plan-move", cmd_plan_move, "move planned money (ask the owner first)")
    plan_move.add_argument("--from", dest="source", required=True)
    plan_move.add_argument("--to", dest="target", required=True)
    plan_move.add_argument("--amount", required=True)
    plan_move.add_argument("--month")
    export = command("export", cmd_export, "export data as a CSV file for Telegram")
    export.add_argument(
        "--entity",
        default="transactions",
        choices=("transactions", "budget_lines", "assets", "accounts", "categories"),
    )

    reconcile_command = command(
        "reconcile", cmd_reconcile, "compare a statement (JSON lines) with doeedd for an account"
    )
    reconcile_command.add_argument("--account", required=True)
    reconcile_command.add_argument("--closing-balance", dest="closing_balance")
    reconcile_command.add_argument(
        "--file", required=True, help='JSON list of {"date","amount","direction","description"}'
    )

    balances = command("balances", cmd_balances, "account balances from logged entries")
    balances.add_argument("--at", help="date (default today)")

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
