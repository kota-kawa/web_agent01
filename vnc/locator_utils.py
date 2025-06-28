# vnc/locator_utils.py
"""
汎用スマートロケータユーティリティ

どの Web サイトでも動作可能なように、与えられた target 文字列を多段階で解釈し、
最初にヒットした Playwright Locator を返す。ヒットしなければ None。
"""
from __future__ import annotations
import re
from typing import Optional

from playwright.async_api import Page, Locator


class SmartLocator:
    """target 文字列を解釈し、最適な Locator (または None) を非同期に返す"""

    _ROLE_PATTERN = re.compile(r"^role=(\w+)\[name=['\"](.+?)['\"]]$", re.I)

    def __init__(self, page: Page, target: str):
        self.page: Page = page
        self.target: str = target.strip()

    async def _try(self, locator: Locator) -> Optional[Locator]:
        """0.3 秒以内に要素が見つかればそのロケータを返す"""
        try:
            await locator.first.wait_for(state="attached", timeout=300)
            return locator
        except Exception:
            return None

    async def locate(self) -> Optional[Locator]:
        t: str = self.target

        # --- 明示プレフィクス (早期判定) ---
        if t.startswith("css="):
            return await self._try(self.page.locator(t[4:]))
        if t.startswith("text="):
            return await self._try(self.page.get_by_text(t[5:], exact=True))
        if t.startswith("role="):
            m = self._ROLE_PATTERN.match(t)
            if m:
                role, name = m.groups()
                return await self._try(
                    self.page.get_by_role(role, name=name, exact=True)
                )

        # --- data-testid 属性 ---
        loc = await self._try(self.page.locator(f"[data-testid='{t}']"))
        if loc:
            return loc

        # --- aria/label/placeholder ---
        loc = await self._try(self.page.get_by_label(t, exact=True))
        if loc:
            return loc
        loc = await self._try(self.page.locator(f"input[placeholder='{t}']"))
        if loc:
            return loc

        # --- 可視テキスト ---
        loc = await self._try(self.page.get_by_text(t, exact=True))
        if loc:
            return loc

        # --- 最後に裸の CSS として解釈 ---
        return await self._try(self.page.locator(t))
