from web.app import normalize_actions


def test_normalize_actions_builds_typed_index_selector():
    result = normalize_actions({
        "actions": [
            {"action": "click", "target": {"index": 21}},
        ]
    })

    assert result.has_typed
    assert result.legacy == []
    assert result.typed[0]["type"] == "click"
    assert result.typed[0]["selector"] == {"index": 21}


def test_normalize_actions_builds_typed_css_selector():
    result = normalize_actions({
        "actions": [
            {"action": "click", "target": {"css": "button.submit"}},
        ]
    })

    assert result.has_typed
    assert result.typed[0]["selector"]["css"] == "button.submit"


def test_normalize_actions_preserves_role_and_text():
    result = normalize_actions({
        "actions": [
            {"action": "click", "target": {"role": "button", "text": "送信"}},
        ]
    })

    assert result.has_typed
    selector = result.typed[0]["selector"]
    assert selector["role"] == "button"
    assert selector["text"] == "送信"


def test_normalize_actions_falls_back_to_legacy_when_unsupported():
    result = normalize_actions({
        "actions": [
            {"action": "refresh_catalog"},
        ]
    })

    assert not result.has_typed
    assert result.legacy[0]["action"] == "refresh_catalog"
