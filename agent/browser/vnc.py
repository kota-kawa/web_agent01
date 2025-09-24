from __future__ import annotations

import logging
import os
import threading
from typing import List

import requests

_DEFAULT_ENDPOINTS: tuple[str, ...] = (
    "http://vnc:7000",
    "http://localhost:7000",
)

_VNC_ENDPOINT: str | None = None
_VNC_LOCK = threading.Lock()

log = logging.getLogger(__name__)


def _normalize_endpoint(value: str | None) -> str:
    if not value:
        return ""
    return value.rstrip("/")


def _candidate_endpoints() -> List[str]:
    candidates: List[str] = []
    env_value = os.getenv("VNC_API")
    if env_value:
        normalised = _normalize_endpoint(env_value)
        if normalised:
            candidates.append(normalised)
    for default in _DEFAULT_ENDPOINTS:
        normalised = _normalize_endpoint(default)
        if normalised and normalised not in candidates:
            candidates.append(normalised)
    return candidates


def _probe_endpoint(endpoint: str, timeout: float = 1.0) -> bool:
    try:
        response = requests.get(f"{endpoint}/healthz", timeout=timeout)
        return response.status_code == 200
    except Exception:
        return False


def get_vnc_api_base(refresh: bool = False) -> str:
    """Return the resolved automation server endpoint."""

    global _VNC_ENDPOINT
    with _VNC_LOCK:
        previous = _VNC_ENDPOINT
        if refresh:
            _VNC_ENDPOINT = None
        if _VNC_ENDPOINT:
            return _VNC_ENDPOINT

        candidates = _candidate_endpoints()
        for endpoint in candidates:
            if _probe_endpoint(endpoint):
                _VNC_ENDPOINT = endpoint
                if previous != _VNC_ENDPOINT:
                    log.info("Resolved automation server endpoint to %s", _VNC_ENDPOINT)
                break
        else:
            # Fall back to the first candidate even if health checks fail so the
            # caller has something to work with.  The health status is exposed
            # via warnings elsewhere.
            fallback = candidates[0] if candidates else "http://vnc:7000"
            if previous != fallback:
                log.warning(
                    "Could not verify automation server connectivity; defaulting to %s",
                    fallback,
                )
            _VNC_ENDPOINT = fallback

        return _VNC_ENDPOINT


def set_vnc_api_base(base_url: str) -> None:
    """Explicitly override the automation server endpoint."""

    normalised = _normalize_endpoint(base_url)
    if not normalised:
        raise ValueError("base_url must be a non-empty string")

    global _VNC_ENDPOINT
    with _VNC_LOCK:
        _VNC_ENDPOINT = normalised
    log.info("VNC automation server endpoint overridden to %s", normalised)


def _vnc_url(path: str) -> str:
    base = get_vnc_api_base()
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}{path}"


def _check_health(timeout: float = 5) -> bool:
    base = get_vnc_api_base()
    try:
        response = requests.get(f"{base}/healthz", timeout=timeout)
        return response.status_code == 200
    except Exception as exc:
        log.warning("Health check failed for %s: %s", base, exc)
        return False


def get_html() -> str:
    """Best-effort retrieval of the current page HTML."""

    try:
        response = requests.get(_vnc_url("/source"), timeout=(5, 30))
        response.raise_for_status()
        return response.text
    except Exception as exc:
        log.error("get_html error: %s", exc)
        return ""


def get_url() -> str:
    """Return the current page URL reported by the automation server."""

    try:
        response = requests.get(_vnc_url("/url"), timeout=(5, 30))
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        log.error("get_url error: %s", exc)
        return ""

    url = data.get("url") if isinstance(data, dict) else None
    return url or ""


__all__ = [
    "get_vnc_api_base",
    "set_vnc_api_base",
    "get_html",
    "get_url",
]
