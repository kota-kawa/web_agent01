#!/usr/bin/env python3
"""
Tests for index resolution and structured response functionality.
"""
import sys
import os
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from agent.browser.dom import DOMElementNode
from agent.element_catalog import generate_element_catalog
from agent.index_resolution import (
    IndexResolver, StructuredExecutor, ErrorCode, StructuredError, 
    Observation, StructuredResponse
)


def test_index_resolver():
    """Test index-based target resolution."""
    print("Testing index resolver...")
    
    # Create catalog
    mock_dom_data = {
        "tagName": "body",
        "attributes": {},
        "xpath": "/html/body",
        "isVisible": True,
        "isInteractive": False,
        "text": "",
        "children": [
            {
                "tagName": "button",
                "attributes": {"id": "submit-btn"},
                "xpath": "/html/body/button[1]",
                "isVisible": True,
                "isInteractive": True,
                "text": "Submit",
                "children": []
            },
            {
                "tagName": "input",
                "attributes": {"type": "text", "disabled": "true"},
                "xpath": "/html/body/input[1]",
                "isVisible": True,
                "isInteractive": True,
                "text": "",
                "children": []
            }
        ]
    }
    
    dom_tree = DOMElementNode.from_json(mock_dom_data)
    catalog = generate_element_catalog(dom_tree, "https://example.com", "Test")
    
    resolver = IndexResolver()
    resolver.set_catalog(catalog)
    
    # Test valid index resolution
    result = resolver.resolve_target("index=0")
    assert result["success"] == True
    assert result["index"] == 0
    assert "robust_selectors" in result
    assert len(result["robust_selectors"]) > 0
    
    # Test invalid index
    result = resolver.resolve_target("index=99")
    assert result["success"] == False
    assert result["error"].code == ErrorCode.ELEMENT_NOT_FOUND
    
    # Test disabled element
    result = resolver.resolve_target("index=1")
    assert result["success"] == False
    assert result["error"].code == ErrorCode.ELEMENT_NOT_INTERACTABLE
    
    # Test catalog version validation
    result = resolver.resolve_target("index=0", "wrong-version")
    assert result["success"] == False
    assert result["error"].code == ErrorCode.CATALOG_OUTDATED
    
    # Test valid catalog version
    result = resolver.resolve_target("index=0", catalog.catalog_version)
    assert result["success"] == True
    
    print("✓ Index resolver works correctly")


def test_target_parsing():
    """Test target specification parsing."""
    print("Testing target parsing...")
    
    resolver = IndexResolver()
    
    # Test index format
    target_info = resolver._parse_target("index=5")
    assert target_info["type"] == "index"
    assert target_info["value"] == 5
    
    # Test CSS format
    target_info = resolver._parse_target("css=button.submit")
    assert target_info["type"] == "css"
    assert target_info["value"] == "button.submit"
    
    # Test XPath format
    target_info = resolver._parse_target("xpath=//button[@id='submit']")
    assert target_info["type"] == "xpath"
    assert target_info["value"] == "//button[@id='submit']"
    
    # Test legacy XPath
    target_info = resolver._parse_target("//button[@id='submit']")
    assert target_info["type"] == "xpath"
    assert target_info["value"] == "//button[@id='submit']"
    
    # Test legacy CSS (default)
    target_info = resolver._parse_target("button.submit")
    assert target_info["type"] == "css"
    assert target_info["value"] == "button.submit"
    
    print("✓ Target parsing works correctly")


def test_backward_compatibility():
    """Test backward compatibility with existing selectors."""
    print("Testing backward compatibility...")
    
    resolver = IndexResolver()
    
    # CSS selector should pass through unchanged
    result = resolver.resolve_target("css=button.submit")
    assert result["success"] == True
    assert result["selector_type"] == "css"
    assert result["selector"] == "button.submit"
    
    # XPath selector should pass through unchanged
    result = resolver.resolve_target("xpath=//button[@id='submit']")
    assert result["success"] == True
    assert result["selector_type"] == "xpath"
    assert result["selector"] == "//button[@id='submit']"
    
    print("✓ Backward compatibility works correctly")


def test_structured_response():
    """Test structured response format."""
    print("Testing structured response format...")
    
    # Test success response
    observation = Observation(
        url="https://example.com",
        title="Test Page",
        short_summary="Page with 3 interactive elements",
        catalog_version="abc123",
        nav_detected=False
    )
    
    response = StructuredResponse(
        success=True,
        observation=observation,
        is_done=False,
        complete=False
    )
    
    response_dict = response.to_dict()
    assert response_dict["success"] == True
    assert response_dict["error"] is None
    assert response_dict["observation"]["url"] == "https://example.com"
    assert response_dict["observation"]["catalog_version"] == "abc123"
    assert response_dict["is_done"] == False
    assert response_dict["complete"] == False
    
    # Test error response
    error = StructuredError(
        code=ErrorCode.ELEMENT_NOT_FOUND,
        message="Element not found",
        details={"index": 5}
    )
    
    response = StructuredResponse(
        success=False,
        error=error
    )
    
    response_dict = response.to_dict()
    assert response_dict["success"] == False
    assert response_dict["error"]["code"] == "ELEMENT_NOT_FOUND"
    assert response_dict["error"]["message"] == "Element not found"
    assert response_dict["error"]["details"]["index"] == 5
    
    print("✓ Structured response format works correctly")


