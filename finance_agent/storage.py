"""SQLite ledger adapter. All writes use transactions; data stays local."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .domain import DEFAULT_CATEGORIES

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
 id TEXT PRIMARY KEY, external_ref TEXT UNIQUE, occurred_on TEXT NOT NULL,
 type TEXT NOT NULL, amount TEXT NOT NULL, currency TEXT NOT NULL,
 merchant TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
 category TEXT NOT NULL DEFAULT '', account TEXT NOT NULL DEFAULT '',
 to_account TEXT NOT NULL DEFAULT '', payment_method TEXT NOT NULL DEFAULT '',
 notes TEXT NOT NULL DEFAULT '', receipt_id TEXT NOT NULL DEFAULT '',
 receipt_path TEXT NOT NULL DEFAULT '', duplicate_status TEXT NOT NULL DEFAULT '',
 reconciliation_status TEXT NOT NULL DEFAULT 'Pending', reconciled_at TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(occurred_on);
CREATE TABLE IF NOT EXISTS categories (name TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, path TEXT NOT NULL);
"""


class Ledger:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        self.connection = sqlite3.connect(path)
        path.chmod(0o600)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self.connection.executemany(
            "INSERT OR IGNORE INTO categories(name) VALUES (?)",
            [(name,) for name in DEFAULT_CATEGORIES],
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def rows(self, where: str = "", params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        query = "SELECT * FROM transactions"
        if where:
            query += " WHERE " + where
        query += " ORDER BY occurred_on DESC, created_at DESC"
        return [dict(row) for row in self.connection.execute(query, params)]

    def get(self, identifier: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM transactions WHERE id=?", (identifier,)
        ).fetchone()
        return dict(row) if row else None

    def by_ref(self, ref: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM transactions WHERE external_ref=?", (ref,)
        ).fetchone()
        return dict(row) if row else None

    def insert(self, row: dict[str, Any]) -> None:
        columns = tuple(row)
        names = ", ".join(columns)
        marks = ", ".join("?" for _ in columns)
        with self.connection:
            self.connection.execute(
                f"INSERT INTO transactions ({names}) VALUES ({marks})", tuple(row.values())
            )

    def update(self, identifier: str, changes: dict[str, Any]) -> None:
        assignments = ", ".join(f"{key}=?" for key in changes)
        with self.connection:
            self.connection.execute(
                f"UPDATE transactions SET {assignments} WHERE id=?", (*changes.values(), identifier)
            )

    def categories(self) -> list[str]:
        return [
            row[0] for row in self.connection.execute("SELECT name FROM categories ORDER BY name")
        ]

    def add_category(self, name: str) -> None:
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO categories(name) VALUES (?)", (name,))

    def save_receipt(self, identifier: str, path: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO receipts(id, path) VALUES (?, ?)", (identifier, path)
            )

    def receipt(self, identifier: str) -> str | None:
        row = self.connection.execute(
            "SELECT path FROM receipts WHERE id=?", (identifier,)
        ).fetchone()
        return row[0] if row else None
