"""Utilities shared between the web UI and automation stack for browser access."""
from __future__ import annotations

import os
import os
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit


def env_flag(name: str, *, default: bool = False) -> bool:
    """Interpret an environment variable as a boolean flag.

    Accepted truthy values: ``1``, ``true``, ``yes``, ``on`` (case-insensitive).
    Accepted falsy values: ``0``, ``false``, ``no``, ``off`` (case-insensitive).
    Any other value results in *default* being returned.
    """

    value = os.getenv(name)
    if value is None:
        return default

    trimmed = value.strip().lower()
    if not trimmed:
        return default

    if trimmed in {"1", "true", "yes", "on"}:
        return True
    if trimmed in {"0", "false", "no", "off"}:
        return False
    return default


def format_shared_browser_error(reason: str, *, candidates: Iterable[str]) -> str:
    """Return a human readable error message for shared browser failures."""

    candidate_list = [candidate for candidate in candidates if candidate]
    candidate_hint = (
        "、".join(candidate_list) if candidate_list else "http://vnc:9222 (デフォルト)"
    )
    guidance = (
        "VNC サービス (例: http://vnc:9222) が起動し `/json/version` にアクセスできるか確認してください。"
        "Docker Compose を利用している場合は `docker compose ps vnc` で稼働状況を確認し、必要に応じて `docker compose up -d vnc` で再起動してください。"
        "接続先を変更する場合は BROWSER_USE_CDP_URL / VNC_CDP_URL / CDP_URL を設定してください。"
    )
    return (
        "ライブビューのブラウザに接続できないため実行できません。"
        f"{reason}。試行した CDP エンドポイント: {candidate_hint}。{guidance}"
    )


_LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def _candidate_host(candidate: str) -> str:
    """Return the host:port portion of *candidate* if available."""

    candidate = (candidate or "").strip()
    if not candidate:
        return ""

    parsed = urlsplit(candidate if "://" in candidate else f"http://{candidate}")
    if parsed.netloc:
        return parsed.netloc
    return parsed.path or ""


def normalise_cdp_websocket(candidate: str, websocket_url: str) -> str:
    """Return a websocket endpoint reachable from the current environment.

    Chromium typically reports DevTools websocket URLs that point at
    ``127.0.0.1`` even when the browser is exposed on a different host.  When
    automation runs in another container this loopback address becomes
    unreachable.  This helper rewrites such URLs so the host portion matches
    *candidate*, preserving the original port and path components.
    """

    base = (candidate or "").strip()
    websocket_url = (websocket_url or "").strip()
    if not websocket_url:
        return base

    try:
        parsed = urlsplit(websocket_url)
    except ValueError:
        return base or websocket_url

    scheme = parsed.scheme.lower()
    if not scheme:
        scheme = "ws"
    elif scheme == "http":
        scheme = "ws"
    elif scheme == "https":
        scheme = "wss"
    elif scheme not in {"ws", "wss"}:
        return base or websocket_url

    host = parsed.netloc
    hostname = parsed.hostname or ""
    if not host or hostname in _LOCAL_HOSTNAMES:
        replacement = _candidate_host(base)
        if replacement:
            host = replacement

    if not host:
        host = parsed.netloc

    if not host:
        return urlunsplit((scheme, parsed.netloc, parsed.path, parsed.query, parsed.fragment)) if parsed.netloc else (base or websocket_url)

    path = parsed.path or ""
    query = parsed.query or ""
    fragment = parsed.fragment or ""
    return urlunsplit((scheme, host, path, query, fragment))
