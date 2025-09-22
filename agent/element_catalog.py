"""Utilities for fetching and formatting the browser element catalog."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Iterable, List, Optional

from agent.browser import vnc

log = logging.getLogger(__name__)
INDEX_MODE_ENABLED = os.getenv("INDEX_MODE", "true").lower() == "true"

_cached_catalog: Optional[Dict[str, Any]] = None
_catalog_dirty: bool = False
_last_prompt_version: Optional[str] = None
_pending_prompt_messages: List[str] = []


def _normalize_catalog(raw: Dict[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    metadata = raw.get("metadata") or {}
    catalog_version = raw.get("catalog_version") or metadata.get("catalog_version")
    return {
        "abbreviated": raw.get("abbreviated", []),
        "full": raw.get("full", []),
        "metadata": metadata,
        "catalog_version": catalog_version,
        "index_mode_enabled": raw.get("index_mode_enabled", INDEX_MODE_ENABLED),
        "error": raw.get("error"),
    }


def is_enabled() -> bool:
    """Return True when index-based catalog mode is enabled."""
    return INDEX_MODE_ENABLED


def reset_cache() -> None:
    global _cached_catalog, _catalog_dirty
    _cached_catalog = None
    _catalog_dirty = True


def get_catalog(refresh: bool = False) -> Dict[str, Any]:
    """Return the element catalog, optionally forcing a refresh from the browser."""
    global _cached_catalog, _catalog_dirty

    if not INDEX_MODE_ENABLED:
        return {
            "abbreviated": [],
            "full": [],
            "metadata": {},
            "catalog_version": None,
            "index_mode_enabled": False,
            "error": None,
        }

    if refresh or _cached_catalog is None or _catalog_dirty:
        try:
            force_refresh = refresh or _catalog_dirty
            raw = vnc.get_element_catalog(refresh=force_refresh)
        except Exception as exc:  # pragma: no cover - network failure
            log.error("Failed to fetch element catalog: %s", exc)
            return {
                "abbreviated": [],
                "full": [],
                "metadata": {},
                "catalog_version": None,
                "index_mode_enabled": True,
                "error": {"message": str(exc)},
            }
        _cached_catalog = _normalize_catalog(raw)
        _catalog_dirty = False
    return _cached_catalog or {
        "abbreviated": [],
        "full": [],
        "metadata": {},
        "catalog_version": None,
        "index_mode_enabled": True,
        "error": None,
    }


def get_expected_version() -> Optional[str]:
    """Return the catalog version to send with executor requests."""
    catalog = get_catalog(refresh=False)
    return catalog.get("catalog_version")


def mark_catalog_dirty(reason: Optional[str] = None) -> None:
    """Invalidate the cached catalog so the next prompt fetches a fresh copy."""

    if not INDEX_MODE_ENABLED:
        return

    global _catalog_dirty, _cached_catalog
    if reason:
        log.debug("Marking catalog dirty: %s", reason)
    _catalog_dirty = True
    _cached_catalog = None


def should_refresh_for_prompt() -> bool:
    """Return True when the prompt should force a catalog refresh."""

    return INDEX_MODE_ENABLED and _catalog_dirty


def record_prompt_version(version: Optional[str]) -> None:
    """Remember the catalog version that the latest prompt was based on."""

    global _last_prompt_version
    _last_prompt_version = version


def get_last_prompt_version() -> Optional[str]:
    """Return the catalog version that was last provided to the LLM prompt."""

    return _last_prompt_version


def _queue_prompt_message(message: str) -> None:
    if not message:
        return
    if message in _pending_prompt_messages:
        return
    _pending_prompt_messages.append(message)


def consume_pending_prompt_messages() -> List[str]:
    """Return and clear any system messages that should reach the planner."""

    global _pending_prompt_messages
    messages = list(_pending_prompt_messages)
    _pending_prompt_messages = []
    return messages


def actions_use_catalog_indices(actions: Iterable[Dict[str, Any]]) -> bool:
    for action in actions or []:
        if not isinstance(action, dict):
            continue
        target = action.get("target")
        if isinstance(target, str) and target.strip().lower().startswith("index="):
            return True
        if isinstance(target, list):
            for item in target:
                if isinstance(item, str) and item.strip().lower().startswith("index="):
                    return True
        value = action.get("value")
        if isinstance(value, str) and value.strip().lower().startswith("index="):
            return True
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip().lower().startswith("index="):
                    return True
    return False


def handle_execution_feedback(actions: Iterable[Dict[str, Any]], result: Dict[str, Any]) -> None:
    """Update catalog bookkeeping based on the last execution result."""

    if not INDEX_MODE_ENABLED:
        return

    warnings = [w for w in (result or {}).get("warnings") or [] if isinstance(w, str)]
    observation = (result or {}).get("observation") or {}

    nav_detected = bool(observation.get("nav_detected"))
    catalog_version = observation.get("catalog_version")

    if nav_detected:
        mark_catalog_dirty("navigation detected by executor")

    uses_refresh_action = any(
        isinstance(action, dict) and action.get("action") == "refresh_catalog" for action in actions or []
    )
    if uses_refresh_action:
        mark_catalog_dirty("refresh_catalog action executed")

    auto_refresh = next((w for w in warnings if "Element catalog auto-refreshed" in w), None)
    if auto_refresh:
        mark_catalog_dirty("executor auto-refreshed catalog")

    mismatch_warning = next((w for w in warnings if "Catalog version still differs" in w), None)
    proceed_warning = next((w for w in warnings if "Proceeding without a refreshed catalog" in w), None)

    if mismatch_warning or proceed_warning:
        mark_catalog_dirty("executor reported catalog mismatch")
        _queue_prompt_message(
            "CATALOG_OUTDATED: Executor detected catalog version drift. Fetch a fresh catalog and rebuild the plan before using index-based targets."
        )

    # If the executor produced a new catalog version, remember it for diagnostics.
    if catalog_version and catalog_version != _last_prompt_version:
        log.debug(
            "Executor observed catalog version %s (planner used %s)",
            catalog_version,
            _last_prompt_version,
        )

    if nav_detected and not mismatch_warning:
        _queue_prompt_message(
            "CATALOG_OUTDATED: Navigation detected after the last execution. Refresh the element catalog before relying on index selectors."
        )

def format_catalog_for_prompt(catalog: Dict[str, Any]) -> str:
    """Return a human-friendly string representation of the abbreviated catalog."""
    entries: List[Dict[str, Any]] = catalog.get("abbreviated", [])
    if not entries:
        return "(No interactive elements detected in the current viewport)"

    lines: List[str] = []
    for item in entries:
        index = item.get("index")
        role = item.get("role") or item.get("tag") or "element"
        primary = item.get("primary_label") or ""
        secondary = item.get("secondary_label") or ""
        section = item.get("section_hint") or ""
        state = item.get("state_hint") or ""
        href = item.get("href_short") or ""

        label_parts = []
        if primary:
            label_parts.append(primary)
        if secondary and secondary != primary:
            label_parts.append(secondary)
        label_text = " — ".join(label_parts) if label_parts else "(no label)"

        hint_parts = []
        if section:
            hint_parts.append(f"section: {section}")
        if state:
            hint_parts.append(state)
        if href:
            hint_parts.append(href)
        hints = f" ({'; '.join(hint_parts)})" if hint_parts else ""

        lines.append(f"[{index}] {role}: {label_text}{hints}")

    return "\n".join(lines)


def get_catalog_for_prompt(refresh: bool = False) -> Dict[str, Any]:
    """Convenience helper returning both catalog data and formatted prompt text."""
    catalog = get_catalog(refresh=refresh)
    prompt_text = format_catalog_for_prompt(catalog)
    return {"catalog": catalog, "prompt_text": prompt_text}
