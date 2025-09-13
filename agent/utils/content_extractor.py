"""
Content extraction using Readability for article/news content.

Provides clean content extraction for news articles and blog posts.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional, Any
from dataclasses import dataclass
import json

try:
    from readabilipy import simple_json
    READABILITY_AVAILABLE = True
except ImportError:
    READABILITY_AVAILABLE = False
    simple_json = None

from playwright.async_api import Page

log = logging.getLogger(__name__)


@dataclass
class ExtractedContent:
    """Extracted content result."""
    title: str
    content: str
    author: str = ""
    date: str = ""
    excerpt: str = ""
    word_count: int = 0
    success: bool = True
    error: Optional[str] = None


class ContentExtractor:
    """Content extractor using Readability."""
    
    def __init__(self):
        self.available = READABILITY_AVAILABLE
        if not self.available:
            log.warning("Readabilipy not available. Install with: pip install readabilipy")
    
    def is_available(self) -> bool:
        """Check if content extraction is available."""
        return self.available
    
    async def extract_content(self, page: Page, url: Optional[str] = None) -> ExtractedContent:
        """Extract clean content from page."""
        if not self.is_available():
            return await self._fallback_extraction(page)
        
        try:
            # Get HTML content
            html_content = await page.content()
            current_url = url or await page.url()
            
            # Use Readability to extract content
            article = simple_json(html_content, use_readability=True)
            
            if not article or "content" not in article:
                log.warning("Readability extraction failed, using fallback")
                return await self._fallback_extraction(page)
            
            # Extract metadata
            title = article.get("title", "").strip()
            content = article.get("content", "").strip()
            
            # Try to extract additional metadata
            author = await self._extract_author(page)
            date = await self._extract_date(page)
            excerpt = await self._extract_excerpt(page, content)
            
            # Count words
            word_count = len(content.split()) if content else 0
            
            return ExtractedContent(
                title=title,
                content=content,
                author=author,
                date=date,
                excerpt=excerpt,
                word_count=word_count,
                success=True
            )
            
        except Exception as e:
            log.error(f"Content extraction failed: {e}")
            return ExtractedContent(
                title="",
                content="",
                success=False,
                error=str(e)
            )
    
    async def _fallback_extraction(self, page: Page) -> ExtractedContent:
        """Fallback content extraction using basic DOM queries."""
        try:
            # Extract title
            title = await page.title() or ""
            
            # Try common article selectors
            content_selectors = [
                "article",
                ".article-content",
                ".post-content", 
                ".entry-content",
                ".content",
                "main",
                "[role='main']"
            ]
            
            content = ""
            for selector in content_selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        text_content = await element.inner_text()
                        if text_content and len(text_content) > len(content):
                            content = text_content
                except Exception:
                    continue
            
            # If no content found, get body text (filtered)
            if not content:
                content = await self._extract_body_text(page)
            
            # Extract author and date
            author = await self._extract_author(page)
            date = await self._extract_date(page)
            excerpt = content[:200] + "..." if len(content) > 200 else content
            
            return ExtractedContent(
                title=title,
                content=content,
                author=author,
                date=date,
                excerpt=excerpt,
                word_count=len(content.split()),
                success=True
            )
            
        except Exception as e:
            log.error(f"Fallback extraction failed: {e}")
            return ExtractedContent(
                title="",
                content="",
                success=False,
                error=str(e)
            )
    
    async def _extract_body_text(self, page: Page) -> str:
        """Extract cleaned body text."""
        try:
            # Get body text but exclude navigation, sidebar, etc.
            script = """
                () => {
                    // Remove unwanted elements
                    const unwanted = document.querySelectorAll('nav, aside, footer, header, .sidebar, .navigation, .menu, script, style, noscript');
                    unwanted.forEach(el => el.remove());
                    
                    // Get main content area
                    const main = document.querySelector('main') || document.querySelector('[role="main"]') || document.body;
                    return main.innerText;
                }
            """
            
            text = await page.evaluate(script)
            return text.strip() if text else ""
            
        except Exception as e:
            log.error(f"Body text extraction failed: {e}")
            return ""
    
    async def _extract_author(self, page: Page) -> str:
        """Extract author information."""
        author_selectors = [
            "[rel='author']",
            ".author",
            ".byline",
            ".post-author",
            ".article-author",
            "[itemprop='author']",
            "meta[name='author']"
        ]
        
        for selector in author_selectors:
            try:
                if selector.startswith("meta"):
                    # Meta tag
                    element = await page.query_selector(selector)
                    if element:
                        author = await element.get_attribute("content")
                        if author:
                            return author.strip()
                else:
                    # Regular element
                    element = await page.query_selector(selector)
                    if element:
                        author = await element.inner_text()
                        if author:
                            return author.strip()
            except Exception:
                continue
        
        return ""
    
    async def _extract_date(self, page: Page) -> str:
        """Extract publication date."""
        date_selectors = [
            "time[datetime]",
            ".date",
            ".published",
            ".post-date",
            ".article-date",
            "[itemprop='datePublished']",
            "meta[property='article:published_time']",
            "meta[name='date']"
        ]
        
        for selector in date_selectors:
            try:
                element = await page.query_selector(selector)
                if element:
                    if selector == "time[datetime]":
                        date = await element.get_attribute("datetime")
                    elif selector.startswith("meta"):
                        date = await element.get_attribute("content")
                    else:
                        date = await element.inner_text()
                    
                    if date:
                        return date.strip()
            except Exception:
                continue
        
        return ""
    
    async def _extract_excerpt(self, page: Page, content: str) -> str:
        """Extract or generate excerpt."""
        # Try meta description first
        try:
            meta_desc = await page.query_selector("meta[name='description']")
            if meta_desc:
                description = await meta_desc.get_attribute("content")
                if description:
                    return description.strip()
        except Exception:
            pass
        
        # Try OpenGraph description
        try:
            og_desc = await page.query_selector("meta[property='og:description']")
            if og_desc:
                description = await og_desc.get_attribute("content")
                if description:
                    return description.strip()
        except Exception:
            pass
        
        # Generate from content
        if content:
            sentences = content.split('. ')
            if len(sentences) >= 2:
                return '. '.join(sentences[:2]) + '.'
            elif content:
                return content[:200] + "..." if len(content) > 200 else content
        
        return ""
    
    async def is_article_page(self, page: Page) -> bool:
        """Detect if current page is likely an article/news page."""
        try:
            # Check for article indicators
            indicators = [
                "article",
                "[role='article']",
                ".article",
                ".post",
                ".entry",
                "time[datetime]",
                "[itemprop='datePublished']",
                ".byline",
                ".author"
            ]
            
            for selector in indicators:
                element = await page.query_selector(selector)
                if element:
                    return True
            
            # Check URL patterns
            url = await page.url()
            article_patterns = [
                "/article/", "/news/", "/blog/", "/post/",
                "/story/", "/press/", "/release/"
            ]
            
            for pattern in article_patterns:
                if pattern in url.lower():
                    return True
            
            return False
            
        except Exception:
            return False


class MockContentExtractor(ContentExtractor):
    """Mock content extractor for testing."""
    
    def __init__(self):
        self.available = True
    
    def is_available(self) -> bool:
        return True
    
    async def extract_content(self, page: Page, url: Optional[str] = None) -> ExtractedContent:
        """Mock content extraction."""
        try:
            title = await page.title() or "Mock Article Title"
            
            return ExtractedContent(
                title=title,
                content="This is mock extracted content for testing purposes. " * 20,
                author="Mock Author",
                date="2024-01-01",
                excerpt="This is a mock excerpt for testing purposes.",
                word_count=100,
                success=True
            )
        except Exception as e:
            return ExtractedContent(
                title="",
                content="",
                success=False,
                error=str(e)
            )