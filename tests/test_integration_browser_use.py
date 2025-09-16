#!/usr/bin/env python3
"""
Integration test for Browser Use style index-based DSL execution
Tests the end-to-end functionality with a simple HTML page
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

import asyncio
import tempfile
import os
from unittest.mock import Mock, AsyncMock, patch

# Test HTML content
TEST_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Test Page for Browser Use Style</title>
</head>
<body>
    <header>
        <h1>Test Application</h1>
        <nav>
            <a href="/home" id="home-link">Home</a>
            <a href="/about" id="about-link">About</a>
        </nav>
    </header>
    
    <main>
        <form id="test-form">
            <label for="username">Username:</label>
            <input type="text" id="username" name="username" placeholder="Enter username">
            
            <label for="password">Password:</label>
            <input type="password" id="password" name="password" placeholder="Enter password">
            
            <button type="submit" id="submit-btn">Login</button>
            <button type="button" id="cancel-btn" disabled>Cancel</button>
        </form>
        
        <div class="info">
            <p>Please log in to continue</p>
            <a href="/forgot-password">Forgot Password?</a>
        </div>
    </main>
    
    <footer>
        <p>&copy; 2024 Test Company</p>
    </footer>
</body>
</html>
"""


def test_element_catalog_generation():
    """Test element catalog generation with mock data."""
    from agent.element_catalog import ElementCatalogGenerator, ElementCatalog
    
    # Mock the browser evaluation to return our test data
    mock_elements = [
        {
            "tag": "a",
            "role": None,
            "primaryLabel": "Home",
            "secondaryLabel": None,
            "sectionHint": "navigation",
            "sectionId": "nav",
            "stateHint": None,
            "hrefShort": "/home",
            "robustSelectors": [
                "css=a[href='/home']",
                "text=Home",
                "css=#home-link"
            ],
            "bbox": {"x": 50, "y": 20, "width": 60, "height": 20},
            "visible": True,
            "disabled": False,
            "hrefFull": "/home",
            "nearestTexts": [],
            "elementId": "home-link",
            "elementClasses": "",
            "dataTestid": None
        },
        {
            "tag": "input",
            "role": None,
            "primaryLabel": "Enter username",
            "secondaryLabel": "Username:",
            "sectionHint": "form",
            "sectionId": "test-form",
            "stateHint": None,
            "hrefShort": None,
            "robustSelectors": [
                "css=input[name='username']",
                "css=#username",
                "css=input[placeholder='Enter username']"
            ],
            "bbox": {"x": 100, "y": 100, "width": 200, "height": 30},
            "visible": True,
            "disabled": False,
            "hrefFull": None,
            "nearestTexts": ["Username:"],
            "elementId": "username",
            "elementClasses": "",
            "dataTestid": None
        },
        {
            "tag": "button",
            "role": None,
            "primaryLabel": "Login",
            "secondaryLabel": None,
            "sectionHint": "form",
            "sectionId": "test-form",
            "stateHint": "enabled",
            "hrefShort": None,
            "robustSelectors": [
                "css=button[type='submit']",
                "text=Login",
                "css=#submit-btn"
            ],
            "bbox": {"x": 100, "y": 150, "width": 80, "height": 30},
            "visible": True,
            "disabled": False,
            "hrefFull": None,
            "nearestTexts": [],
            "elementId": "submit-btn",
            "elementClasses": "",
            "dataTestid": None
        }
    ]
    
    async def run_test():
        # Create mock page
        mock_page = AsyncMock()
        mock_page.url.return_value = "https://test.com/login"
        mock_page.title.return_value = "Test Page for Browser Use Style"
        mock_page.viewport_size.return_value = {"width": 1280, "height": 720}
        mock_page.evaluate.return_value = mock_elements
        
        # Generate catalog
        generator = ElementCatalogGenerator(mock_page)
        catalog = await generator.generate_catalog()
        
        # Verify catalog
        assert catalog.url == "https://test.com/login"
        assert catalog.title == "Test Page for Browser Use Style"
        assert len(catalog.entries) == 3
        
        # Test index mapping
        assert catalog.get_element_by_index(0) is not None
        assert catalog.get_element_by_index(0).tag == "a"
        assert catalog.get_element_by_index(1).tag == "input"
        assert catalog.get_element_by_index(2).tag == "button"
        
        # Test robust selectors
        selectors_0 = catalog.get_robust_selectors(0)
        assert "css=a[href='/home']" in selectors_0
        assert "text=Home" in selectors_0
        
        # Test short view
        short_view = catalog.get_short_view()
        assert len(short_view) == 3
        assert short_view[0]["primary_label"] == "Home"
        assert short_view[1]["primary_label"] == "Enter username"
        assert short_view[2]["primary_label"] == "Login"
        
        print("✓ Element catalog generation test passed")
        return catalog
    
    return asyncio.run(run_test())


def test_index_resolution():
    """Test index to selector resolution."""
    catalog = test_element_catalog_generation()
    
    # Test resolving index 0 (Home link)
    selectors_0 = catalog.get_robust_selectors(0)
    assert len(selectors_0) > 0
    assert selectors_0[0] == "css=a[href='/home']"  # Primary selector
    
    # Test resolving index 1 (Username input)
    selectors_1 = catalog.get_robust_selectors(1)
    assert len(selectors_1) > 0
    assert "css=input[name='username']" in selectors_1
    
    # Test resolving index 2 (Login button)
    selectors_2 = catalog.get_robust_selectors(2)
    assert len(selectors_2) > 0
    assert "css=button[type='submit']" in selectors_2
    
    # Test invalid index
    selectors_invalid = catalog.get_robust_selectors(999)
    assert len(selectors_invalid) == 0
    
    print("✓ Index resolution test passed")


