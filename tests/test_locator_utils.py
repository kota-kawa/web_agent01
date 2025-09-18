import asyncio
import types

from vnc.locator_utils import (
    SmartLocator,
    _css_escape,
    _extract_css_hint,
    _parse_role_selector,
)


class DummyLocator:
    def __init__(self, placeholder: str):
        self._placeholder = placeholder
        self.first = self

    async def wait_for(self, *args, **kwargs):
        return None

    async def get_attribute(self, name: str):
        if name == "placeholder":
            return self._placeholder
        return None


def test_parse_role_selector_with_name():
    assert _parse_role_selector('role=button[name="Submit"]') == ("button", "Submit")


def test_parse_role_selector_without_name():
    assert _parse_role_selector("role=textbox") == ("textbox", None)


def test_parse_role_selector_invalid():
    assert _parse_role_selector("role-button") is None


def test_locate_by_placeholder_prefers_exact(monkeypatch):
    placeholder = "Search"
    page = types.SimpleNamespace()
    locator = DummyLocator(placeholder)

    def get_by_placeholder(value, exact=True):
        assert exact is True
        return locator

    page.get_by_placeholder = get_by_placeholder

    def fail_locator(selector):
        raise AssertionError("should not use fallback")

    page.locator = fail_locator

    async def fake_try(self, candidate):
        return candidate

    monkeypatch.setattr(SmartLocator, "_try", fake_try)

    smart = SmartLocator(page, "dummy")
    result = asyncio.run(smart._locate_by_placeholder(placeholder))
    assert result is locator


def test_locate_by_placeholder_uses_fallback_on_mismatch(monkeypatch):
    placeholder = 'Say "hi"'
    page = types.SimpleNamespace()
    selectors = []

    def get_by_placeholder(value, exact=True):
        assert exact is True
        return DummyLocator("Mismatch")

    page.get_by_placeholder = get_by_placeholder

    def locator_factory(selector):
        selectors.append(selector)
        if selector.startswith("input"):
            return DummyLocator(placeholder)
        raise AssertionError("unexpected fallback selector")

    page.locator = locator_factory

    async def fake_try(self, candidate):
        return candidate

    monkeypatch.setattr(SmartLocator, "_try", fake_try)

    smart = SmartLocator(page, "dummy")
    result = asyncio.run(smart._locate_by_placeholder(placeholder))
    assert result is not None
    expected = f'input[placeholder="{_css_escape(placeholder)}"]'
    assert selectors[0] == expected


def test_css_fallbacks_skip_generic_when_hint_present(monkeypatch):
    page = types.SimpleNamespace()

    def fail_locator(selector):
        raise AssertionError(f"should not request fallback {selector}")

    page.locator = fail_locator

    async def fail_try(self, candidate):
        raise AssertionError("should not invoke _try for skipped fallbacks")

    monkeypatch.setattr(SmartLocator, "_try", fail_try)

    smart = SmartLocator(page, "dummy")
    hint = _extract_css_hint("input[data-testid='foo']")
    result = asyncio.run(smart._try_css_fallbacks("input[data-testid='foo']", hint))
    assert result is None


def test_extract_css_hint_placeholder():
    hint = _extract_css_hint('textarea[placeholder="Write here"]')
    assert hint.placeholder == "Write here"
