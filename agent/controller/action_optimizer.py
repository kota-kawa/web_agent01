from __future__ import annotations

import copy
import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from agent.browser.dom import DOMElementNode

_WHITESPACE_RE = re.compile(r"\s+")
_ROLE_PATTERN = re.compile(r"^role=([\w-]+)(?:\[name=['\"](.+?)['\"]])?$", re.I)


def _normalize_text(value: str | None) -> str:
    """Normalize text for fuzzy comparisons."""

    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.strip()
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    return normalized.lower()


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(value)


def _role_bonus(role: str, tag: str, action: str) -> float:
    role = (role or "").lower()
    tag = (tag or "").lower()
    bonus = 0.0
    if action in {"click", "hover", "extract_text"}:
        if role in {"button", "link", "menuitem", "option", "radio", "checkbox", "tab"}:
            bonus += 0.35
        if tag in {"button", "a", "option", "input"}:
            bonus += 0.2
    if action in {"type", "search", "submit_form"}:
        if role in {"textbox", "combobox", "searchbox", "spinbutton"}:
            bonus += 0.5
        if tag in {"input", "textarea"}:
            bonus += 0.35
    if action in {"select_option"}:
        if role in {"listbox", "combobox"} or tag == "select":
            bonus += 0.5
    return bonus


@dataclass(slots=True)
class IndexResolution:
    index: int
    source: str
    match: str
    confidence: float


class CatalogLookup:
    """Heuristic lookup helper based on the element catalog."""

    def __init__(self, catalog: Dict[str, Any] | None) -> None:
        self.entries_by_index: Dict[int, Dict[str, Any]] = {}
        self.selector_to_index: Dict[str, int] = {}
        self.text_entries: List[Tuple[int, Dict[str, Any], List[str]]] = []
        if not isinstance(catalog, dict):
            return

        entries = catalog.get("full") or []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            idx = entry.get("index")
            if not isinstance(idx, int):
                continue
            self.entries_by_index[idx] = entry

            selectors = entry.get("robust_selectors") or []
            for raw in selectors:
                if not isinstance(raw, str):
                    continue
                trimmed = raw.strip()
                if not trimmed:
                    continue
                self.selector_to_index.setdefault(trimmed, idx)
                self.selector_to_index.setdefault(trimmed.lower(), idx)

            texts: List[str] = []
            for key in ("primary_label", "secondary_label", "section_hint", "state_hint", "href_short"):
                value = entry.get(key)
                if isinstance(value, str) and value.strip():
                    texts.append(value)
            nearest = entry.get("nearest_texts") or []
            for value in nearest:
                if isinstance(value, str) and value.strip():
                    texts.append(value)

            primary = entry.get("primary_label") or ""
            secondary = entry.get("secondary_label") or ""
            if primary and secondary:
                texts.append(f"{primary} {secondary}")

            for raw in selectors:
                if not isinstance(raw, str):
                    continue
                lower = raw.lower().strip()
                if lower.startswith("text="):
                    texts.append(raw[5:])
                else:
                    match = _ROLE_PATTERN.match(raw)
                    if match and match.group(2):
                        texts.append(match.group(2))

            self.text_entries.append((idx, entry, texts))

    def has_entries(self) -> bool:
        return bool(self.entries_by_index)

    def match_selector(self, selector: str | None) -> Optional[IndexResolution]:
        if not selector:
            return None
        trimmed = selector.strip()
        if not trimmed:
            return None
        candidate = self.selector_to_index.get(trimmed)
        if candidate is None:
            candidate = self.selector_to_index.get(trimmed.lower())
        if candidate is None:
            return None
        return IndexResolution(index=candidate, source="catalog_selector", match=trimmed, confidence=2.5)

    def match_text(self, text: str | None, action: str) -> Optional[IndexResolution]:
        normalized = _normalize_text(text)
        if not normalized:
            return None

        best: Optional[Tuple[float, int, str, Dict[str, Any]]] = None
        second_score = 0.0
        for idx, entry, candidates in self.text_entries:
            role = (entry.get("role") or "").lower()
            tag = (entry.get("tag") or "").lower()
            for candidate in candidates:
                candidate_norm = _normalize_text(candidate)
                if not candidate_norm:
                    continue
                score = 0.0
                if normalized == candidate_norm:
                    score += 2.2
                elif normalized in candidate_norm or candidate_norm in normalized:
                    score += 1.1
                ratio = SequenceMatcher(None, normalized, candidate_norm).ratio()
                score += ratio
                score += _role_bonus(role, tag, action)

                if best is None or score > best[0]:
                    second_score = best[0] if best else 0.0
                    best = (score, idx, candidate, entry)
                elif score > second_score:
                    second_score = score

        if not best:
            return None
        score, idx, candidate, entry = best
        if score < 1.35:
            return None
        if second_score and (score - second_score) < 0.25:
            return None
        return IndexResolution(index=idx, source="catalog_text", match=candidate, confidence=score)


