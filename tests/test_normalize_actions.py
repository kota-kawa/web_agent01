import pytest

from web.app import normalize_actions


def _extract_targets(actions):
    return [action.get("target") for action in actions]


def _extract_selectors(actions):
    return [action.get("selector") for action in actions]


def test_normalize_actions_converts_index_dict():
    actions = normalize_actions({
        "actions": [
            {"action": "click", "target": {"index": 21}},
        ]
    })

    assert _extract_targets(actions) == ["index=21"]
    assert _extract_selectors(actions) == [{"index": 21}]


def test_normalize_actions_converts_css_dict():
    actions = normalize_actions({
        "actions": [
            {"action": "click", "target": {"css": "button.submit"}},
        ]
    })

    assert _extract_targets(actions) == ["css=button.submit"]
    assert _extract_selectors(actions) == [{"css": "button.submit"}]


def test_normalize_actions_converts_role_dict_with_name():
    actions = normalize_actions({
        "actions": [
            {"action": "click", "target": {"role": "button", "text": "送信"}},
        ]
    })

    assert _extract_targets(actions) == ['role=button[name="送信"]']
    assert _extract_selectors(actions) == [{"role": "button", "text": "送信"}]


def test_normalize_actions_joins_selector_list():
    actions = normalize_actions({
        "actions": [
            {"action": "click", "target": ["css=button.primary", {"index": 3}]},
        ]
    })

    assert _extract_targets(actions) == ["css=button.primary || index=3"]
    assert _extract_selectors(actions) == [["css=button.primary", {"index": 3}]]


def test_normalize_actions_handles_direct_string_target():
    actions = normalize_actions({
        "actions": [
            {"action": "click", "target": "css=a[href='/?p=1']"},
        ]
    })

    assert _extract_targets(actions) == ["css=a[href='/?p=1']"]
    assert _extract_selectors(actions) == [None]


def test_normalize_actions_preserves_frame_metadata():
    actions = normalize_actions({
        "actions": [
            {
                "action": "click",
                "target": {
                    "selector": {"css": ".btn"},
                    "frame": {"strategy": "index", "value": 1},
                },
            },
        ]
    })

    assert _extract_targets(actions) == ["css=.btn"]
    assert _extract_selectors(actions) == [
        {"selector": {"css": ".btn"}, "frame": {"strategy": "index", "value": 1}}
    ]


def test_normalize_actions_preserves_priority_and_near_text():
    actions = normalize_actions({
        "actions": [
            {
                "action": "click",
                "target": {
                    "selector": {"css": ".cta"},
                    "priority": ["css", "near_text"],
                    "near_text": "Submit",
                    "frame": {"strategy": "index", "value": 2},
                },
            },
        ]
    })

    normalized = actions[0]
    assert normalized["selector"] == {
        "selector": {"css": ".cta"},
        "priority": ["css", "near_text"],
        "near_text": "Submit",
        "frame": {"strategy": "index", "value": 2},
    }
    assert normalized["target"] == "css=.cta"


def test_normalize_actions_handles_selectors_collection():
    actions = normalize_actions({
        "actions": [
            {
                "action": "click",
                "selectors": [
                    {
                        "selector": {"css": ".primary"},
                        "frame": {"strategy": "index", "value": 0},
                    },
                    {
                        "selector": {"role": "button", "text": "Submit"},
                        "priority": "role",
                    },
                ],
            },
        ]
    })

    normalized = actions[0]
    selectors = normalized["selectors"]

    assert isinstance(selectors, list)
    assert selectors[0]["frame"] == {"strategy": "index", "value": 0}
    assert selectors[1]["priority"] == ["role"]
    assert normalized["target"] == 'css=.primary || role=button[name="Submit"]'
