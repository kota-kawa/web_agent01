"""Tests for ``normalize_actions`` selector handling."""

from __future__ import annotations

import copy

from web.app import normalize_actions


def _single_action_response(action: dict) -> dict:
    """Utility wrapper returning a minimal LLM-style payload."""

    payload = {"actions": [copy.deepcopy(action)]}
    return payload


def test_preserves_composite_selector_for_typed_action():
    composite_selector = [
        {"css": "button.submit"},
        {"stable_id": "submit-button"},
    ]
    response = _single_action_response(
        {
            "action": "click",
            "selector": composite_selector,
            "button": "left",
            "click_count": 2,
        }
    )

    actions = normalize_actions(response)

    assert len(actions) == 1
    normalized = actions[0]
    # The original selector list should survive untouched.
    assert normalized["target"] == composite_selector
    # Ensure numeric fields are kept as integers.
    assert normalized["click_count"] == 2


def test_wait_condition_structure_preserved():
    response = _single_action_response(
        {
            "action": "wait",
            "timeout_ms": 5000,
            "for": {
                "selector": {"css": "div.ready"},
                "state": "visible",
            },
        }
    )

    actions = normalize_actions(response)

    assert len(actions) == 1
    normalized = actions[0]
    assert normalized["timeout_ms"] == 5000
    assert normalized["for"] == {
        "selector": {"css": "div.ready"},
        "state": "visible",
    }


def test_typed_select_keeps_value_or_label_type():
    response = _single_action_response(
        {
            "action": "select",
            "selector": {"css": "select#country"},
            "value_or_label": 3,
        }
    )

    actions = normalize_actions(response)

    assert len(actions) == 1
    normalized = actions[0]
    assert normalized["target"] == {"css": "select#country"}
    assert normalized["value_or_label"] == 3


def test_legacy_action_stringifies_selector():
    response = _single_action_response(
        {
            "action": "select_option",
            "selector": {"css": "select#region"},
            "value": 7,
        }
    )

    actions = normalize_actions(response)

    assert len(actions) == 1
    normalized = actions[0]
    assert normalized["target"] == "css=select#region"
    assert normalized["value"] == "7"


def test_explicit_legacy_flag_forces_string_target():
    response = _single_action_response(
        {
            "action": "click",
            "selector": {"css": "button.primary"},
            "legacy_only": True,
        }
    )

    actions = normalize_actions(response)

    assert len(actions) == 1
    normalized = actions[0]
    assert normalized["target"] == "css=button.primary"


def test_click_text_target_defaults_to_text():
    response = _single_action_response(
        {
            "action": "click_text",
            "text": "次へ",
        }
    )

    actions = normalize_actions(response)

    assert len(actions) == 1
    normalized = actions[0]
    assert normalized["target"] == "次へ"