@dataclass(slots=True)
class DOMNodeInfo:
    index: int
    tag: str
    role: str
    texts: List[str]


class DOMLookup:
    """Extract interactive node metadata from the DOM snapshot."""

    def __init__(self, dom: DOMElementNode | Sequence[DOMElementNode] | None) -> None:
        self.nodes: Dict[int, DOMNodeInfo] = {}
        self.text_to_indices: Dict[str, List[int]] = {}
        if dom is None:
            return

        if isinstance(dom, DOMElementNode):
            roots: Iterable[DOMElementNode] = [dom]
        else:
            roots = [node for node in dom if isinstance(node, DOMElementNode)]

        for root in roots:
            self._collect(root)

    def _collect(self, node: DOMElementNode) -> None:
        if node.highlightIndex is not None:
            info = self._build_info(node)
            self.nodes[info.index] = info
            for text in info.texts:
                normalized = _normalize_text(text)
                if not normalized:
                    continue
                bucket = self.text_to_indices.setdefault(normalized, [])
                if info.index not in bucket:
                    bucket.append(info.index)
        for child in getattr(node, "children", []) or []:
            if isinstance(child, DOMElementNode):
                self._collect(child)

    def _build_info(self, node: DOMElementNode) -> DOMNodeInfo:
        attributes = {k.lower(): v for k, v in (getattr(node, "attributes", {}) or {}).items() if isinstance(v, str)}
        role = attributes.get("role", "")
        tag = (getattr(node, "tagName", "") or "").lower()

        texts: List[str] = []
        node_text = self._collect_text(node)
        if node_text:
            texts.append(node_text)
        for key in ("aria-label", "aria_label", "placeholder", "alt", "title", "value", "name", "id"):
            value = attributes.get(key)
            if value and value not in texts:
                texts.append(value)
        for annotation in getattr(node, "annotations", []) or []:
            if annotation and annotation not in texts:
                texts.append(annotation)
        return DOMNodeInfo(index=node.highlightIndex, tag=tag, role=role, texts=texts)

    def _collect_text(self, node: DOMElementNode) -> str:
        texts: List[str] = []

        if node.text:
            texts.append(str(node.text))
        for child in getattr(node, "children", []) or []:
            if getattr(child, "tagName", "") == "#text":
                if child.text:
                    texts.append(str(child.text))
            elif isinstance(child, DOMElementNode):
                nested = self._collect_text(child)
                if nested:
                    texts.append(nested)
        combined = " ".join(texts).strip()
        return _WHITESPACE_RE.sub(" ", combined) if combined else ""

    def has_entries(self) -> bool:
        return bool(self.nodes)

    def match_text(self, text: str | None, action: str) -> Optional[IndexResolution]:
        normalized = _normalize_text(text)
        if not normalized:
            return None

        direct = self.text_to_indices.get(normalized)
        if direct:
            if len(direct) == 1:
                idx = direct[0]
                return IndexResolution(index=idx, source="dom_text", match=text or "", confidence=2.3)

        best: Optional[Tuple[float, DOMNodeInfo, str]] = None
        second_score = 0.0
        for info in self.nodes.values():
            for candidate in info.texts:
                candidate_norm = _normalize_text(candidate)
                if not candidate_norm:
                    continue
                score = 0.0
                if normalized == candidate_norm:
                    score += 2.0
                elif normalized in candidate_norm or candidate_norm in normalized:
                    score += 1.0
                ratio = SequenceMatcher(None, normalized, candidate_norm).ratio()
                score += ratio
                score += _role_bonus(info.role, info.tag, action)

                if best is None or score > best[0]:
                    second_score = best[0] if best else 0.0
                    best = (score, info, candidate)
                elif score > second_score:
                    second_score = score

        if not best:
            return None
        score, info, candidate = best
        if score < 1.25:
            return None
        if second_score and (score - second_score) < 0.2:
            return None
        return IndexResolution(index=info.index, source="dom_text", match=candidate, confidence=score)


