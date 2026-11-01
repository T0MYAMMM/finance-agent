"""Initial doeedd categories and accounts from the user's own spreadsheets.

Categories come from the budgeting template's Setup lists (placeholders and sample rows
dropped, ``Entertaiment`` typo fixed). Accounts come from the finance ledger's Accounts tab.
Seeding only creates what is missing; it never renames, archives or deletes.
"""

from __future__ import annotations

from typing import Any

from .aliases import normalize
from .client import DoeeddClient

SEED_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("Monthly Salary", "income"),
    ("Freelance Fee", "income"),
    ("General Savings", "saving"),
    ("Emergency Funds", "saving"),
    ("Deposits", "saving"),
    ("Family", "need"),
    ("Utilities", "need"),
    ("Food", "need"),
    ("Supplies", "need"),
    ("Transportation", "need"),
    ("Healthcare", "need"),
    ("Debt", "need"),
    ("Shopping", "want"),
    ("Entertainment", "want"),
    ("Gifts", "want"),
    ("Travel", "want"),
)

# (name, doeedd account type, liquid). Credit cards are liabilities: type other, not liquid (D-06).
SEED_ACCOUNTS: tuple[tuple[str, str, bool], ...] = (
    ("BCA", "bank", True),
    ("Mandiri", "bank", True),
    ("Cash", "cash", True),
    ("GoPay", "ewallet", True),
    ("OVO", "ewallet", True),
    ("DANA", "ewallet", True),
    ("Credit Card", "other", False),
)


def plan_seed(client: DoeeddClient) -> dict[str, Any]:
    """What seeding would create, given what already exists (archived items count as existing)."""
    categories = {
        (normalize(c["name"]), c["kind"]) for c in client.categories(include_archived=True)
    }
    accounts = {normalize(a["name"]) for a in client.accounts(include_archived=True)}
    return {
        "categories": [
            {"name": name, "kind": kind}
            for name, kind in SEED_CATEGORIES
            if (normalize(name), kind) not in categories
        ],
        "accounts": [
            {"name": name, "type": kind, "is_liquid": liquid}
            for name, kind, liquid in SEED_ACCOUNTS
            if normalize(name) not in accounts
        ],
    }


def apply_seed(client: DoeeddClient, plan: dict[str, Any]) -> dict[str, Any]:
    """Create the planned categories and accounts."""
    created_categories = [client.create_category(body)["name"] for body in plan["categories"]]
    created_accounts = [client.create_account(body)["name"] for body in plan["accounts"]]
    return {"categories": created_categories, "accounts": created_accounts}
