"""Alias memory: how the user's words map to doeedd category and account names.

Defaults ship in code; the user's own choices and corrections are stored as overrides in
``aliases.json``. Values are doeedd *names*, resolved to ids at runtime, so a rename in doeedd
never leaves a stale id behind. A value of ``None`` means "known to be ambiguous: always ask".
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .store import load_json, save_json

SECTIONS = ("category", "account", "merchant_defaults")
MERCHANT_FIELDS = ("category", "account", "subcategory", "payment_method")

DEFAULT_ALIASES: dict[str, Any] = {
    "category": {
        "food & dining": "Food",
        "makan": "Food",
        "makanan": "Food",
        "makan siang": "Food",
        "kopi": "Food",
        "coffee": "Food",
        "restaurant": "Food",
        "restoran": "Food",
        "groceries": "Food",
        "gofood": "Food",
        "grabfood": "Food",
        "transport": "Transportation",
        "bensin": "Transportation",
        "fuel": "Transportation",
        "parkir": "Transportation",
        "parking": "Transportation",
        "tol": "Transportation",
        "goride": "Transportation",
        "gocar": "Transportation",
        "grabbike": "Transportation",
        "grabcar": "Transportation",
        "grab": None,
        "gojek": None,
        "listrik": "Utilities",
        "token listrik": "Utilities",
        "internet": "Utilities",
        "pulsa": "Utilities",
        "health": "Healthcare",
        "obat": "Healthcare",
        "dokter": "Healthcare",
        "belanja": None,
        "hiburan": "Entertainment",
        "hadiah": "Gifts",
        "kado": "Gifts",
        "salary": "Monthly Salary",
        "gaji": "Monthly Salary",
        "gajian": "Monthly Salary",
        "freelance": "Freelance Fee",
        "indomaret": None,
        "alfamart": None,
    },
    "account": {
        "bca debit": "BCA",
        "m-banking bca": "BCA",
        "bca credit card": "Credit Card",
        "kartu kredit": "Credit Card",
        "livin": "Mandiri",
        "livin mandiri": "Mandiri",
        "tunai": "Cash",
        "qris": None,
        "transfer": None,
        "bank transfer": None,
        "paylater": None,
        "gopaylater": None,
    },
    "merchant_defaults": {
        "tomoro coffee": {
            "category": "Food",
            "account": "Mandiri",
            "subcategory": "Coffee",
            "payment_method": "QRIS",
        },
    },
    "default_account": "BCA",
}


def normalize(text: str) -> str:
    """Lower-case and collapse whitespace so lookups ignore typing noise."""
    return " ".join(text.lower().split())


@dataclass(frozen=True)
class Match:
    """Result of resolving a word: a doeedd name, or ambiguous, or unknown (both empty)."""

    name: str | None
    ambiguous: bool = False


def resolve(text: str, known_names: list[str], table: dict[str, str | None]) -> Match:
    """Resolve ``text`` against doeedd names first, then the alias table."""
    key = normalize(text)
    by_key = {normalize(name): name for name in known_names}
    if key in by_key:
        return Match(by_key[key])
    if key in table:
        target = table[key]
        if target is None:
            return Match(None, ambiguous=True)
        return Match(by_key.get(normalize(target)))
    return Match(None)


class AliasStore:
    """Defaults merged with the user's overrides in ``aliases.json``."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def overrides(self) -> dict[str, Any]:
        """Only what the user taught or corrected."""
        data = load_json(self.path, {})
        return data if isinstance(data, dict) else {}

    def merged(self) -> dict[str, Any]:
        """Defaults with the overrides applied on top."""
        data = copy.deepcopy(DEFAULT_ALIASES)
        overrides = self.overrides()
        for section in SECTIONS:
            data[section].update(overrides.get(section, {}))
        if overrides.get("default_account"):
            data["default_account"] = overrides["default_account"]
        return data

    def set(self, section: str, key: str, value: str | None) -> None:
        """Store an alias (``None`` marks the word as ambiguous)."""
        if section == "default_account":
            overrides = self.overrides()
            overrides["default_account"] = value
            save_json(self.path, overrides)
            return
        if section not in ("category", "account"):
            raise ValueError("section must be category, account or default_account")
        overrides = self.overrides()
        overrides.setdefault(section, {})[normalize(key)] = value
        save_json(self.path, overrides)

    def unset(self, section: str, key: str) -> bool:
        """Drop an override; returns whether one existed."""
        overrides = self.overrides()
        removed = overrides.get(section, {}).pop(normalize(key), None) is not None
        if removed:
            save_json(self.path, overrides)
        return removed

    def remember_merchant(self, merchant: str, **fields: str | None) -> None:
        """Remember a merchant's usual category, account, subcategory or payment method."""
        values = {name: value for name, value in fields.items() if value}
        unknown = set(values) - set(MERCHANT_FIELDS)
        if unknown:
            raise ValueError(f"unknown merchant fields: {', '.join(sorted(unknown))}")
        if not values:
            return
        overrides = self.overrides()
        defaults = overrides.setdefault("merchant_defaults", {})
        merged = {**self.merged()["merchant_defaults"].get(normalize(merchant), {}), **values}
        defaults[normalize(merchant)] = merged
        save_json(self.path, overrides)
