"""Utilities for fetching and formatting the browser element catalog."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from agent.browser import vnc

log = logging.getLogger(__name__)
INDEX_MODE_ENABLED = os.getenv("INDEX_MODE", "true").lower() == "true"

_cached_catalog: Optional[Dict[str, Any]] = None
_last_observed_version: Optional[str] = None


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
    global _cached_catalog, _last_observed_version
    _cached_catalog = None
    _last_observed_version = None


def update_cache_from_signature(signature: Optional[Dict[str, Any]]) -> None:
    """Update cached catalog metadata after observing a new catalog signature."""

    if not INDEX_MODE_ENABLED:
        return

    if not isinstance(signature, dict):
        return

    metadata = signature.get("metadata") or {}
    observed_version = signature.get("catalog_version") or metadata.get("catalog_version")
    if not observed_version:
        return

    global _cached_catalog, _last_observed_version

    if _last_observed_version == observed_version:
        return

    cached_version = (_cached_catalog or {}).get("catalog_version")
    _last_observed_version = observed_version

    if cached_version == observed_version:
        return

    if _cached_catalog is not None:
        log.debug(
            "Observed new catalog version; invalidating cache (cached=%s observed=%s)",
            cached_version,
            observed_version,
        )

    _cached_catalog = None


def get_catalog(refresh: bool = False) -> Dict[str, Any]:
    """Return the element catalog, optionally forcing a refresh from the browser."""
    global _cached_catalog, _last_observed_version

    if not INDEX_MODE_ENABLED:
        return {
            "abbreviated": [],
            "full": [],
            "metadata": {},
            "catalog_version": None,
            "index_mode_enabled": False,
            "error": None,
        }

    if refresh or _cached_catalog is None:
        try:
            raw = vnc.get_element_catalog(refresh=refresh)
        except Exception as exc:  # pragma: no cover - network failure
            log.error("Failed to fetch element catalog: %s", exc)
            return {
                "abbreviated": [],
                "full": [],
                "metadata": {},
                "catalog_version": _last_observed_version,
                "index_mode_enabled": True,
                "error": {"message": str(exc)},
            }
        _cached_catalog = _normalize_catalog(raw)
        _last_observed_version = _cached_catalog.get("catalog_version")
    return _cached_catalog or {
        "abbreviated": [],
        "full": [],
        "metadata": {},
        "catalog_version": _last_observed_version,
        "index_mode_enabled": True,
        "error": None,
    }


def get_expected_version() -> Optional[str]:
    """Return the catalog version to send with executor requests."""
    catalog = get_catalog(refresh=False)
    return catalog.get("catalog_version")


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
