#!/usr/bin/env python3
"""
Tests for error contract and response format verification.
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from agent.index_resolution import (
    ErrorCode, StructuredError, Observation, StructuredResponse
)


def test_error_code_enum():
    """Test that all required error codes are defined."""
    print("Testing error code enum...")
    
    required_codes = [
        "ELEMENT_NOT_FOUND",
        "ELEMENT_NOT_INTERACTABLE", 
        "CATALOG_OUTDATED",
        "NAVIGATION_TIMEOUT",
        "UNSUPPORTED_ACTION",
        "DOMAIN_NOT_ALLOWED"
    ]
    
    for code_name in required_codes:
        assert hasattr(ErrorCode, code_name), f"Missing error code: {code_name}"
        code = getattr(ErrorCode, code_name)
        assert code.value == code_name, f"Error code value mismatch: {code.value} != {code_name}"
    
    print("✓ All required error codes are defined")


def test_structured_error_format():
    """Test structured error format compliance."""
    print("Testing structured error format...")
    
    # Test basic error
    error = StructuredError(
        code=ErrorCode.ELEMENT_NOT_FOUND,
        message="Element not found"
    )
    
    assert error.code == ErrorCode.ELEMENT_NOT_FOUND
    assert error.message == "Element not found"
    assert error.details is None
    
    # Test error with details
    error_with_details = StructuredError(
        code=ErrorCode.CATALOG_OUTDATED,
        message="Catalog version mismatch",
        details={
            "expected_version": "abc123",
            "current_version": "def456"
        }
    )
    
    assert error_with_details.details["expected_version"] == "abc123"
    assert error_with_details.details["current_version"] == "def456"
    
    print("✓ Structured error format is correct")


def test_observation_format():
    """Test observation format compliance."""
    print("Testing observation format...")
    
    # Test basic observation
    observation = Observation(
        url="https://example.com",
        title="Test Page",
        short_summary="Page with 3 interactive elements"
    )
    
    assert observation.url == "https://example.com"
    assert observation.title == "Test Page"
    assert observation.short_summary == "Page with 3 interactive elements"
    assert observation.catalog_version is None
    assert observation.nav_detected == False
    
    # Test observation with all fields
    full_observation = Observation(
        url="https://example.com/page2",
        title="Another Page",
        short_summary="Page after navigation",
        catalog_version="xyz789",
        nav_detected=True
    )
    
    assert full_observation.catalog_version == "xyz789"
    assert full_observation.nav_detected == True
    
    print("✓ Observation format is correct")


def test_structured_response_success():
    """Test structured response format for success cases."""
    print("Testing structured response success format...")
    
    observation = Observation(
        url="https://example.com",
        title="Test Page",
        short_summary="Clicked button successfully",
        catalog_version="abc123"
    )
    
    response = StructuredResponse(
        success=True,
        observation=observation,
        is_done=False,
        complete=False
    )
    
    response_dict = response.to_dict()
    
    # Check required fields
    assert "success" in response_dict
    assert "error" in response_dict
    assert "observation" in response_dict
    assert "is_done" in response_dict
    assert "complete" in response_dict
    
    # Check values
    assert response_dict["success"] == True
    assert response_dict["error"] is None
    assert response_dict["is_done"] == False
    assert response_dict["complete"] == False
    
    # Check observation fields
    obs = response_dict["observation"]
    assert obs["url"] == "https://example.com"
    assert obs["title"] == "Test Page"
    assert obs["short_summary"] == "Clicked button successfully"
    assert obs["catalog_version"] == "abc123"
    assert obs["nav_detected"] == False
    
    print("✓ Structured response success format is correct")


def test_structured_response_error():
    """Test structured response format for error cases."""
    print("Testing structured response error format...")
    
    error = StructuredError(
        code=ErrorCode.ELEMENT_NOT_FOUND,
        message="Button not found on page",
        details={"requested_index": 5}
    )
    
    response = StructuredResponse(
        success=False,
        error=error
    )
    
    response_dict = response.to_dict()
    
    # Check required fields
    assert response_dict["success"] == False
    assert response_dict["error"] is not None
    
    # Check error structure
    err = response_dict["error"]
    assert err["code"] == "ELEMENT_NOT_FOUND"
    assert err["message"] == "Button not found on page"
    assert err["details"]["requested_index"] == 5
    
    print("✓ Structured response error format is correct")


def test_all_error_codes_in_responses():
    """Test that all error codes can be used in structured responses."""
    print("Testing all error codes in responses...")
    
    test_cases = [
        (ErrorCode.ELEMENT_NOT_FOUND, "Element with index 5 not found"),
        (ErrorCode.ELEMENT_NOT_INTERACTABLE, "Element is disabled"),
        (ErrorCode.CATALOG_OUTDATED, "Please refresh catalog"),
        (ErrorCode.NAVIGATION_TIMEOUT, "Page took too long to load"),
        (ErrorCode.UNSUPPORTED_ACTION, "Action not supported"),
        (ErrorCode.DOMAIN_NOT_ALLOWED, "Domain not in allowlist")
    ]
    
    for code, message in test_cases:
        error = StructuredError(code=code, message=message)
        response = StructuredResponse(success=False, error=error)
        response_dict = response.to_dict()
        
        assert response_dict["success"] == False
        assert response_dict["error"]["code"] == code.value
        assert response_dict["error"]["message"] == message
    
    print("✓ All error codes work in structured responses")


def test_backward_compatibility_fields():
    """Test that backward compatibility fields are included."""
    print("Testing backward compatibility fields...")
    
    # Test complete field (backward compatibility)
    response = StructuredResponse(
        success=True,
        is_done=True,
        complete=True  # Should match is_done for backward compatibility
    )
    
    response_dict = response.to_dict()
    assert response_dict["complete"] == True
    assert response_dict["is_done"] == True
    
    # Test that both fields can be set independently
    response2 = StructuredResponse(
        success=True,
        is_done=False,
        complete=False
    )
    
    response_dict2 = response2.to_dict()
    assert response_dict2["complete"] == False
    assert response_dict2["is_done"] == False
    
    print("✓ Backward compatibility fields are included")


def test_response_serialization():
    """Test that responses can be properly serialized to JSON."""
    print("Testing response serialization...")
    
    import json
    
    # Test complex response with all fields
    error = StructuredError(
        code=ErrorCode.ELEMENT_NOT_INTERACTABLE,
        message="Element is currently disabled",
        details={
            "element_info": {
                "tag": "button",
                "index": 3,
                "disabled": True
            }
        }
    )
    
    observation = Observation(
        url="https://example.com/form",
        title="Contact Form",
        short_summary="Form with 5 inputs and 2 buttons",
        catalog_version="abc123def",
        nav_detected=False
    )
    
    response = StructuredResponse(
        success=False,
        error=error,
        observation=observation,
        is_done=False,
        complete=False
    )
    
    # Convert to dict and serialize
    response_dict = response.to_dict()
    json_string = json.dumps(response_dict, indent=2)
    
    # Parse back to verify it's valid JSON
    parsed = json.loads(json_string)
    
    assert parsed["success"] == False
    assert parsed["error"]["code"] == "ELEMENT_NOT_INTERACTABLE"
    assert parsed["observation"]["catalog_version"] == "abc123def"
    
    print("✓ Response serialization works correctly")


def test_minimal_error_response():
    """Test minimal error response (just code and message)."""
    print("Testing minimal error response...")
    
    error = StructuredError(
        code=ErrorCode.UNSUPPORTED_ACTION,
        message="Action not supported"
    )
    
    response = StructuredResponse(success=False, error=error)
    response_dict = response.to_dict()
    
    # Should have minimal required fields
    assert response_dict["success"] == False
    assert response_dict["error"]["code"] == "UNSUPPORTED_ACTION"
    assert response_dict["error"]["message"] == "Action not supported"
    # Details should not be present when None
    assert "details" not in response_dict["error"]
    
    print("✓ Minimal error response format is correct")


def run_all_tests():
    """Run all error contract tests."""
    print("Running error contract tests...")
    print()
    
    test_error_code_enum()
    test_structured_error_format()
    test_observation_format()
    test_structured_response_success()
    test_structured_response_error()
    test_all_error_codes_in_responses()
    test_backward_compatibility_fields()
    test_response_serialization()
    test_minimal_error_response()
    
    print()
    print("🎉 All error contract tests passed!")


if __name__ == "__main__":
    run_all_tests()