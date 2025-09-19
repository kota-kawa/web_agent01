"""
Browser-use adapter for web_agent01

This module provides an adapter layer to integrate browser-use-style functionality
while maintaining compatibility with the existing Playwright-based interface.
Since browser-use ultimately uses Playwright under the hood, this implementation
directly uses Playwright with a browser-use style API.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from typing import Any, Dict, List, Optional, Union
from pathlib import Path

# Import Playwright for the actual browser operations
try:
    from playwright.async_api import async_playwright, Browser, Page, Playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    Browser = Page = Playwright = None

log = logging.getLogger(__name__)


class BrowserUseAdapter:
    """
    Adapter class that provides browser-use-style functionality using Playwright.
    This maintains compatibility while using a more modern, browser-use inspired interface.
    """
    
    def __init__(self):
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self._initialized = False
        self._headless = True
        
    async def initialize(self, headless: bool = True) -> bool:
        """Initialize browser instance using Playwright"""
        self._headless = headless
        
        if not PLAYWRIGHT_AVAILABLE:
            log.warning("Playwright not available, using placeholder mode")
            self._initialized = True
            return True
            
        try:
            self.playwright = await async_playwright().start()
            
            # Try to connect to existing CDP browser first (like original code)
            cdp_url = os.getenv("CDP_URL", "http://localhost:9222")
            try:
                import httpx
                async with httpx.AsyncClient(timeout=2) as client:
                    await client.get(f"{cdp_url}/json/version")
                self.browser = await self.playwright.chromium.connect_over_cdp(cdp_url)
                ctx = self.browser.contexts[0] if self.browser.contexts else await self.browser.new_context()
                self.page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                await self.page.bring_to_front()
                log.info("Connected to existing CDP browser")
            except Exception:
                # Fallback to launching new browser
                self.browser = await self.playwright.chromium.launch(headless=headless)
                self.page = await self.browser.new_page()
                log.info("Launched new browser instance")
            
            self._initialized = True
            return True
        except Exception as e:
            log.error(f"Failed to initialize browser: {e}")
            return False
    
    async def close(self):
        """Close browser instance"""
        try:
            if self.page:
                await self.page.close()
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
        except Exception as e:
            log.error(f"Error closing browser: {e}")
        finally:
            self.page = None
            self.browser = None
            self.playwright = None
            self._initialized = False
    
    async def navigate(self, url: str, wait_until: str = "load", timeout: int = 30000) -> Dict[str, Any]:
        """Navigate to URL"""
        if not self._initialized:
            await self.initialize()
            
        if not PLAYWRIGHT_AVAILABLE or not self.page:
            return {
                "success": True,
                "url": url,
                "message": f"Navigated to {url} (placeholder)"
            }
            
        try:
            await self.page.goto(url, wait_until=wait_until, timeout=timeout)
            return {
                "success": True,
                "url": url,
                "message": f"Successfully navigated to {url}"
            }
        except Exception as e:
            log.error(f"Navigation failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "url": url
            }
    
    async def click(self, selector: str, timeout: int = 10000) -> Dict[str, Any]:
        """Click element"""
        if not PLAYWRIGHT_AVAILABLE or not self.page:
            return {
                "success": True,
                "selector": selector,
                "message": f"Clicked {selector} (placeholder)"
            }
            
        try:
            await self.page.click(selector, timeout=timeout)
            return {
                "success": True,
                "selector": selector,
                "message": f"Successfully clicked {selector}"
            }
        except Exception as e:
            log.error(f"Click failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "selector": selector
            }
    
    async def fill(self, selector: str, text: str, timeout: int = 10000) -> Dict[str, Any]:
        """Fill input element"""
        if not PLAYWRIGHT_AVAILABLE or not self.page:
            return {
                "success": True,
                "selector": selector,
                "text": text,
                "message": f"Filled {selector} with '{text}' (placeholder)"
            }
            
        try:
            await self.page.fill(selector, text, timeout=timeout)
            return {
                "success": True,
                "selector": selector,
                "text": text,
                "message": f"Successfully filled {selector}"
            }
        except Exception as e:
            log.error(f"Fill failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "selector": selector
            }
    
    async def screenshot(self, full_page: bool = False, timeout: int = 15000) -> bytes:
        """Take screenshot with improved timeout handling"""
        if not PLAYWRIGHT_AVAILABLE or not self.page:
            # Return a minimal 1x1 PNG for compatibility
            return base64.b64decode(
                b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='
            )
            
        try:
            # Wait for fonts to load before screenshot
            await self.page.evaluate("""
                () => {
                    return document.fonts.ready;
                }
            """)
            
            # Use shorter timeout for screenshot to avoid long waits
            return await self.page.screenshot(
                type="png", 
                full_page=full_page, 
                timeout=timeout
            )
        except Exception as e:
            log.error(f"Screenshot failed: {e}")
            # Return a minimal error image instead of empty bytes
            return base64.b64decode(
                b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='
            )
    
    async def get_page_content(self) -> str:
        """Get page HTML content"""
        if not PLAYWRIGHT_AVAILABLE or not self.page:
            return "<html><body><h1>Browser-use adapter placeholder content</h1></body></html>"
            
        try:
            return await self.page.content()
        except Exception as e:
            log.error(f"Get page content failed: {e}")
            return ""
    
    async def get_url(self) -> str:
        """Get current page URL"""
        if not PLAYWRIGHT_AVAILABLE or not self.page:
            return "about:blank"
            
        try:
            return self.page.url
        except Exception as e:
            log.error(f"Get URL failed: {e}")
            return ""
    
    async def wait_for_selector(self, selector: str, timeout: int = 5000) -> Dict[str, Any]:
        """Wait for selector"""
        if not PLAYWRIGHT_AVAILABLE or not self.page:
            return {
                "success": True,
                "selector": selector,
                "message": f"Found {selector} (placeholder)"
            }
            
        try:
            await self.page.wait_for_selector(selector, timeout=timeout)
            return {
                "success": True,
                "selector": selector,
                "message": f"Successfully found {selector}"
            }
        except Exception as e:
            log.error(f"Wait for selector failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "selector": selector
            }
    
    async def evaluate(self, script: str, timeout: int = 5000) -> Any:
        """Evaluate JavaScript with improved error handling"""
        if not PLAYWRIGHT_AVAILABLE or not self.page:
            log.warning("Evaluate called but no page available")
            return None
            
        try:
            # Add timeout to evaluation to prevent hanging
            return await asyncio.wait_for(
                self.page.evaluate(script), 
                timeout=timeout/1000
            )
        except asyncio.TimeoutError:
            log.error(f"Evaluate timed out after {timeout}ms")
            return None
        except Exception as e:
            log.error(f"Evaluate failed: {e}")
            return None
    
    async def scroll(self, x: int = 0, y: int = 0) -> Dict[str, Any]:
        """Scroll page"""
        if not PLAYWRIGHT_AVAILABLE or not self.page:
            return {
                "success": True,
                "x": x,
                "y": y,
                "message": f"Scrolled by ({x}, {y}) (placeholder)"
            }
            
        try:
            await self.page.mouse.wheel(x, y)
            return {
                "success": True,
                "x": x,
                "y": y,
                "message": f"Successfully scrolled by ({x}, {y})"
            }
        except Exception as e:
            log.error(f"Scroll failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def is_healthy(self) -> bool:
        """Check if browser instance is healthy"""
        if not self._initialized:
            return False
            
        if not PLAYWRIGHT_AVAILABLE or not self.page:
            return True  # In placeholder mode, always healthy
            
        try:
            # Try to evaluate a simple script to check health
            result = await asyncio.wait_for(
                self.page.evaluate("() => document.readyState"), 
                timeout=2.0
            )
            return result in ["complete", "interactive", "loading"]
        except Exception as e:
            log.error(f"Health check failed: {e}")
            return False
    
    async def hover(self, selector: str, timeout: int = 10000) -> Dict[str, Any]:
        """Hover over element"""
        if not PLAYWRIGHT_AVAILABLE or not self.page:
            return {
                "success": True,
                "selector": selector,
                "message": f"Hovered over {selector} (placeholder)"
            }
            
        try:
            await self.page.hover(selector, timeout=timeout)
            return {
                "success": True,
                "selector": selector,
                "message": f"Successfully hovered over {selector}"
            }
        except Exception as e:
            log.error(f"Hover failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "selector": selector
            }
    
    async def press_key(self, key: str) -> Dict[str, Any]:
        """Press keyboard key"""
        if not PLAYWRIGHT_AVAILABLE or not self.page:
            return {
                "success": True,
                "key": key,
                "message": f"Pressed key {key} (placeholder)"
            }
            
        try:
            await self.page.keyboard.press(key)
            return {
                "success": True,
                "key": key,
                "message": f"Successfully pressed key {key}"
            }
        except Exception as e:
            log.error(f"Press key failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "key": key
            }


# Global instance to be used by automation_server.py
_browser_use_adapter: Optional[BrowserUseAdapter] = None


async def get_browser_adapter() -> BrowserUseAdapter:
    """Get or create the global browser adapter instance"""
    global _browser_use_adapter
    
    if _browser_use_adapter is None:
        _browser_use_adapter = BrowserUseAdapter()
        await _browser_use_adapter.initialize()
    
    return _browser_use_adapter


async def close_browser_adapter():
    """Close the global browser adapter instance"""
    global _browser_use_adapter
    
    if _browser_use_adapter:
        await _browser_use_adapter.close()
        _browser_use_adapter = None