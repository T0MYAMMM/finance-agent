from pathlib import Path

from doeedd_agent.aliases import AliasStore, Match, resolve
from doeedd_agent.outbox import Outbox
from doeedd_agent.state import StateStore, key_group

NAMES = ["Food", "Shopping", "GoPay"]


def test_resolves_names_before_aliases() -> None:
    assert resolve("  food ", NAMES, {}) == Match("Food")
    assert resolve("kopi", NAMES, {"kopi": "Food"}) == Match("Food")
    assert resolve("indomaret", NAMES, {"indomaret": None}) == Match(None, ambiguous=True)
    assert resolve("travel", NAMES, {"travel": "Travel"}) == Match(None)  # target not in doeedd
    assert resolve("unknown", NAMES, {}) == Match(None)


def test_alias_overrides_persist_and_merge_with_defaults(tmp_path: Path) -> None:
    store = AliasStore(tmp_path / "aliases.json")
    assert store.merged()["category"]["kopi"] == "Food"

    store.set("category", "Kopi", "Shopping")
    store.set("account", "Jago", "Jago Bank")
    store.set("default_account", "", "Mandiri")
    merged = AliasStore(tmp_path / "aliases.json").merged()
    assert merged["category"]["kopi"] == "Shopping"
    assert merged["account"]["jago"] == "Jago Bank"
    assert merged["default_account"] == "Mandiri"

    assert store.unset("category", "kopi") is True
    assert store.merged()["category"]["kopi"] == "Food"


def test_remember_merchant_merges_fields(tmp_path: Path) -> None:
    store = AliasStore(tmp_path / "aliases.json")
    store.remember_merchant("Tomoro Coffee", account="BCA")
    defaults = store.merged()["merchant_defaults"]["tomoro coffee"]
    assert defaults == {
        "category": "Food",
        "account": "BCA",
        "subcategory": "Coffee",
        "payment_method": "QRIS",
    }


def test_capture_groups_follow_the_message() -> None:
    assert key_group("telegram:1:42:2") == "telegram:1:42"
    assert key_group("agent:abc") == "agent:abc"
    assert key_group(None) is None


def test_state_undo_group_restore_and_habits(tmp_path: Path) -> None:
    state = StateStore(tmp_path / "state.json")
    state.record_capture("telegram:1:42:1", "a")
    state.record_capture("telegram:1:42:2", "b")
    assert state.last_capture_ids() == ["a", "b"]

    state.record_capture("telegram:1:43:1", "c")
    assert state.last_capture_ids() == ["c"]

    state.record_deleted(["c"])
    assert state.last_capture_ids() == []
    assert state.take_deleted_ids() == ["c"]
    assert state.last_capture_ids() == ["c"]
    assert state.take_deleted_ids() == []

    state.remember_account("Food", "GoPay")
    assert state.account_for_category("Food") == "GoPay"
    assert state.account_for_category("Travel") is None


def test_outbox_queue_remove_and_fail(tmp_path: Path) -> None:
    outbox = Outbox(tmp_path / "outbox.json")
    first = outbox.append({"kind": "capture", "key": "k1"})
    second = outbox.append({"kind": "capture", "key": "k2"})
    assert [entry["key"] for entry in outbox.entries()] == ["k1", "k2"]

    outbox.remove(first)
    entry = outbox.entries()[0]
    outbox.fail(entry, {"kind": "validation"})
    assert len(outbox) == 0
    assert entry["id"] == second
    assert outbox.failed_path.is_file()
    assert (tmp_path / "outbox.json").stat().st_mode & 0o777 == 0o600