def _extract_index(target: Any) -> Optional[int]:
    if target is None:
        return None
    if isinstance(target, dict):
        if "index" in target:
            try:
                return int(target["index"])
            except (TypeError, ValueError):
                return None
        nested = target.get("selector") or target.get("target") or target.get("value")
        if nested is not None:
            return _extract_index(nested)
    if isinstance(target, list):
        for item in target:
            result = _extract_index(item)
            if result is not None:
                return result
        return None
    if isinstance(target, str):
        text = target.strip()
        if text.lower().startswith("index="):
            try:
                return int(text.split("=", 1)[1])
            except ValueError:
                return None
    return None


def _iter_selector_strings(target: Any) -> Iterable[str]:
    if target is None:
        return
    if isinstance(target, str):
        lowered = target.lower().strip()
        if lowered.startswith(("css=", "xpath=", "role=", "aria-label=", "aria_label=")):
            yield target.strip()
        elif lowered.startswith("index="):
            yield target.strip()
        elif lowered.startswith("text="):
            return
        else:
            return
    elif isinstance(target, dict):
        if "index" in target:
            yield f"index={target['index']}"
        if "css" in target and target["css"]:
            yield f"css={target['css']}"
        if "xpath" in target and target["xpath"]:
            yield f"xpath={target['xpath']}"
        if "role" in target:
            role_value = str(target["role"]).strip()
            name_value = target.get("name") or target.get("text")
            if name_value:
                yield f"role={role_value}[name=\"{str(name_value).strip()}\"]"
            yield f"role={role_value}"
        if "aria-label" in target:
            yield f"aria-label={target['aria-label']}"
        if "aria_label" in target:
            yield f"aria-label={target['aria_label']}"
        for key in ("selector", "target", "value"):
            if key in target and target[key] is not None:
                yield from _iter_selector_strings(target[key])
    elif isinstance(target, list):
        for item in target:
            yield from _iter_selector_strings(item)


def _iter_text_strings(target: Any) -> Iterable[str]:
    if target is None:
        return
    if isinstance(target, str):
        lowered = target.lower().strip()
        if lowered.startswith("text="):
            yield target[5:]
        elif lowered.startswith(("css=", "xpath=", "role=", "aria-label=", "aria_label=", "index=")):
            return
        else:
            yield target
    elif isinstance(target, dict):
        for key in ("text", "label", "name", "aria-label", "aria_label", "placeholder", "alt", "title"):
            if key in target and isinstance(target[key], str):
                yield target[key]
        for key in ("selector", "target", "value"):
            if key in target and target[key] is not None:
                yield from _iter_text_strings(target[key])
    elif isinstance(target, list):
        for item in target:
            yield from _iter_text_strings(item)


def _resolve_index(
    target: Any,
    action: str,
    catalog_lookup: CatalogLookup,
    dom_lookup: DOMLookup,
) -> Optional[IndexResolution]:
    existing = _extract_index(target)
    if existing is not None:
        return IndexResolution(index=existing, source="existing", match=f"index={existing}", confidence=3.0)

    for selector in _iter_selector_strings(target):
        resolution = catalog_lookup.match_selector(selector)
        if resolution:
            return resolution

    for text in _iter_text_strings(target):
        resolution = catalog_lookup.match_text(text, action)
        if resolution:
            return resolution
        resolution = dom_lookup.match_text(text, action)
        if resolution:
            return resolution
    return None


