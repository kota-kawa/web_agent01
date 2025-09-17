"""Tests for the improved prompt template with Typed DSL system."""

from agent.controller.prompt import build_prompt


def test_prompt_contains_typed_dsl_documentation():
    """Test that the prompt includes comprehensive Typed DSL documentation."""
    prompt = build_prompt(
        cmd="テストコマンド",
        page="<html><body><div>test</div></body></html>",
        hist=[],
        screenshot=False,
        elements=None,
        error=None,
    )
    
    # Check for typed DSL system documentation
    assert "Typed DSL System" in prompt
    assert "composite selector" in prompt
    assert "Plan → Validate → Dry Run → Execute" in prompt
    
    # Check for modern action examples
    assert '"action": "navigate"' in prompt
    assert '"action": "click"' in prompt
    assert '"action": "wait"' in prompt
    assert '"action": "extract"' in prompt
    assert '"action": "screenshot"' in prompt
    assert '"action": "switch_tab"' in prompt
    assert '"action": "focus_iframe"' in prompt


def test_prompt_contains_selector_strategy_guidance():
    """Test that the prompt includes comprehensive selector strategy guidance."""
    prompt = build_prompt(
        cmd="テストコマンド",
        page="<html><body><div>test</div></body></html>",
        hist=[],
    )
    
    # Check for selector priority guidance
    assert "stable_id" in prompt
    assert "data-testid" in prompt
    assert "role" in prompt
    assert "aria_label" in prompt
    assert "Composite Selector System" in prompt


def test_prompt_contains_error_handling_guidance():
    """Test that the prompt includes comprehensive error handling guidance."""
    prompt = build_prompt(
        cmd="テストコマンド", 
        page="<html><body><div>test</div></body></html>",
        hist=[],
    )
    
    # Check for error handling strategies
    assert "エラーハンドリング戦略" in prompt
    assert "CATALOG_OUTDATED" in prompt
    assert "ELEMENT_NOT_INTERACTABLE" in prompt
    assert "NAVIGATION_TIMEOUT" in prompt
    assert "SELECTOR_RESOLUTION_FAILED" in prompt
    assert "FRAME_DETACHED" in prompt


def test_prompt_contains_modern_helper_functions():
    """Test that the prompt includes updated helper functions."""
    prompt = build_prompt(
        cmd="テストコマンド",
        page="<html><body><div>test</div></body></html>", 
        hist=[],
    )
    
    # Check for modern helper functions
    assert "Typed DSL対応" in prompt
    assert "composite selectorサポート" in prompt
    assert "wait_for: dict" in prompt
    assert "clear: bool" in prompt
    assert "press_enter: bool" in prompt
    assert "value_or_label" in prompt


def test_prompt_with_catalog_includes_index_guidance():
    """Test that catalog-enabled prompts include proper index guidance."""
    prompt = build_prompt(
        cmd="テストコマンド",
        page="<html><body><div>test</div></body></html>",
        hist=[],
        element_catalog_text="[0] button: 送信",
        catalog_metadata={"index_mode_enabled": True},
    )
    
    assert "index=N" in prompt
    assert "[0] button: 送信" in prompt


def test_prompt_without_catalog_includes_css_guidance():
    """Test that non-catalog prompts include CSS selector guidance."""
    prompt = build_prompt(
        cmd="テストコマンド", 
        page="<html><body><div>test</div></body></html>",
        hist=[],
        catalog_metadata={"index_mode_enabled": False},
    )
    
    assert "INDEX_MODE が無効" in prompt
    assert "css=" in prompt


def test_prompt_structure_and_organization():
    """Test that the prompt has good structure and organization."""
    prompt = build_prompt(
        cmd="テストコマンド",
        page="<html><body><div>test</div></body></html>",
        hist=[],
    )
    
    # Check for proper section headers
    assert "## 【重要】ブラウザ自動化の基本原則" in prompt
    assert "## DSL アクション一覧" in prompt
    assert "## ブラウザ操作とエラーハンドリング指針" in prompt
    assert "## 成功のための重要な指針" in prompt
    assert "## アクションヘルパー関数" in prompt
    
    # Check that sections are logically organized
    basic_principles_pos = prompt.find("ブラウザ自動化の基本原則")
    dsl_actions_pos = prompt.find("DSL アクション一覧")
    helper_functions_pos = prompt.find("アクションヘルパー関数")
    
    assert basic_principles_pos < dsl_actions_pos < helper_functions_pos