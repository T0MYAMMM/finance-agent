"""HTTP client for the doeedd API, authenticated with a personal API token."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx

log = logging.getLogger(__name__)

RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
PAGE_SIZE = 200
MAX_PAGES = 50
MAX_RETRY_AFTER_SECONDS = 10.0
_KIND_BY_STATUS = {
    400: "validation",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation",
    429: "rate_limited",
}


@dataclass(eq=False)
class DoeeddError(Exception):
    """A failed call, carrying the API's RFC 9457 problem details when there were any."""

    status: int | None
    title: str
    detail: str = ""
    errors: list[dict[str, str]] = field(default_factory=list)
    request_id: str | None = None

    def __str__(self) -> str:
        parts = [f"{self.status or 'no response'} {self.title}"]
        if self.detail:
            parts.append(self.detail)
        parts.extend(f"{error.get('path')}: {error.get('message')}" for error in self.errors)
        return "; ".join(parts)

    @property
    def kind(self) -> str:
        """A stable label the CLI and the skill branch on."""
        if self.status is None:
            return "unreachable"
        if self.status >= 500:
            return "server"
        return _KIND_BY_STATUS.get(self.status, "error")

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly form for CLI output."""
        return {
            "kind": self.kind,
            "status": self.status,
            "title": self.title,
            "detail": self.detail,
            "errors": self.errors,
            "request_id": self.request_id,
        }


class DoeeddClient:
    """Thin wrapper over the doeedd REST API (``/api/v1``).

    Reads, PATCH, DELETE and writes carrying an ``Idempotency-Key`` are retried on network
    errors and 429/5xx; a POST without a key is never retried, so it cannot double-book.
    Logs carry method, path, status, duration and request id only, never amounts or text.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 20.0,
        retries: int = 2,
        backoff: float = 0.5,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._retries = retries
        self._backoff = backoff
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "doeedd-agent/0.2",
            },
        )

    def close(self) -> None:
        """Close the underlying connection pool."""
        self._http.close()

    def __enter__(self) -> DoeeddClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- transport ------------------------------------------------------------------------

    def send(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        idempotency_key: str | None = None,
    ) -> httpx.Response:
        """Send one request with retries; raise :class:`DoeeddError` unless it succeeded."""
        request_id = f"hermes-{uuid.uuid4()}"
        headers = {"X-Request-ID": request_id}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        retryable = method in {"GET", "PATCH", "DELETE"} or idempotency_key is not None

        attempt = 0
        while True:
            started = time.monotonic()
            try:
                response = self._http.request(
                    method, path.lstrip("/"), params=_query(params), json=json, headers=headers
                )
            except httpx.TransportError as exc:
                log.warning("doeedd %s %s failed: %s", method, path, type(exc).__name__)
                if retryable and attempt < self._retries:
                    attempt += 1
                    self._sleep(self._backoff * 2 ** (attempt - 1))
                    continue
                raise DoeeddError(
                    None, "doeedd is unreachable", type(exc).__name__, request_id=request_id
                ) from exc

            elapsed_ms = int((time.monotonic() - started) * 1000)
            log.info(
                "doeedd %s %s -> %s (%d ms, request_id=%s)",
                method,
                path,
                response.status_code,
                elapsed_ms,
                request_id,
            )
            if response.status_code in RETRYABLE_STATUSES and retryable and attempt < self._retries:
                attempt += 1
                self._sleep(_retry_after(response) or self._backoff * 2 ** (attempt - 1))
                continue
            if response.is_success:
                return response
            raise _problem(response, request_id)

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Send a request and return the decoded JSON body (``None`` for empty responses)."""
        response = self.send(method, path, **kwargs)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def list_all(
        self, path: str, params: Mapping[str, Any] | None = None, *, max_items: int | None = None
    ) -> list[dict[str, Any]]:
        """Follow ``next_cursor`` through a list endpoint."""
        query: dict[str, Any] = {"limit": PAGE_SIZE, **(params or {})}
        items: list[dict[str, Any]] = []
        for _ in range(MAX_PAGES):
            page = self.request("GET", path, params=query)
            items.extend(page["items"])
            if max_items is not None and len(items) >= max_items:
                return items[:max_items]
            cursor = page.get("next_cursor")
            if not cursor:
                return items
            query["cursor"] = cursor
        log.warning("doeedd %s: stopped after %d pages", path, MAX_PAGES)
        return items

    # -- endpoints ------------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """``GET /health``."""
        return self.request("GET", "health")

    def me(self) -> dict[str, Any]:
        """The token's user with settings."""
        return self.request("GET", "auth/me")

    def accounts(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        """Accounts in display order."""
        return self.list_all("accounts", {"include_archived": include_archived})

    def categories(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        """Categories of every kind in display order."""
        return self.list_all("categories", {"include_archived": include_archived})

    def create_account(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """``POST /accounts``."""
        return self.request("POST", "accounts", json=dict(body))

    def create_category(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """``POST /categories``."""
        return self.request("POST", "categories", json=dict(body))

    def transactions(
        self, filters: Mapping[str, Any] | None = None, *, max_items: int | None = None
    ) -> list[dict[str, Any]]:
        """Transactions matching the list filters (``from``, ``to``, ``q``, ``source``...)."""
        return self.list_all("transactions", filters, max_items=max_items)

    def get_transaction(self, transaction_id: str) -> dict[str, Any]:
        """One transaction, deleted or not."""
        return self.request("GET", f"transactions/{transaction_id}")

    def create_transaction(
        self, body: Mapping[str, Any], idempotency_key: str | None = None
    ) -> tuple[dict[str, Any], bool]:
        """Create a transaction; the flag is true when doeedd replayed an earlier request."""
        response = self.send(
            "POST", "transactions", json=dict(body), idempotency_key=idempotency_key
        )
        return response.json(), response.headers.get("idempotency-replayed") == "true"

    def update_transaction(self, transaction_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """``PATCH /transactions/{id}``."""
        return self.request("PATCH", f"transactions/{transaction_id}", json=dict(body))

    def delete_transaction(self, transaction_id: str) -> None:
        """Soft delete (restorable for 30 days)."""
        self.request("DELETE", f"transactions/{transaction_id}")

    def restore_transaction(self, transaction_id: str) -> dict[str, Any]:
        """Undo a soft delete."""
        return self.request("POST", f"transactions/{transaction_id}/restore")

    def add_attachment(self, transaction_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """Link a file reference (a Drive receipt) to a transaction."""
        return self.request("POST", f"transactions/{transaction_id}/attachments", json=dict(body))

    def bulk_review(self, ids: list[str], is_reviewed: bool) -> dict[str, Any]:
        """Set the reviewed flag on up to 500 transactions at once."""
        body = {"ids": ids, "is_reviewed": is_reviewed}
        return self.request("POST", "transactions/bulk-review", json=body)

    def attachments(self, transaction_id: str) -> list[dict[str, Any]]:
        """File references of a transaction."""
        return self.request("GET", f"transactions/{transaction_id}/attachments")["items"]

    def assets(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        """Assets with their latest value."""
        return self.list_all("assets", {"include_archived": include_archived})

    def create_asset(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Create an asset together with its first valuation."""
        return self.request("POST", "assets", json=dict(body))

    def add_valuation(self, asset_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """Record a dated value; a second valuation on the same day replaces the first."""
        return self.request("POST", f"assets/{asset_id}/valuations", json=dict(body))

    def copy_budget(self, year: int, month: int, *, overwrite: bool = False) -> Any:
        """Copy a month's plan to the next month (non-zero target lines kept unless overwrite)."""
        body = {"to": "next", "overwrite": overwrite}
        return self.request("POST", f"budgets/{year}/{month}/copy", json=body)

    def budget_period(self, year: int, month: int) -> dict[str, Any] | None:
        """One month's plan with lines and totals, or ``None`` when nothing was ever planned."""
        try:
            return self.request("GET", f"budgets/{year}/{month}")
        except DoeeddError as error:
            if error.kind == "not_found":
                return None
            raise

    def put_budget_lines(
        self, year: int, month: int, lines: list[Mapping[str, Any]]
    ) -> dict[str, Any]:
        """Upsert the given lines only (creates the month's period when missing)."""
        body = [dict(line) for line in lines]
        return self.request("PUT", f"budgets/{year}/{month}/lines", json=body)

    def export_csv(self, entity: str) -> str:
        """One entity (transactions, budget_lines, assets, accounts, categories) as CSV text."""
        return self.send("GET", f"data/export/{entity}.csv").text

    def account_balances(self, at: date, *, include_archived: bool = False) -> dict[str, Any]:
        """Every account's balance on a date (opening balance plus non-deleted transactions)."""
        params = {"at": at, "include_archived": include_archived}
        return self.request("GET", "accounts/balances", params=params)

    def report_home(self, today: date) -> dict[str, Any]:
        """Payday, this month's remaining budget, recent entries and net worth."""
        return self.request("GET", "reports/home", params={"today": today})

    def report_monthly(self, year: int, month: int) -> dict[str, Any]:
        """Planned versus actual per category for one month."""
        return self.request("GET", f"reports/monthly/{year}/{month}")

    def report_summary(self, year: int, month: int) -> dict[str, Any]:
        """The month's plan by account, by kind and per line."""
        return self.request("GET", f"reports/summary/{year}/{month}")

    def report_trend(self, start: str, end: str) -> list[dict[str, Any]]:
        """Monthly totals from ``start`` to ``end`` (both ``YYYY-MM``)."""
        return self.request("GET", "reports/trend", params={"from": start, "to": end})

    def report_cumulative_savings(self, end: str, today: date) -> list[dict[str, Any]]:
        """Planned and actual savings, cumulated up to ``end`` (``YYYY-MM``)."""
        return self.request("GET", "reports/cumulative-savings", params={"to": end, "today": today})

    def net_worth(self, at: date) -> dict[str, Any]:
        """Net worth at a date with liquid split and history."""
        return self.request("GET", "assets/net-worth", params={"at": at})

    def goals(self, at: date) -> list[dict[str, Any]]:
        """Goals with progress measured at ``at``."""
        return self.list_all("goals", {"at": at})


def _query(params: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Drop empty values and render booleans and dates the way the API expects."""
    if not params:
        return None
    rendered: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            rendered[key] = "true" if value else "false"
        elif isinstance(value, date):
            rendered[key] = value.isoformat()
        else:
            rendered[key] = value
    return rendered


def _retry_after(response: httpx.Response) -> float | None:
    """Seconds from a ``Retry-After`` header, capped."""
    value = response.headers.get("retry-after")
    try:
        return min(float(value), MAX_RETRY_AFTER_SECONDS) if value else None
    except ValueError:
        return None


def _problem(response: httpx.Response, request_id: str) -> DoeeddError:
    """Turn an error response into a :class:`DoeeddError`."""
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    return DoeeddError(
        status=response.status_code,
        title=str(body.get("title") or response.reason_phrase or "Error"),
        detail=str(body.get("detail") or ""),
        errors=list(body.get("errors") or []),
        request_id=str(body.get("request_id") or request_id),
    )
