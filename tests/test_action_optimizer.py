import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.browser.dom import DOMElementNode
from agent.controller.action_optimizer import optimize_actions


def _make_dom():
    return DOMElementNode(
        tagName="body",
        children=[
            DOMElementNode(
                tagName="input",
                text="",
                attributes={"id": "search-input", "aria-label": "検索ワード"},
                highlightIndex=1,
            ),
            DOMElementNode(
                tagName="button",
                text="検索",
                attributes={"role": "button"},
                highlightIndex=5,
            ),
            DOMElementNode(
                tagName="a",
                text="詳細を見る",
                attributes={"role": "link"},
                highlightIndex=9,
            ),
        ],
    )


def _make_catalog():
    return {
        "full": [
            {
                "index": 1,
                "primary_label": "検索入力",
                "secondary_label": "",
                "role": "textbox",
                "tag": "input",
                "robust_selectors": ["css=#search-input", "role=textbox[name=\"検索入力\"]"],
                "nearest_texts": ["検索ワード"],
            },
            {
                "index": 5,
                "primary_label": "検索",
                "secondary_label": "ボタン",
                "role": "button",
                "tag": "button",
                "robust_selectors": ["role=button[name=\"検索\"]", "text=検索"],
                "nearest_texts": ["検索"],
            },
            {
                "index": 9,
                "primary_label": "詳細を見る",
                "secondary_label": "",
                "role": "link",
                "tag": "a",
                "robust_selectors": ["text=詳細を見る"],
                "nearest_texts": ["詳細を見る"],
            },
        ]
    }


def test_click_text_converted_to_click():
    actions = [{"action": "click_text", "text": "検索"}]
    optimized, notes = optimize_actions(actions, _make_catalog(), _make_dom())
    assert optimized[0]["action"] == "click"
    assert optimized[0]["target"] == {"index": 5}
    assert notes and "index=5" in notes[0]


def test_type_selector_uses_catalog_index():
    actions = [
        {"action": "type", "target": {"css": "#search-input"}, "text": "箱根"},
    ]
    optimized, notes = optimize_actions(actions, _make_catalog(), _make_dom())
    assert optimized[0]["target"] == {"index": 1}
    assert any("index=1" in note for note in notes)


def test_wait_for_selector_text_matching():
    actions = [
        {"action": "wait_for_selector", "target": "text=詳細を見る"},
    ]
    optimized, notes = optimize_actions(actions, _make_catalog(), _make_dom())
    assert optimized[0]["target"] == {"index": 9}
    assert any("index=9" in note for note in notes)


def test_no_change_when_match_not_found():
    actions = [{"action": "click", "target": "css=.unknown"}]
    optimized, notes = optimize_actions(actions, _make_catalog(), _make_dom())
    assert optimized[0]["target"] == "css=.unknown"
    assert notes == []
