"""Composite selector resolution with scoring and stable node IDs."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from playwright.async_api import ElementHandle, Frame, Locator, Page

from automation.dsl import Selector
from automation.dsl.resolution import ResolutionAttempt, ResolvedNode

MAX_CANDIDATES_PER_STRATEGY = 6
TEXT_DIGEST_LENGTH = 80


CSS_PATH_SCRIPT = """
(element) => {
  if (!(element instanceof Element)) return '';
  const path = [];
  while (element && element.nodeType === Node.ELEMENT_NODE) {
    let selector = element.tagName.toLowerCase();
    if (element.id) {
      selector += '#' + element.id;
      path.unshift(selector);
      break;
    } else {
      let index = 1;
      let sibling = element.previousElementSibling;
      while (sibling) {
        if (sibling.tagName === element.tagName) {
          index += 1;
        }
        sibling = sibling.previousElementSibling;
      }
      selector += `:nth-of-type(${index})`;
      path.unshift(selector);
      element = element.parentElement;
    }
  }
  return path.join(' > ');
}
"""

TEXT_SUMMARY_SCRIPT = """
(element, maxLen) => {
  const text = (element.innerText || element.textContent || '').trim();
  if (!text) return '';
  return text.length > maxLen ? text.slice(0, maxLen) : text;
}
"""

METRICS_SCRIPT = """
(element) => {
  const rect = element.getBoundingClientRect();
  const style = window.getComputedStyle(element);
  const visible = rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  const clickableTags = ['a','button','input','select','textarea'];
  const role = element.getAttribute('role') || '';
  const ariaLabel = element.getAttribute('aria-label') || '';
  const tag = element.tagName.toLowerCase();
  const tabIndex = element.tabIndex;
  const clickable = clickableTags.includes(tag) || role in {button:1, link:1} || tabIndex >= 0 || element.isContentEditable;
  const inViewport = rect.bottom > 0 && rect.top < (window.innerHeight || document.documentElement.clientHeight);
  return {
    rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
    visible,
    clickable,
    inViewport,
    ariaLabel,
    role,
    tag,
  };
}
"""

RESOLVE_PATH_SCRIPT = """
(path) => {
  if (!path) return null;
  const segments = path.split('>');
  let current = document;
  for (const raw of segments) {
    const segment = raw.trim();
    if (!segment) continue;
    const [selector, nthPart] = segment.split(':nth-of-type');
    let baseSelector = selector.trim();
    let target = null;
    if (baseSelector.includes('#')) {
      target = document.querySelector(baseSelector);
      if (!target) return null;
    } else {
      const parent = current.querySelectorAll(baseSelector.split(' ').slice(-1)[0]);
      const nth = nthPart ? parseInt(nthPart.slice(1, -1), 10) : 1;
      let count = 0;
      for (const node of parent) {
        if (node.matches(baseSelector)) {
          count += 1;
          if (count === nth) {
            target = node;
            break;
          }
        }
      }
    }
    if (!target) {
      target = current.querySelector(segment);
    }
    if (!target) return null;
    current = target;
  }
  return current;
}
"""


@dataclass
class StableNode:
    dom_path: str
    text_digest: str


class StableNodeStore:
    """Keeps track of previously resolved nodes by a stable identifier."""

    def __init__(self) -> None:
        self._nodes: Dict[str, StableNode] = {}

    def make_id(self, dom_path: str, text_digest: str) -> str:
        raw = f"{dom_path}|{text_digest}"
        stable = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
        self._nodes[stable] = StableNode(dom_path=dom_path, text_digest=text_digest)
        return stable

    def update(self, stable_id: str, dom_path: str, text_digest: str) -> None:
        self._nodes[stable_id] = StableNode(dom_path=dom_path, text_digest=text_digest)

    def get(self, stable_id: str) -> Optional[StableNode]:
        return self._nodes.get(stable_id)


class SelectorResolver:
    """Resolve composite selectors using a scoring model and stable IDs."""

    def __init__(self, page: Page | Frame, store: Optional[StableNodeStore] = None) -> None:
        self.page = page
        self.store = store or StableNodeStore()

    async def resolve(self, selector: Selector) -> ResolvedNode:
        if selector.stable_id:
            resolved = await self._resolve_by_stable_id(selector)
            if resolved:
                return resolved

        ref_metrics = None
        if selector.near_text:
            ref_metrics = await self._reference_metrics(selector.near_text)

        attempts: List[ResolutionAttempt] = []
        for strategy in selector.effective_priority():
            collector = getattr(self, f"_collect_{strategy}", None)
            if not collector:
                continue
            attempts.extend(await collector(selector, ref_metrics=ref_metrics))
            if attempts:
                break  # respect priority order

        if not attempts:
            # fall back to generic search if no strategy succeeded
            attempts.extend(await self._collect_generic(selector, ref_metrics=ref_metrics))

        if not attempts:
            raise LookupError(f"Selector could not be resolved: {selector}")

        best = max(attempts, key=lambda attempt: attempt.score)
        stable_id = selector.stable_id or self.store.make_id(best.dom_path, best.text_digest)
        self.store.update(stable_id, best.dom_path, best.text_digest)
        return best.to_resolved(stable_id)

    async def _resolve_by_stable_id(self, selector: Selector) -> Optional[ResolvedNode]:
        info = self.store.get(selector.stable_id) if selector.stable_id else None
        if not info:
            return None
        handle_js = await self.page.evaluate_handle(RESOLVE_PATH_SCRIPT, info.dom_path)
        element = handle_js.as_element() if handle_js else None
        if element is None:
            return None
        locator = element.locator(":scope")
        attempt = await self._build_attempt(locator, element, selector, strategy="stable")
        self.store.update(selector.stable_id, attempt.dom_path, attempt.text_digest)
        return attempt.to_resolved(selector.stable_id)

    async def _collect_css(
        self, selector: Selector, *, ref_metrics: Optional[Dict[str, Any]] = None
    ) -> List[ResolutionAttempt]:
        if not selector.css:
            return []
        locator = self.page.locator(selector.css)
        return await self._collect_from_locator(locator, selector, "css", ref_metrics=ref_metrics)

    async def _collect_xpath(
        self, selector: Selector, *, ref_metrics: Optional[Dict[str, Any]] = None
    ) -> List[ResolutionAttempt]:
        if not selector.xpath:
            return []
        locator = self.page.locator(f"xpath={selector.xpath}")
        return await self._collect_from_locator(locator, selector, "xpath", ref_metrics=ref_metrics)

    async def _collect_text(
        self, selector: Selector, *, ref_metrics: Optional[Dict[str, Any]] = None
    ) -> List[ResolutionAttempt]:
        if not selector.text:
            return []
        locator = self.page.get_by_text(selector.text, exact=False)
        return await self._collect_from_locator(locator, selector, "text", ref_metrics=ref_metrics)

    async def _collect_role(
        self, selector: Selector, *, ref_metrics: Optional[Dict[str, Any]] = None
    ) -> List[ResolutionAttempt]:
        if not selector.role:
            return []
        locator = self.page.get_by_role(selector.role, name=selector.text or selector.aria_label or None, exact=False)
        return await self._collect_from_locator(locator, selector, "role", ref_metrics=ref_metrics)

    async def _collect_aria_label(
        self, selector: Selector, *, ref_metrics: Optional[Dict[str, Any]] = None
    ) -> List[ResolutionAttempt]:
        if not selector.aria_label:
            return []
        locator = self.page.get_by_label(selector.aria_label, exact=False)
        return await self._collect_from_locator(locator, selector, "aria_label", ref_metrics=ref_metrics)

    async def _collect_index(self, selector: Selector) -> List[ResolutionAttempt]:
        # Index acts as a filter on previous results; handled in scoring.
        return []

    async def _collect_generic(
        self, selector: Selector, *, ref_metrics: Optional[Dict[str, Any]] = None
    ) -> List[ResolutionAttempt]:
        attempts: List[ResolutionAttempt] = []
        if selector.css:
            attempts.extend(await self._collect_css(selector, ref_metrics=ref_metrics))
        if selector.text:
            attempts.extend(await self._collect_text(selector, ref_metrics=ref_metrics))
        if selector.role:
            attempts.extend(await self._collect_role(selector, ref_metrics=ref_metrics))
        return attempts

    async def _collect_from_locator(
        self,
        locator: Locator,
        selector: Selector,
        strategy: str,
        *,
        ref_metrics: Optional[Dict[str, Any]] = None,
    ) -> List[ResolutionAttempt]:
        attempts: List[ResolutionAttempt] = []
        count = await locator.count()
        for index in range(min(count, MAX_CANDIDATES_PER_STRATEGY)):
            candidate_locator = locator.nth(index)
            handle = await candidate_locator.element_handle()
            if handle is None:
                continue
            attempts.append(
                await self._build_attempt(
                    candidate_locator,
                    handle,
                    selector,
                    strategy=strategy,
                    ordinal=index,
                    ref_metrics=ref_metrics,
                )
            )
        return attempts

    async def _build_attempt(
        self,
        locator: Locator,
        element: ElementHandle,
        selector: Selector,
        *,
        strategy: str,
        ordinal: Optional[int] = None,
        ref_metrics: Optional[Dict[str, Any]] = None,
    ) -> ResolutionAttempt:
        dom_path = await element.evaluate(CSS_PATH_SCRIPT)
        text_summary = await element.evaluate(TEXT_SUMMARY_SCRIPT, TEXT_DIGEST_LENGTH)
        metrics = await element.evaluate(METRICS_SCRIPT)
        text_digest = text_summary or metrics.get("ariaLabel", "") or metrics.get("role", "")
        score = self._score_candidate(selector, metrics, text_summary, ordinal=ordinal, ref_metrics=ref_metrics)
        metadata = {
            "strategy": strategy,
            "metrics": metrics,
            "ordinal": ordinal,
        }
        return ResolutionAttempt(
            selector=selector,
            locator=locator,
            element=element,
            dom_path=dom_path,
            text_digest=text_summary,
            strategy=strategy,
            score=score,
            metadata=metadata,
        )

    async def _reference_metrics(self, text: str) -> Optional[Dict[str, Any]]:
        locator = self.page.get_by_text(text, exact=False)
        handle = await locator.element_handle()
        if handle is None:
            return None
        metrics = await handle.evaluate(METRICS_SCRIPT)
        metrics["rect"] = metrics.get("rect")
        return metrics

    def _score_candidate(
        self,
        selector: Selector,
        metrics: Dict[str, Any],
        text_summary: str,
        *,
        ordinal: Optional[int] = None,
        ref_metrics: Optional[Dict[str, Any]] = None,
    ) -> float:
        score = 0.0
        if metrics.get("visible"):
            score += 2.0
        if metrics.get("clickable"):
            score += 1.0
        if metrics.get("inViewport"):
            score += 0.5

        text_target = selector.text or ""
        if text_target:
            ratio = SequenceMatcher(None, text_target.lower(), text_summary.lower()).ratio() if text_summary else 0
            score += ratio * 2.0

        aria_target = selector.aria_label or ""
        aria_value = metrics.get("ariaLabel", "")
        if aria_target:
            ratio = SequenceMatcher(None, aria_target.lower(), aria_value.lower()).ratio() if aria_value else 0
            score += ratio * 1.5

        role_target = selector.role or ""
        if role_target:
            score += 1.0 if metrics.get("role", "").lower() == role_target.lower() else 0

        if ordinal is not None and selector.index is not None:
            difference = abs(selector.index - ordinal)
            score -= min(difference * 0.5, 2.0)

        if ref_metrics and metrics.get("rect"):
            score += self._proximity_bonus(metrics["rect"], ref_metrics.get("rect"))

        return score

    def _proximity_bonus(self, rect: Dict[str, float], ref_rect: Optional[Dict[str, float]]) -> float:
        if not ref_rect:
            return 0.0
        cx = rect["x"] + rect["width"] / 2
        cy = rect["y"] + rect["height"] / 2
        rcx = ref_rect["x"] + ref_rect["width"] / 2
        rcy = ref_rect["y"] + ref_rect["height"] / 2
        distance = math.dist((cx, cy), (rcx, rcy))
        return max(0.0, 1.5 - min(distance / 400, 1.5))
