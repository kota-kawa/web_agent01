import sys
import types
from collections import OrderedDict
from typing import Optional

import pytest

if "jsonschema" not in sys.modules:
    jsonschema_stub = types.ModuleType("jsonschema")

    class _DummyValidator:
        def __init__(self, *args, **kwargs) -> None:  # pragma: no cover - simple stub
            self.schema = args[0] if args else None

        def iter_errors(self, _instance):  # pragma: no cover - simple stub
            return []

    class _DummyValidationError(Exception):
        pass

    jsonschema_stub.Draft7Validator = _DummyValidator
    jsonschema_stub.ValidationError = _DummyValidationError
    sys.modules["jsonschema"] = jsonschema_stub

import vnc.automation_server as automation


def _build_catalog_entry(
    index: int,
    *,
    dom_path_hash: str,
    primary_label: str,
    role: str = "button",
    href: str = "",
    selectors: Optional[list[str]] = None,
    nearest_texts: Optional[list[str]] = None,
) -> dict:
    return {
        "index": index,
        "dom_path_hash": dom_path_hash,
        "primary_label": primary_label,
        "secondary_label": "",
        "section_hint": "",
        "state_hint": "",
        "role": role,
        "tag": role if role else "button",
        "href_full": href,
        "href_short": href,
        "robust_selectors": selectors or [],
        "nearest_texts": nearest_texts or [],
    }


def test_store_and_load_catalog_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(automation, "INDEX_MODE", True)
    monkeypatch.setattr(automation, "CATALOG_CACHE_DIR", tmp_path)
    monkeypatch.setattr(automation, "CATALOG_CACHE_LIMIT", 1)
    monkeypatch.setattr(automation, "_CATALOG_ARCHIVE", OrderedDict())

    catalog_v1 = {
        "catalog_version": "v1",
        "index_map": {
            "5": _build_catalog_entry(
                5,
                dom_path_hash="hash-1",
                primary_label="Submit",
                selectors=["text=Submit"],
                nearest_texts=["Submit"],
            )
        },
        "dom_hash": "dom-1",
    }

    automation._store_catalog_snapshot(catalog_v1)
    snapshot_path = tmp_path / "v1.json"
    assert snapshot_path.exists()

    loaded_v1 = automation._load_catalog_snapshot("v1")
    assert loaded_v1 is not None
    assert loaded_v1["catalog_version"] == "v1"
    assert loaded_v1["index_map"]["5"]["primary_label"] == "Submit"

    catalog_v2 = {
        "catalog_version": "v2",
        "index_map": {
            "2": _build_catalog_entry(
                2,
                dom_path_hash="hash-2",
                primary_label="Continue",
                selectors=["text=Continue"],
            )
        },
        "dom_hash": "dom-2",
    }

    automation._store_catalog_snapshot(catalog_v2)
    assert not snapshot_path.exists()
    assert (tmp_path / "v2.json").exists()


def test_find_matching_catalog_entry_prioritizes_dom_and_textual_similarity():
    expected_entry = _build_catalog_entry(
        5,
        dom_path_hash="match-hash",
        primary_label="Place Order",
        selectors=["text=Place Order"],
        nearest_texts=["Order"],
    )

    candidate_index_map = {
        "2": _build_catalog_entry(
            2,
            dom_path_hash="match-hash",
            primary_label="Checkout",
            selectors=["text=Checkout"],
            nearest_texts=["Checkout"],
        ),
        "3": _build_catalog_entry(
            3,
            dom_path_hash="different",
            primary_label="Confirm Purchase",
            selectors=["text=Confirm Purchase"],
            nearest_texts=["Confirm"],
        ),
    }

    match_index, _, reason = automation._find_matching_catalog_entry(
        expected_entry, candidate_index_map
    )
    assert match_index == 2
    assert reason["dom_hash"] == pytest.approx(1.0)

    expected_entry_no_dom = dict(expected_entry)
    expected_entry_no_dom["dom_path_hash"] = ""
    expected_entry_no_dom["primary_label"] = "Confirm Purchase"
    expected_entry_no_dom["nearest_texts"] = ["Confirm"]

    match_index_textual, _, reason_textual = automation._find_matching_catalog_entry(
        expected_entry_no_dom, candidate_index_map
    )
    assert match_index_textual == 3
    assert reason_textual["textual"] > reason["textual"]


def test_rebind_actions_for_catalog_updates_targets(monkeypatch, tmp_path):
    monkeypatch.setattr(automation, "INDEX_MODE", True)
    monkeypatch.setattr(automation, "CATALOG_CACHE_DIR", tmp_path)
    monkeypatch.setattr(automation, "_CATALOG_ARCHIVE", OrderedDict())
    monkeypatch.setattr(automation, "CATALOG_CACHE_LIMIT", 3)

    expected_version = "ver-1"
    expected_catalog = {
        "catalog_version": expected_version,
        "index_map": {
            "5": _build_catalog_entry(
                5,
                dom_path_hash="stable-hash",
                primary_label="Checkout",
                selectors=[],
                nearest_texts=["Proceed to checkout"],
                href="https://example.com/checkout",
            )
        },
        "dom_hash": "dom-old",
    }
    automation._store_catalog_snapshot(expected_catalog)

    current_catalog = {
        "catalog_version": "ver-2",
        "index_map": {
            "2": _build_catalog_entry(
                2,
                dom_path_hash="stable-hash",
                primary_label="Checkout",
                selectors=["role=button[name=\"Checkout\"]"],
                nearest_texts=["Checkout"],
                href="https://example.com/checkout",
            )
        },
    }

    actions = [{"action": "click", "target": "index=5"}]

    messages = automation._rebind_actions_for_catalog(
        actions, expected_version, current_catalog
    )

    assert actions[0]["target"] == "index=2"
    binding = actions[0]["_catalog_binding"]
    assert binding["expected_index"] == 5
    assert binding["match_index"] == 2
    assert binding["selectors"]
    assert "text=Checkout" in binding["selectors"]
    assert binding["match_reason"]["dom_hash"] == pytest.approx(1.0)
    assert messages
    assert messages[0].startswith("INFO:auto:Catalog index 5 rebound to 2")
