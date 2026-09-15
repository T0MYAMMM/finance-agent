"""Assets and their valuations: "saldo BCA 12,5jt" becomes a dated valuation in doeedd."""

from __future__ import annotations

from datetime import date
from typing import Any

from .aliases import resolve
from .capture import Names
from .client import DoeeddClient
from .formatting import idr_short, short_date


def list_assets(client: DoeeddClient) -> dict[str, Any]:
    """Every active asset with its latest value."""
    assets = client.assets()
    if not assets:
        return {"status": "ok", "reply": "No assets tracked yet.", "items": []}
    lines = ["🏦 Assets:"]
    for asset in assets:
        value = asset.get("current_value")
        when = asset.get("valued_on")
        amount = idr_short(value) if value is not None else "no value yet"
        suffix = f" ({short_date(date.fromisoformat(when))})" if when else ""
        lines.append(f"• {asset['name']}: {amount}{suffix}")
    return {"status": "ok", "reply": "\n".join(lines), "items": assets}


def record_valuation(
    client: DoeeddClient, asset_name: str, amount: int, valued_on: date
) -> dict[str, Any]:
    """Store today's (or a given day's) value of an asset; same-day updates replace."""
    assets = client.assets()
    match = resolve(asset_name, [asset["name"] for asset in assets], {})
    asset = next((item for item in assets if item["name"] == match.name), None)
    if asset is None:
        return {
            "status": "needs_input",
            "missing": ["asset"],
            "options": {"asset": [item["name"] for item in assets]},
            "reply": f"No asset called {asset_name!r}. Which one, or should I add it?",
        }
    client.add_valuation(asset["id"], {"value": amount, "valued_on": valued_on.isoformat()})
    reply = f"🏦 {asset['name']}: {idr_short(amount)} on {short_date(valued_on)}"
    previous = asset.get("current_value")
    if (
        previous is not None
        and asset.get("valued_on")
        and asset["valued_on"] != valued_on.isoformat()
    ):
        reply += (
            f" (was {idr_short(previous)} on {short_date(date.fromisoformat(asset['valued_on']))})"
        )
    return {"status": "recorded", "asset": asset["name"], "value": amount, "reply": reply + "."}


def create_asset(
    client: DoeeddClient,
    names: Names,
    name: str,
    amount: int,
    valued_on: date,
    *,
    account: str | None = None,
    liquid: bool = True,
) -> dict[str, Any]:
    """Start tracking an asset with its first valuation (ask the owner first)."""
    body: dict[str, Any] = {
        "name": name,
        "is_liquid": liquid,
        "value": amount,
        "valued_on": valued_on.isoformat(),
    }
    if account:
        match = resolve(account, [item["name"] for item in names.accounts], {})
        linked = next((item for item in names.accounts if item["name"] == match.name), None)
        if linked is None:
            return {
                "status": "needs_input",
                "missing": ["account"],
                "options": {"account": [item["name"] for item in names.accounts]},
                "reply": f"No account called {account!r}.",
            }
        body["account_id"] = linked["id"]
    created = client.create_asset(body)
    kind = "liquid" if liquid else "non-liquid"
    reply = (
        f"🏦 Tracking {created['name']} ({kind}): {idr_short(amount)} on {short_date(valued_on)}."
    )
    return {"status": "created", "asset": created, "reply": reply}
