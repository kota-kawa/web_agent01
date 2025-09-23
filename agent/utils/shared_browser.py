"""Utilities shared between the web UI and automation stack for browser access."""
from __future__ import annotations

import os
from typing import Iterable


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
