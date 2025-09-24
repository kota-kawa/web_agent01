from agent.utils.history import format_history_for_prompt


def test_format_history_for_prompt_empty() -> None:
    assert format_history_for_prompt([]) == ""


def test_format_history_for_prompt_includes_summary() -> None:
    history = [
        {
            "user": "検索で最新ニュースを調べて",
            "bot": {
                "status": "completed",
                "result": {
                    "success": True,
                    "final_result": "最新ニュースの一覧を表示しました",
                    "warnings": ["スクロールが必要でした"],
                },
            },
            "url": "https://news.example.com/",
        }
    ]

    formatted = format_history_for_prompt(history)

    assert "ユーザー指示: 検索で最新ニュースを調べて" in formatted
    assert "要約: 最新ニュースの一覧を表示しました" in formatted
    assert "最終URL: https://news.example.com/" in formatted


def test_format_history_for_prompt_limits_entries() -> None:
    history = [
        {"user": "one", "bot": {"status": "completed", "result": {"success": True}}, "url": "A"},
        {"user": "two", "bot": {"status": "failed", "error": "timeout"}, "url": "B"},
        {"user": "three", "bot": {"status": "completed", "result": {"success": True}}, "url": "C"},
    ]

    formatted = format_history_for_prompt(history, limit=2)

    assert "ユーザー指示: two" in formatted
    assert "ユーザー指示: three" in formatted
    assert "ユーザー指示: one" not in formatted


def test_format_history_for_prompt_includes_errors() -> None:
    history = [
        {
            "user": "フォーム送信",
            "bot": {
                "status": "failed",
                "error": "validation error",
                "result": {"success": False, "errors": ["missing field"]},
            },
            "url": "https://example.com/form",
        }
    ]

    formatted = format_history_for_prompt(history)

    assert "完了状態: 失敗" in formatted
    assert "エラー: missing field" in formatted or "エラー: validation error" in formatted