def _convert_target_value(
    container: Dict[str, Any],
    field: str,
    action: str,
    catalog_lookup: CatalogLookup,
    dom_lookup: DOMLookup,
    extra_text: Optional[str] = None,
) -> Optional[str]:
    if field not in container:
        return None
    original = container[field]
    resolution = _resolve_index(original, action, catalog_lookup, dom_lookup)
    if not resolution and extra_text:
        resolution = catalog_lookup.match_text(extra_text, action) or dom_lookup.match_text(extra_text, action)
    if not resolution:
        return None

    new_value: Any = {"index": resolution.index}
    if isinstance(original, dict) and original.get("index") == resolution.index:
        new_value = {"index": int(original["index"])}
    container[field] = new_value
    description = _stringify(original)
    return f"{action}.{field} -> index={resolution.index} ({resolution.source}:{resolution.match}) from {description}"


def _optimize_single_action(
    action: Dict[str, Any],
    catalog_lookup: CatalogLookup,
    dom_lookup: DOMLookup,
) -> Tuple[Dict[str, Any], List[str]]:
    optimized = copy.deepcopy(action)
    notes: List[str] = []
    name = str(optimized.get("action", "")).lower()

    if name == "click_text":
        text_value = optimized.get("text") or optimized.get("target")
        resolution = _resolve_index(text_value, "click", catalog_lookup, dom_lookup)
        if resolution:
            optimized["action"] = "click"
            optimized["target"] = {"index": resolution.index}
            optimized.pop("text", None)
            notes.append(f"click_text -> click index={resolution.index} ({resolution.source}:{resolution.match})")
        else:
            if "target" not in optimized and isinstance(text_value, str):
                optimized["target"] = text_value
        return optimized, notes

    if name in {"click", "hover", "type", "select_option", "extract_text"}:
        note = _convert_target_value(optimized, "target", name, catalog_lookup, dom_lookup)
        if note:
            notes.append(note)

    if name == "wait_for_selector":
        note = _convert_target_value(optimized, "target", name, catalog_lookup, dom_lookup)
        if note:
            notes.append(note)

    if name == "wait":
        until = optimized.get("until") or optimized.get("for")
        if isinstance(until, str) and until.lower() == "selector":
            target_field = "target" if "target" in optimized else "value"
            note = _convert_target_value(optimized, target_field, name, catalog_lookup, dom_lookup)
            if note:
                notes.append(note)

    if name == "search":
        note = _convert_target_value(optimized, "input", name, catalog_lookup, dom_lookup)
        if note:
            notes.append(note)
        note = _convert_target_value(optimized, "submit_selector", name, catalog_lookup, dom_lookup)
        if note:
            notes.append(note)

    if name == "submit_form":
        fields = optimized.get("fields")
        if isinstance(fields, list):
            for idx, field in enumerate(fields):
                if not isinstance(field, dict):
                    continue
                note = _convert_target_value(field, "selector", name, catalog_lookup, dom_lookup)
                if note:
                    notes.append(f"submit_form.fields[{idx}] selector -> {note.split('->')[-1].strip()}")
        note = _convert_target_value(optimized, "submit_selector", name, catalog_lookup, dom_lookup)
        if note:
            notes.append(note)

    if name == "assert":
        note = _convert_target_value(optimized, "selector", name, catalog_lookup, dom_lookup)
        if note:
            notes.append(note)

    if name == "scroll":
        note = _convert_target_value(optimized, "target", name, catalog_lookup, dom_lookup)
        if note:
            notes.append(note)

    return optimized, notes


def optimize_actions(
    actions: Sequence[Dict[str, Any]],
    catalog: Dict[str, Any] | None,
    dom: DOMElementNode | Sequence[DOMElementNode] | None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Return a deep-copied list of actions with index-based targeting when possible."""

    if not actions:
        return [], []

    catalog_lookup = CatalogLookup(catalog)
    dom_lookup = DOMLookup(dom)

    if not catalog_lookup.has_entries() and not dom_lookup.has_entries():
        return [copy.deepcopy(a) for a in actions], []

    optimized_actions: List[Dict[str, Any]] = []
    notes: List[str] = []
    for action in actions:
        if not isinstance(action, dict):
            optimized_actions.append(copy.deepcopy(action))
            continue
        optimized, action_notes = _optimize_single_action(action, catalog_lookup, dom_lookup)
        optimized_actions.append(optimized)
        notes.extend(action_notes)

    return optimized_actions, notes