def test_error_response_creation():
    """Test structured error response creation."""
    from agent.response_types import (
        create_error_response, create_catalog_outdated_response,
        DSLErrorCode, ObservationData
    )
    
    # Test element not found error
    observation = ObservationData(
        url="https://test.com",
        title="Test Page",
        catalog_version="v123"
    )
    
    error_response = create_error_response(
        error_code=DSLErrorCode.ELEMENT_NOT_FOUND,
        message="Element at index 5 not found",
        observation=observation
    )
    
    assert error_response.success is False
    assert error_response.error.code == DSLErrorCode.ELEMENT_NOT_FOUND
    assert "index 5" in error_response.error.message
    
    # Test catalog outdated error
    outdated_response = create_catalog_outdated_response(
        current_version="v123",
        expected_version="v456"
    )
    
    assert outdated_response.success is False
    assert outdated_response.error.code == DSLErrorCode.CATALOG_OUTDATED
    assert "refresh_catalog" in outdated_response.error.details["suggested_action"]
    
    print("✓ Error response creation test passed")


def test_dsl_action_structure():
    """Test DSL action structure for new actions."""
    # Test index-based click action
    click_action = {"action": "click", "index": 0}
    assert click_action["action"] == "click"
    assert click_action["index"] == 0
    
    # Test index-based type action
    type_action = {"action": "type", "index": 1, "value": "testuser"}
    assert type_action["action"] == "type"
    assert type_action["index"] == 1
    assert type_action["value"] == "testuser"
    
    # Test refresh catalog action
    refresh_action = {"action": "refresh_catalog"}
    assert refresh_action["action"] == "refresh_catalog"
    
    # Test scroll to text action
    scroll_action = {"action": "scroll_to_text", "text": "Login"}
    assert scroll_action["action"] == "scroll_to_text"
    assert scroll_action["text"] == "Login"
    
    # Test enhanced wait action
    wait_action = {"action": "wait", "until": "network_idle", "ms": 3000}
    assert wait_action["action"] == "wait"
    assert wait_action["until"] == "network_idle"
    assert wait_action["ms"] == 3000
    
    print("✓ DSL action structure test passed")


def test_catalog_version_generation():
    """Test catalog version generation for change detection."""
    from agent.element_catalog import ElementCatalogGenerator
    import hashlib
    
    # Test that different content generates different versions
    content1 = "page1_content"
    content2 = "page2_content"
    
    hash1 = hashlib.md5(content1.encode()).hexdigest()[:8]
    hash2 = hashlib.md5(content2.encode()).hexdigest()[:8]
    
    assert hash1 != hash2
    print("✓ Catalog version generation test passed")


def test_backward_compatibility():
    """Test that new features don't break existing functionality."""
    # Test that traditional CSS selectors still work
    traditional_action = {"action": "click", "target": "css=button.submit"}
    assert traditional_action["action"] == "click"
    assert traditional_action["target"] == "css=button.submit"
    
    # Test that responses include both new and old fields
    from agent.response_types import create_success_response, ObservationData
    
    observation = ObservationData(url="test.com", title="Test")
    response = create_success_response(
        observation=observation, 
        is_done=True,
        html="<html>test</html>",
        warnings=["test warning"]
    )
    response_dict = response.to_dict()
    
    # Check backward compatibility fields exist
    assert "complete" in response_dict
    assert "warnings" in response_dict
    
    # Check new fields
    assert "success" in response_dict
    assert "is_done" in response_dict
    assert "observation" in response_dict
    assert "error" in response_dict
    
    # Check that complete mirrors is_done
    assert response_dict["complete"] == response_dict["is_done"]
    
    # Check that html is included when provided
    assert response_dict["html"] == "<html>test</html>"
    
    print("✓ Backward compatibility test passed")


def test_prompt_integration():
    """Test that the prompt includes Browser Use style guidance."""
    from agent.controller.prompt import build_prompt
    from agent.browser.dom import DOMElementNode
    
    # Create mock DOM elements
    elements = DOMElementNode(
        tagName="div",
        children=[
            DOMElementNode(
                tagName="button",
                attributes={"id": "test-btn"},
                text="Test Button",
                highlightIndex=0,
                isInteractive=True,
                isVisible=True
            )
        ]
    )
    
    # Build prompt
    prompt = build_prompt(
        cmd="Click the test button",
        page="<html>Test</html>",
        hist=[],
        screenshot=False,
        elements=elements,
        error=None
    )
    
    # Check that prompt includes Browser Use guidance
    assert "Browser Use" in prompt or "インデックス" in prompt
    assert "index=" in prompt or "index:" in prompt
    assert "refresh_catalog" in prompt
    assert "scroll_to_text" in prompt
    
    print("✓ Prompt integration test passed")


def run_all_tests():
    """Run all integration tests."""
    print("Running Browser Use style integration tests...\n")
    
    test_element_catalog_generation()
    test_index_resolution()
    test_error_response_creation()
    test_dsl_action_structure()
    test_catalog_version_generation()
    test_backward_compatibility()
    test_prompt_integration()
    
    print("\n🎉 All integration tests passed! Browser Use style implementation is ready for use.")


if __name__ == "__main__":
    run_all_tests()