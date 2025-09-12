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
import re, os
from typing import Optional

from playwright.async_api import Locator, Page

LOCATOR_TIMEOUT = int(os.getenv("LOCATOR_TIMEOUT", "2000"))  # ms(8000)

class SmartLocator:
    _ROLE = re.compile(r"^role=(\w+)\[name=['\"](.+?)['\"]]$", re.I)

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
            m = self._ROLE.match(t)
            if m:
                role, name = m.groups()
                return await self._try(self.page.get_by_role(role, name=name, exact=True))
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
        loc = await self._try(self.page.locator(f"input[placeholder='{t}']"))
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
        """Wait for element to be ready for interaction using Playwright's built-in mechanisms"""
        try:
            # Use Playwright's built-in state waiting instead of custom JavaScript polling
            await loc.first.wait_for(state="visible", timeout=timeout)
            await loc.first.wait_for(state="attached", timeout=timeout)
            
            # Check if it's an interactive element and ensure it's enabled
            if await self._is_interactive_element(loc):
                # For interactive elements, wait for enabled state
                try:
                    # Use a more efficient check using evaluate without polling
                    is_ready = await loc.first.evaluate("""
                        element => {
                            const rect = element.getBoundingClientRect();
                            const isVisible = rect.width > 0 && rect.height > 0;
                            const isEnabled = !element.disabled;
                            const isNotReadonly = !element.readOnly;
                            return isVisible && isEnabled && isNotReadonly;
                        }
                    """)
                    if not is_ready:
                        # Brief wait for element to become ready, but cap it
                        await loc.first.page.wait_for_timeout(50)
                except Exception:
                    pass
        except Exception:
            # Enhanced fallback using Playwright states instead of fixed timeout
            try:
                await loc.first.wait_for(state="visible", timeout=min(timeout // 2, 1000))
            except Exception:
                # Minimal fallback timeout
                await loc.first.page.wait_for_timeout(50)


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
            loc = await self._try(self.page.locator(original_selector))
            if loc:
                return loc
            # Enhanced fallbacks for CSS selectors
            return await self._try_css_fallbacks(original_selector)
            
        if t.startswith("text="):
            return await self._try(self.page.get_by_text(t[5:], exact=True))
        if t.startswith("role="):
            m = self._ROLE.match(t)
            if m:
                role, name = m.groups()
                return await self._try(self.page.get_by_role(role, name=name, exact=True))
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
        loc = await self._try(self.page.locator(f"input[placeholder='{t}']"))
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
        loc = await self._try(self.page.locator(t))
        if loc:
            return loc
        return await self._try_css_fallbacks(t)

    async def _try_css_fallbacks(self, selector: str) -> Optional[Locator]:
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
                loc = await self._try(self.page.locator(fallback))
                if loc:
                    return loc
        
        # Button with aria-label fallbacks  
        if "button" in selector and "aria-label" in selector:
            # Extract aria-label value
            import re
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
                loc = await self._try(self.page.locator(fallback))
                if loc:
                    return loc
        
        # General element type fallbacks
        if selector.startswith(("button", "input", "a", "div")):
            element_type = selector.split("[")[0].split(".")[0].split("#")[0]
            fallback = f"{element_type}:visible"
            loc = await self._try(self.page.locator(fallback))
            if loc:
                return loc
                
        return None

