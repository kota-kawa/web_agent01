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
"""
from __future__ import annotations
import re, os
from typing import Optional

from playwright.async_api import Locator, Page

LOCATOR_TIMEOUT = int(os.getenv("LOCATOR_TIMEOUT", "8000"))  # ms

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
        try:
            await loc.first.wait_for(state="attached", timeout=LOCATOR_TIMEOUT)
            return loc
        except Exception:
            return None

    async def locate(self) -> Optional[Locator]:
        for part in self.raw.split("||"):
            t = part.strip()
            if not t:
                continue
            loc = await self._locate_one(t)
            if loc:
                return loc
        return None
