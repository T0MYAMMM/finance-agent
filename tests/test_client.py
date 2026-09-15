from collections.abc import Callable
from datetime import date

import httpx
import pytest

from doeedd_agent.client import DoeeddClient, DoeeddError

BASE = "https://doeedd.test/api/v1"


def make_client(
    handler: Callable[[httpx.Request], httpx.Response], **kwargs: object
) -> DoeeddClient:
    return DoeeddClient(
        BASE, "doe_token", transport=httpx.MockTransport(handler), sleep=lambda _: None, **kwargs
    )


def test_sends_token_request_id_and_resolves_paths() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["request_id"] = request.headers["x-request-id"]
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"status": "ok", "db": "ok"})

    assert make_client(handler).health() == {"status": "ok", "db": "ok"}
    assert seen["auth"] == "Bearer doe_token"
    assert seen["request_id"].startswith("hermes-")
    assert seen["url"] == f"{BASE}/health"


def test_renders_dates_booleans_and_drops_empty_filters() -> None:
    params: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        params.update(dict(request.url.params))
        return httpx.Response(200, json={"items": [], "next_cursor": None})

    make_client(handler).transactions({"from": date(2026, 9, 14), "reviewed": False, "q": None})
    assert params == {"limit": "200", "from": "2026-09-14", "reviewed": "false"}


def test_follows_cursors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "cursor" in request.url.params:
            return httpx.Response(200, json={"items": [{"id": "b"}], "next_cursor": None})
        return httpx.Response(200, json={"items": [{"id": "a"}], "next_cursor": "c1"})

    assert [item["id"] for item in make_client(handler).accounts()] == ["a", "b"]


def test_problem_details_become_typed_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "title": "Unprocessable transaction",
                "detail": "see errors",
                "errors": [{"path": "category_id", "message": "required"}],
                "request_id": "req-1",
            },
        )

    with pytest.raises(DoeeddError) as caught:
        make_client(handler).create_transaction({"amount": 1})
    error = caught.value
    assert error.kind == "validation"
    assert error.errors == [{"path": "category_id", "message": "required"}]
    assert error.request_id == "req-1"
    assert "category_id: required" in str(error)


def test_retries_reads_on_server_errors() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"status": "ok", "db": "ok"})

    make_client(handler, retries=2).health()
    assert len(calls) == 3


def test_never_retries_a_post_without_idempotency_key() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503)

    with pytest.raises(DoeeddError) as caught:
        make_client(handler).create_transaction({"amount": 1})
    assert caught.value.kind == "server"
    assert len(calls) == 1


def test_retries_keyed_post_and_reports_replay() -> None:
    keys: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        keys.append(request.headers["idempotency-key"])
        if len(keys) == 1:
            return httpx.Response(502)
        return httpx.Response(201, json={"id": "tx"}, headers={"Idempotency-Replayed": "true"})

    transaction, replayed = make_client(handler).create_transaction({"amount": 1}, "telegram:1:2:1")
    assert transaction == {"id": "tx"}
    assert replayed is True
    assert keys == ["telegram:1:2:1", "telegram:1:2:1"]


def test_network_failure_is_reported_as_unreachable() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(DoeeddError) as caught:
        make_client(handler, retries=1).me()
    assert caught.value.kind == "unreachable"
    assert len(calls) == 2
