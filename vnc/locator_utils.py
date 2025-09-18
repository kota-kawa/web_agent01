# vnc/locator_utils.py
"""
SmartLocator — どのサイトにも耐える多段フォールバックロケータ

優先度:
 1) data-testid
 2) role=button[name="…"] などの ARIA Role
 3) <label>／aria-label／placeholder
 4) 可視テキスト
 5) 明示 css=/text=/role= プレフィクス
 6) 裸の CSS セレクタ

各候補を 3000 ms 以内に発見できなければ次へ。

target に "css=btn || text=Next" のように "||" 区切りで複数の
候補を与えると、左から順に試行する。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from playwright.async_api import Locator, Page

LOCATOR_TIMEOUT = int(os.getenv("LOCATOR_TIMEOUT", "2000"))  # ms(8000)

@dataclass
class _CssSelectorHint:
    tag: Optional[str]
    attributes: Dict[str, str]

    @property
    def placeholder(self) -> Optional[str]:
        return self.attributes.get("placeholder")

    def uses_hint(self, candidate: str) -> bool:
        if not self.attributes:
            return True

        normalized_candidate = candidate.lower().replace("\\'", "'").replace('\\"', '"')
        for attr, value in self.attributes.items():
            attr_lower = attr.lower()
            if f"[{attr_lower}" in normalized_candidate:
                return True
            if value:
                value_lower = value.lower()
                if value_lower in normalized_candidate:
                    return True
        return False


def _parse_role_selector(value: str) -> Optional[Tuple[str, Optional[str]]]:
    match = re.fullmatch(
        r"role=([a-z0-9_-]+)(?:\[name=(['\"])(.+?)\2\])?",
        value,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    role, _, name = match.groups()
    return role, name


def _extract_css_hint(selector: str) -> _CssSelectorHint:
    tag_match = re.match(r"\s*([a-zA-Z][\w-]*)", selector)
    tag = tag_match.group(1).lower() if tag_match else None

    attributes: Dict[str, str] = {}
    for attr, quote, value in re.findall(r"\[([\w:-]+)\s*=\s*(['\"])(.*?)\2\]", selector):
        attributes[attr.lower()] = value

    return _CssSelectorHint(tag=tag, attributes=attributes)


def _css_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("'", "\\'")
        .replace("]", "\\]")
    )


class SmartLocator:

    def __init__(self, page: Page, target: str) -> None:
        self.page = page
        self.raw = target.strip()

    async def _locate_one(self, t: str) -> Optional[Locator]:
        """Return first matching locator for a single selector string."""
        # 明示プレフィクス
        if t.startswith("css="):
            return await self._try(self.page.locator(t[4:]))
        if t.startswith("text="):
            return await self._try(self.page.get_by_text(t[5:], exact=True))
        if t.startswith("role="):
            parsed = _parse_role_selector(t)
            if parsed:
                role, name = parsed
                return await self._locate_by_role(role, name)
        if t.startswith("xpath="):
            return await self._try(self.page.locator(t))

        # data-testid
        loc = await self._try(self.page.locator(f"[data-testid='{t}']"))
        if loc:
            return loc

        # label / aria-label / placeholder
        loc = await self._try(self.page.get_by_label(t, exact=True))
        if loc:
            return loc
        loc = await self._locate_by_placeholder(t)
        if loc:
            return loc
        # label text followed by input element
        loc = await self._try(self.page.locator(f"label:has-text('{t}') + input"))
        if loc:
            return loc
        loc = await self._try(self.page.locator(f"xpath=//*[normalize-space(text())='{t}']/following::input[1]"))
        if loc:
            return loc

        # 可視テキスト
        loc = await self._try(self.page.get_by_text(t, exact=True))
        if loc:
            return loc
        loc = await self._try(self.page.locator(f"xpath=//*[contains(normalize-space(text()), '{t}')][1]"))
        if loc:
            return loc

        # 最後に裸 CSS
        return await self._try(self.page.locator(t))

    async def _try(self, loc: Locator) -> Optional[Locator]:
        """Enhanced try method with better element waiting strategies."""
        try:
            # Enhanced waiting strategy
            await loc.first.wait_for(state="attached", timeout=LOCATOR_TIMEOUT)

            # Additional checks for interactive elements
            if await self._is_interactive_element(loc):
                # For interactive elements, also ensure they're visible and enabled
                await loc.first.wait_for(state="visible", timeout=LOCATOR_TIMEOUT)

                # For form elements, wait for them to be enabled
                if await self._is_form_element(loc):
                    await self._wait_for_element_ready(loc, timeout=LOCATOR_TIMEOUT)

            return loc
        except Exception:
            return None

    async def _locate_by_role(self, role: str, name: Optional[str]) -> Optional[Locator]:
        if name:
            loc = await self._try(self.page.get_by_role(role, name=name, exact=True))
            if loc:
                return loc
            loc = await self._try(self.page.get_by_role(role, name=name, exact=False))
            if loc:
                return loc
        return await self._try(self.page.get_by_role(role))

    async def _locate_by_placeholder(self, placeholder: str) -> Optional[Locator]:
        if not placeholder:
            return None

        escaped = _css_escape(placeholder)
        candidate_factories = [
            lambda: self.page.get_by_placeholder(placeholder, exact=True),
            lambda: self.page.locator(f'input[placeholder="{escaped}"]'),
            lambda: self.page.locator(f'textarea[placeholder="{escaped}"]'),
        ]

        for factory in candidate_factories:
            try:
                candidate = factory()
            except Exception:
                continue

            loc = await self._try(candidate)
            if loc and await self._attribute_equals(loc, "placeholder", placeholder):
                return loc
        return None

    async def _attribute_equals(self, loc: Locator, attribute: str, expected: str) -> bool:
        try:
            value = await loc.first.get_attribute(attribute)
        except Exception:
            return False
        return value == expected

    async def _is_interactive_element(self, loc: Locator) -> bool:
        """Check if element is interactive (button, input, link, etc.)"""
        try:
            tag = await loc.first.evaluate("el => el.tagName.toLowerCase()")
            return tag in ["button", "input", "a", "select", "textarea"]
        except Exception:
            return False

    async def _is_form_element(self, loc: Locator) -> bool:
        """Check if element is a form element"""
        try:
            tag = await loc.first.evaluate("el => el.tagName.toLowerCase()")
            return tag in ["input", "select", "textarea"]
        except Exception:
            return False

    async def _wait_for_element_ready(self, loc: Locator, timeout: int = 2000):
        """Wait for element to be ready for interaction"""
        try:
            # Use JavaScript to check element readiness
            script = """
                (element, timeout) => {
                    return new Promise((resolve) => {
                        const start = Date.now();
                        const check = () => {
                            if (Date.now() - start > timeout) {
                                resolve(false);
                                return;
                            }
                            
                            // Check if element is ready
                            const rect = element.getBoundingClientRect();
                            const isVisible = rect.width > 0 && rect.height > 0;
                            const isEnabled = !element.disabled;
                            const isNotReadonly = !element.readOnly;
                            
                            if (isVisible && isEnabled && isNotReadonly) {
                                resolve(true);
                            } else {
                                setTimeout(check, 100);
                            }
                        };
                        check();
                    });
                }
            """
            await loc.first.evaluate(script, timeout)
        except Exception:
            # Fallback to basic wait
            await loc.first.page.wait_for_timeout(100)


    async def locate(self) -> Optional[Locator]:
        t = self.raw

        # Multiple fallbacks separated by "||"
        if "||" in t:
            for cand in [c.strip() for c in t.split("||") if c.strip()]:
                loc = await SmartLocator(self.page, cand).locate()
                if loc:
                    return loc
            return None

        # Enhanced selector handling with automatic fallbacks
        return await self._locate_with_enhanced_fallbacks(t)

    async def _locate_with_enhanced_fallbacks(self, t: str) -> Optional[Locator]:
        """Enhanced locate with automatic fallbacks for common problematic selectors."""
        
        # 明示プレフィクス
        if t.startswith("css="):
            original_selector = t[4:]
            hint = _extract_css_hint(original_selector)
            loc = await self._try(self.page.locator(original_selector))
            if loc:
                return loc
            if hint.placeholder:
                loc = await self._locate_by_placeholder(hint.placeholder)
                if loc:
                    return loc
            # Enhanced fallbacks for CSS selectors
            return await self._try_css_fallbacks(original_selector, hint)

        if t.startswith("text="):
            return await self._try(self.page.get_by_text(t[5:], exact=True))
        if t.startswith("role="):
            parsed = _parse_role_selector(t)
            if parsed:
                role, name = parsed
                return await self._locate_by_role(role, name)
        if t.startswith("xpath="):
            return await self._try(self.page.locator(t))

        # data-testid
        loc = await self._try(self.page.locator(f"[data-testid='{t}']"))
        if loc:
            return loc

        # label / aria-label / placeholder
        loc = await self._try(self.page.get_by_label(t, exact=True))
        if loc:
            return loc
        loc = await self._locate_by_placeholder(t)
        if loc:
            return loc
        # label text followed by input element
        loc = await self._try(self.page.locator(f"label:has-text('{t}') + input"))
        if loc:
            return loc
        loc = await self._try(self.page.locator(f"xpath=//*[normalize-space(text())='{t}']/following::input[1]"))
        if loc:
            return loc

        # 可視テキスト
        loc = await self._try(self.page.get_by_text(t, exact=True))
        if loc:
            return loc
        loc = await self._try(self.page.locator(f"xpath=//*[contains(normalize-space(text()), '{t}')][1]"))
        if loc:
            return loc

        # 最後に裸 CSS - with enhanced fallbacks
        hint = _extract_css_hint(t)
        loc = await self._try(self.page.locator(t))
        if loc:
            return loc
        if hint.placeholder:
            loc = await self._locate_by_placeholder(hint.placeholder)
            if loc:
                return loc
        return await self._try_css_fallbacks(t, hint)

    async def _try_css_fallbacks(
        self, selector: str, hint: _CssSelectorHint
    ) -> Optional[Locator]:
        """Try enhanced fallbacks for CSS selectors that commonly fail."""

        # Checkbox fallbacks
        if "checkbox" in selector and "[value=" in selector:
            # Try simpler checkbox selectors
            fallbacks = [
                "input[type=checkbox]:visible",
                "input[type=checkbox]",
                "[type=checkbox]:visible",
                "[type=checkbox]"
            ]
            for fallback in fallbacks:
                if not hint.uses_hint(fallback):
                    continue
                loc = await self._try(self.page.locator(fallback))
                if loc:
                    return loc

        # Button with aria-label fallbacks
        if "button" in selector and "aria-label" in selector:
            # Extract aria-label value
            match = re.search(r"aria-label=['\"]([^'\"]+)['\"]", selector)
            if match:
                label_text = match.group(1)
                fallbacks = [
                    f"button:has-text('{label_text}')",
                    f"[aria-label*='{label_text}']",
                    f"button[title='{label_text}']",
                    f"*[role=button][aria-label*='{label_text}']",
                    f"button:visible",
                    f"[role=button]:visible"
                ]
                for fallback in fallbacks:
                    if not hint.uses_hint(fallback):
                        continue
                    loc = await self._try(self.page.locator(fallback))
                    if loc:
                        return loc

        # Dynamic index-based selectors fallbacks
        if "data-cl_cl_index" in selector or "[data-" in selector:
            # Try more general approaches for dynamic attributes
            fallbacks = [
                "a:visible",
                "[role=link]:visible",
                "a[href]:visible"
            ]
            for fallback in fallbacks:
                if not hint.uses_hint(fallback):
                    continue
                loc = await self._try(self.page.locator(fallback))
                if loc:
                    return loc

        # Input field fallbacks
        if "input" in selector:
            fallbacks = [
                "input:visible",
                "[contenteditable]:visible",
                "textarea:visible"
            ]
            for fallback in fallbacks:
                if not hint.uses_hint(fallback):
                    continue
                loc = await self._try(self.page.locator(fallback))
                if loc:
                    return loc

        # General element type fallbacks
        if selector.startswith(("button", "input", "a", "div")):
            element_type = selector.split("[")[0].split(".")[0].split("#")[0]
            fallback = f"{element_type}:visible"
            if hint.uses_hint(fallback):
                loc = await self._try(self.page.locator(fallback))
                if loc:
                    return loc

        return None

