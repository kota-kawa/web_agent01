from automation.dsl import RunRequest, models, registry
from agent.actions import basic


def test_registry_parses_legacy_navigate():
    action = registry.parse_action({"action": "navigate", "target": "https://example.com"})
    assert isinstance(action, models.NavigateAction)
    assert action.url == "https://example.com"


def test_click_action_legacy_payload_roundtrip():
    action = models.ClickAction(selector="#submit")
    payload = action.legacy_payload()
    assert payload == {"action": "click", "target": "#submit", "button": "left", "click_count": 1}


def test_wait_selector_conversion():
    cond = models.WaitForSelector(selector=".item")
    action = models.WaitAction(for_=cond, timeout_ms=2500)
    payload = action.legacy_payload()
    assert payload["action"] == "wait"
    assert payload["ms"] == 2500
    assert payload["until"] == "selector"
    assert payload["state"] == "visible"
    assert payload["target"] == ".item"


def test_run_request_validation_and_dump():
    request = RunRequest.model_validate(
        {
            "run_id": "sample",
            "plan": [
                {"type": "navigate", "url": "https://example.com"},
                {"type": "click", "selector": {"text": "Next"}},
                {"type": "assert", "selector": {"text": "Next"}, "state": "visible"},
            ],
        }
    )

    assert request.run_id == "sample"
    assert len(request.plan.actions) == 3
    dumped = request.to_payload()
    assert dumped["plan"][0]["type"] == "navigate"
    assert dumped["plan"][1]["selector"]["text"] == "Next"


def test_basic_helpers_use_typed_actions():
    payload = basic.navigate("https://example.com")
    assert payload == {"action": "navigate", "target": "https://example.com", "url": "https://example.com"}

    click_payload = basic.click("#btn")
    assert click_payload["action"] == "click"
    assert click_payload["target"] == "#btn"
