import pytest

from web.app import normalize_actions


def _extract_targets(actions):
    return [action.get("target") for action in actions]


def test_normalize_actions_converts_index_dict():
    actions = normalize_actions({
        "actions": [
            {"action": "click", "target": {"index": 21}},
        ]
    })

    assert _extract_targets(actions) == ["index=21"]


def test_normalize_actions_converts_css_dict():
    actions = normalize_actions({
        "actions": [
            {"action": "click", "target": {"css": "button.submit"}},
        ]
    })

    assert _extract_targets(actions) == ["css=button.submit"]


def test_normalize_actions_converts_role_dict_with_name():
    actions = normalize_actions({
        "actions": [
            {"action": "click", "target": {"role": "button", "text": "送信"}},
        ]
    })

    assert _extract_targets(actions) == ['role=button[name="送信"]']


def test_normalize_actions_joins_selector_list():
    actions = normalize_actions({
        "actions": [
            {"action": "click", "target": ["css=button.primary", {"index": 3}]},
        ]
    })

    assert _extract_targets(actions) == ["css=button.primary || index=3"]


def test_normalize_actions_handles_direct_string_target():
    actions = normalize_actions({
        "actions": [
            {"action": "click", "target": "css=a[href='/?p=1']"},
        ]
    })

    assert _extract_targets(actions) == ["css=a[href='/?p=1']"]
