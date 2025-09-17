from agent.controller.prompt import build_prompt


def test_build_prompt_includes_catalog_section():
    prompt = build_prompt(
        cmd="ボタンをクリック",
        page="<html><body><button>送信</button></body></html>",
        hist=[],
        screenshot=False,
        elements=None,
        error=None,
        element_catalog_text="[0] button: 送信",
        catalog_metadata={
            "catalog_version": "v1",
            "metadata": {"url": "https://example.com"},
            "index_mode_enabled": True,
        },
    )

    assert "catalog_version: v1" in prompt
    assert "index=N" in prompt
    assert "[0] button: 送信" in prompt


def test_build_prompt_when_index_disabled():
    prompt = build_prompt(
        cmd="テスト",
        page="<html></html>",
        hist=[],
        screenshot=False,
        elements=None,
        error=None,
        element_catalog_text="",
        catalog_metadata={"index_mode_enabled": False},
    )

    assert "INDEX_MODE が無効" in prompt