def test_index_mode_disabled():
    """Test behavior when index mode is disabled."""
    print("Testing index mode disabled...")
    
    # Temporarily set environment variable
    original_value = os.environ.get("INDEX_MODE")
    os.environ["INDEX_MODE"] = "false"
    
    try:
        resolver = IndexResolver()
        
        # Should reject index-based targets
        result = resolver.resolve_target("index=0")
        assert result["success"] == False
        assert result["error"].code == ErrorCode.UNSUPPORTED_ACTION
        
        # Should still accept CSS/XPath
        result = resolver.resolve_target("css=button")
        assert result["success"] == True
        
    finally:
        # Restore original environment
        if original_value is not None:
            os.environ["INDEX_MODE"] = original_value
        else:
            os.environ.pop("INDEX_MODE", None)
    
    print("✓ Index mode disabled works correctly")


def test_domain_allowlist():
    """Test domain allowlist functionality."""
    print("Testing domain allowlist...")
    
    # Temporarily set environment variable
    original_value = os.environ.get("ALLOWED_DOMAINS")
    os.environ["ALLOWED_DOMAINS"] = "example.com,test.org"
    
    try:
        executor = StructuredExecutor()
        
        # Test allowed domain
        assert executor._is_domain_allowed("https://example.com/page") == True
        assert executor._is_domain_allowed("https://sub.example.com/page") == True
        assert executor._is_domain_allowed("https://test.org") == True
        
        # Test disallowed domain
        assert executor._is_domain_allowed("https://malicious.com") == False
        assert executor._is_domain_allowed("https://other.net") == False
        
    finally:
        # Restore original environment
        if original_value is not None:
            os.environ["ALLOWED_DOMAINS"] = original_value
        else:
            os.environ.pop("ALLOWED_DOMAINS", None)
    
    print("✓ Domain allowlist works correctly")


def test_error_codes():
    """Test all error code scenarios."""
    print("Testing error codes...")
    
    # Test each error code
    error_codes = [
        ErrorCode.ELEMENT_NOT_FOUND,
        ErrorCode.ELEMENT_NOT_INTERACTABLE,
        ErrorCode.CATALOG_OUTDATED,
        ErrorCode.NAVIGATION_TIMEOUT,
        ErrorCode.UNSUPPORTED_ACTION,
        ErrorCode.DOMAIN_NOT_ALLOWED
    ]
    
    for code in error_codes:
        error = StructuredError(
            code=code,
            message=f"Test error for {code.value}",
            details={"test": True}
        )
        
        response = StructuredResponse(success=False, error=error)
        response_dict = response.to_dict()
        
        assert response_dict["error"]["code"] == code.value
        assert "Test error for" in response_dict["error"]["message"]
    
    print("✓ Error codes work correctly")


def mock_execute_function(payload):
    """Mock execute function for testing."""
    return {"html": "<html></html>", "warnings": []}


def test_new_actions():
    """Test new auxiliary actions."""
    print("Testing new auxiliary actions...")
    
    executor = StructuredExecutor()
    
    # Test refresh_catalog (will fail without proper DOM but should handle gracefully)
    refresh_action = {"action": "refresh_catalog"}
    response = executor.execute_action_with_structure(
        refresh_action, 
        mock_execute_function,
        "https://example.com",
        "Test Page"
    )
    # Should fail gracefully when DOM is not available
    assert response.success == False
    assert response.error.code == ErrorCode.UNSUPPORTED_ACTION
    
    # Test scroll_to_text
    scroll_action = {"action": "scroll_to_text", "text": "Submit"}
    response = executor.execute_action_with_structure(
        scroll_action,
        mock_execute_function,
        "https://example.com",
        "Test Page"
    )
    # Should succeed with mock function
    assert response.success == True
    assert "Scrolled to text" in response.observation.short_summary
    
    # Test wait_until
    wait_action = {"action": "wait", "until": "timeout", "value": "1000"}
    response = executor.execute_action_with_structure(
        wait_action,
        mock_execute_function,
        "https://example.com",
        "Test Page"
    )
    assert response.success == True
    assert "Waited for timeout" in response.observation.short_summary
    
    print("✓ New auxiliary actions work correctly")


def run_all_tests():
    """Run all index resolution tests."""
    print("Running index resolution tests...")
    print()
    
    test_index_resolver()
    test_target_parsing()
    test_backward_compatibility()
    test_structured_response()
    test_index_mode_disabled()
    test_domain_allowlist()
    test_error_codes()
    test_new_actions()
    
    print()
    print("🎉 All index resolution tests passed!")


if __name__ == "__main__":
    run_all_tests()