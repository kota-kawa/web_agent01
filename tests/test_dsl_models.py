from automation.dsl import RunRequest, models, registry
from agent.actions import basic


def test_registry_parses_legacy_navigate():
    action = registry.parse_action({"action": "navigate", "target": "https://example.com"})
    assert isinstance(action, models.NavigateAction)
    assert action.url == "https://example.com"


def test_registry_parses_new_actions():
    hover = registry.parse_action({"action": "hover", "target": "#item"})
    assert isinstance(hover, models.HoverAction)

    scroll_text = registry.parse_action({"action": "scroll_to_text", "target": "Example"})
    assert isinstance(scroll_text, models.ScrollToTextAction)

    blank = registry.parse_action({"action": "click_blank_area"})
    assert isinstance(blank, models.ClickBlankAreaAction)

    stop = registry.parse_action({"action": "stop", "reason": "pause", "message": "waiting"})
    assert isinstance(stop, models.StopAction)


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


def test_new_action_legacy_payloads_and_wrappers_align():
    hover_action = models.HoverAction(selector="#link")
    assert hover_action.legacy_payload() == basic.hover("#link")

    scroll_text = models.ScrollToTextAction(text="Example")
    assert scroll_text.legacy_payload() == basic.scroll_to_text("Example")

    refresh = models.RefreshCatalogAction()
    assert refresh.legacy_payload() == basic.refresh_catalog()

    blank = models.ClickBlankAreaAction()
    assert blank.legacy_payload() == basic.click_blank_area()

    close = models.ClosePopupAction()
    assert close.legacy_payload() == basic.close_popup()

    eval_action = models.EvalJsAction(script="2 + 2")
    assert eval_action.legacy_payload() == basic.eval_js("2 + 2")

    stop_action = models.StopAction(reason="test", message="please confirm")
    assert stop_action.legacy_payload() == basic.stop("test", "please confirm")


def test_press_key_legacy_payload_combines_with_plus():
    action = models.PressKeyAction(keys=["Control", "S"], scope="page")
    payload = action.legacy_payload()
    assert payload["action"] == "press_key"
    assert payload["key"] == "Control+S"


def test_scroll_action_direction_only_payload():
    action = models.ScrollAction(direction="down")
    payload = action.legacy_payload()
    assert payload["action"] == "scroll"
    assert payload["direction"] == "down"
