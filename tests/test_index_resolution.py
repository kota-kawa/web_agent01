#!/usr/bin/env python3
"""
Unit tests for index resolution and error handling in Browser Use style DSL execution
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from agent.response_types import (
    DSLResponse, DSLError, DSLErrorCode, ObservationData,
    create_success_response, create_error_response,
    create_catalog_outdated_response, create_element_not_found_response,
    parse_legacy_warning_to_error
)


def test_dsl_error_creation():
    """Test creating DSL error objects."""
    error = DSLError(
        code=DSLErrorCode.ELEMENT_NOT_FOUND,
        message="Element not found on page",
        details={"target": "css=button.submit", "suggestions": ["Try alternative selector"]}
    )
    
    assert error.code == DSLErrorCode.ELEMENT_NOT_FOUND
    assert error.message == "Element not found on page"
    assert error.details["target"] == "css=button.submit"
    
    error_dict = error.to_dict()
    assert error_dict["code"] == "ELEMENT_NOT_FOUND"
    assert error_dict["message"] == "Element not found on page"
    assert "details" in error_dict
    
    print("✓ DSL error creation works correctly")


def test_observation_data_creation():
    """Test creating observation data objects."""
    observation = ObservationData(
        url="https://example.com/page",
        title="Example Page",
        short_summary="Login form with username and password fields",
        catalog_version="abc123",
        nav_detected=True
    )
    
    assert observation.url == "https://example.com/page"
    assert observation.catalog_version == "abc123"
    assert observation.nav_detected is True
    
    obs_dict = observation.to_dict()
    assert obs_dict["url"] == "https://example.com/page"
    assert obs_dict["catalog_version"] == "abc123"
    assert obs_dict["nav_detected"] is True
    
    print("✓ Observation data creation works correctly")


def test_success_response_creation():
    """Test creating successful DSL responses."""
    observation = ObservationData(
        url="https://example.com",
        title="Success Page",
        catalog_version="v123"
    )
    
    response = create_success_response(
        observation=observation,
        is_done=True,
        html="<html>Success</html>",
        warnings=["INFO: Action completed"],
        correlation_id="test123"
    )
    
    assert response.success is True
    assert response.is_done is True
    assert response.complete is True  # Backward compatibility
    assert response.error is None
    assert response.observation == observation
    assert response.html == "<html>Success</html>"
    assert len(response.warnings) == 1
    assert response.correlation_id == "test123"
    
    # Test dictionary conversion
    response_dict = response.to_dict()
    assert response_dict["success"] is True
    assert response_dict["is_done"] is True
    assert response_dict["error"] is None
    assert "observation" in response_dict
    
    print("✓ Success response creation works correctly")


def test_error_response_creation():
    """Test creating error DSL responses."""
    observation = ObservationData(
        url="https://example.com",
        title="Error Page"
    )
    
    response = create_error_response(
        error_code=DSLErrorCode.ELEMENT_NOT_INTERACTABLE,
        message="Button is disabled",
        details={"reason": "Element has disabled attribute"},
        observation=observation,
        warnings=["WARNING: Previous action may have caused this"],
        correlation_id="error123"
    )
    
    assert response.success is False
    assert response.is_done is False
    assert response.complete is False
    assert response.error is not None
    assert response.error.code == DSLErrorCode.ELEMENT_NOT_INTERACTABLE
    assert response.error.message == "Button is disabled"
    assert response.observation == observation
    
    # Test dictionary conversion
    response_dict = response.to_dict()
    assert response_dict["success"] is False
    assert response_dict["error"]["code"] == "ELEMENT_NOT_INTERACTABLE"
    assert response_dict["error"]["message"] == "Button is disabled"
    
    print("✓ Error response creation works correctly")


def test_catalog_outdated_response():
    """Test creating catalog outdated error responses."""
    response = create_catalog_outdated_response(
        current_version="v123",
        expected_version="v456",
        correlation_id="outdated123"
    )
    
    assert response.success is False
    assert response.error.code == DSLErrorCode.CATALOG_OUTDATED
    assert "refresh_catalog" in response.error.message
    assert response.error.details["current_catalog_version"] == "v123"
    assert response.error.details["expected_catalog_version"] == "v456"
    assert response.error.details["suggested_action"] == "refresh_catalog"
    
    print("✓ Catalog outdated response creation works correctly")


def test_element_not_found_response():
    """Test creating element not found error responses."""
    suggestions = ["Try refresh_catalog", "Use scroll_to_text"]
    
    response = create_element_not_found_response(
        target="index=5",
        suggestions=suggestions,
        correlation_id="notfound123"
    )
    
    assert response.success is False
    assert response.error.code == DSLErrorCode.ELEMENT_NOT_FOUND
    assert "index=5" in response.error.message
    assert response.error.details["target"] == "index=5"
    assert response.error.details["suggestions"] == suggestions
    
    print("✓ Element not found response creation works correctly")


def test_legacy_warning_parsing():
    """Test parsing legacy warning strings into structured errors."""
    # Test element not found
    warning1 = "ERROR:auto:Element not found: css=button.submit"
    error1 = parse_legacy_warning_to_error(warning1)
    assert error1.code == DSLErrorCode.ELEMENT_NOT_FOUND
    
    # Test timeout
    warning2 = "WARNING:auto:Navigation timeout occurred"
    error2 = parse_legacy_warning_to_error(warning2)
    assert error2.code == DSLErrorCode.NAVIGATION_TIMEOUT
    
    # Test not visible
    warning3 = "ERROR:auto:Element not visible: button"
    error3 = parse_legacy_warning_to_error(warning3)
    assert error3.code == DSLErrorCode.ELEMENT_NOT_INTERACTABLE
    
    # Test network error
    warning4 = "ERROR:auto:Network connection failed"
    error4 = parse_legacy_warning_to_error(warning4)
    assert error4.code == DSLErrorCode.NETWORK_ERROR
    
    # Test unknown error
    warning5 = "ERROR:auto:Something unexpected happened"
    error5 = parse_legacy_warning_to_error(warning5)
    assert error5.code == DSLErrorCode.BROWSER_ERROR
    
    print("✓ Legacy warning parsing works correctly")


def test_error_code_coverage():
    """Test that all error codes are properly defined."""
    # Test that all error codes have messages
    from agent.response_types import ERROR_CODE_MESSAGES, get_error_message
    
    for error_code in DSLErrorCode:
        message = get_error_message(error_code)
        assert message is not None
        assert len(message) > 0
        assert message != "An unknown error occurred."
    
    # Test specific messages
    assert "could not be found" in get_error_message(DSLErrorCode.ELEMENT_NOT_FOUND).lower()
    assert "interacted with" in get_error_message(DSLErrorCode.ELEMENT_NOT_INTERACTABLE).lower()
    assert "outdated" in get_error_message(DSLErrorCode.CATALOG_OUTDATED).lower()
    
    print("✓ Error code coverage is complete")


def test_response_backward_compatibility():
    """Test that new responses maintain backward compatibility."""
    # Create a response that should work with old code
    observation = ObservationData(
        url="https://example.com",
        title="Test Page"
    )
    
    response = create_success_response(
        observation=observation,
        is_done=True,
        html="<html>Test</html>",
        warnings=["INFO: Test warning"]
    )
    
    response_dict = response.to_dict()
    
    # Check that legacy fields exist
    assert "complete" in response_dict
    assert "html" in response_dict
    assert "warnings" in response_dict
    
    # Check that complete mirrors is_done for backward compatibility
    assert response_dict["complete"] == response_dict["is_done"]
    
    # Check that new fields are added
    assert "success" in response_dict
    assert "observation" in response_dict
    assert "error" in response_dict
    
    print("✓ Response backward compatibility maintained")


def test_structured_error_details():
    """Test that error details provide actionable information."""
    # Element not interactable with detailed suggestions
    response = create_element_not_found_response(
        target="index=10", 
        suggestions=[
            "Element may not be loaded yet - try wait action",
            "Element may be in a different section - try scroll_to_text",
            "Element catalog may be outdated - try refresh_catalog"
        ]
    )
    
    details = response.error.details
    assert "suggestions" in details
    assert len(details["suggestions"]) == 3
    assert "refresh_catalog" in details["suggestions"][-1]
    
    # Catalog outdated with specific guidance
    response2 = create_catalog_outdated_response("v1", "v2")
    details2 = response2.error.details
    assert details2["suggested_action"] == "refresh_catalog"
    assert details2["current_catalog_version"] == "v1"
    assert details2["expected_catalog_version"] == "v2"
    
    print("✓ Structured error details provide actionable information")


def run_all_tests():
    """Run all index resolution and error handling tests."""
    print("Running Browser Use style index resolution and error handling tests...\n")
    
    test_dsl_error_creation()
    test_observation_data_creation()
    test_success_response_creation()
    test_error_response_creation()
    test_catalog_outdated_response()
    test_element_not_found_response()
    test_legacy_warning_parsing()
    test_error_code_coverage()
    test_response_backward_compatibility()
    test_structured_error_details()
    
    print("\n🎉 All index resolution and error handling tests passed! Response structure implementation is working correctly.")


if __name__ == "__main__":
    run_all_tests()