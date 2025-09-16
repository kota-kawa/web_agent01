"""
Response structures and error codes for enhanced DSL execution with Browser Use style index support.

This module defines the structured response format and error codes that provide
better feedback to LLMs for making informed decisions about next actions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum


class DSLErrorCode(Enum):
    """Standardized error codes for DSL execution failures."""
    
    # Element-related errors
    ELEMENT_NOT_FOUND = "ELEMENT_NOT_FOUND"
    ELEMENT_NOT_INTERACTABLE = "ELEMENT_NOT_INTERACTABLE" 
    ELEMENT_NOT_VISIBLE = "ELEMENT_NOT_VISIBLE"
    ELEMENT_DISABLED = "ELEMENT_DISABLED"
    
    # Catalog-related errors
    CATALOG_OUTDATED = "CATALOG_OUTDATED"
    INVALID_INDEX = "INVALID_INDEX"
    CATALOG_GENERATION_FAILED = "CATALOG_GENERATION_FAILED"
    
    # Navigation and timing errors
    NAVIGATION_TIMEOUT = "NAVIGATION_TIMEOUT"
    ACTION_TIMEOUT = "ACTION_TIMEOUT"
    PAGE_LOAD_TIMEOUT = "PAGE_LOAD_TIMEOUT"
    
    # Action-specific errors
    UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"
    INVALID_SELECTOR = "INVALID_SELECTOR"
    INVALID_VALUE = "INVALID_VALUE"
    
    # System errors
    BROWSER_ERROR = "BROWSER_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    SECURITY_VIOLATION = "SECURITY_VIOLATION"


@dataclass
class DSLError:
    """Structured error information for DSL execution failures."""
    
    code: DSLErrorCode
    message: str
    details: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            "code": self.code.value,
            "message": self.message
        }
        if self.details:
            result["details"] = self.details
        return result


@dataclass
class ObservationData:
    """Page observation information included in DSL responses."""
    
    url: str
    title: str
    short_summary: Optional[str] = None
    catalog_version: Optional[str] = None
    nav_detected: bool = False
    dom_changes_detected: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "url": self.url,
            "title": self.title,
            "short_summary": self.short_summary,
            "catalog_version": self.catalog_version,
            "nav_detected": self.nav_detected,
            "dom_changes_detected": self.dom_changes_detected
        }


@dataclass
class DSLResponse:
    """Enhanced structured response for DSL execution with Browser Use style support."""
    
    # Core execution result
    success: bool
    
    # Error information (if success=False)
    error: Optional[DSLError] = None
    
    # Page observation data
    observation: Optional[ObservationData] = None
    
    # Task completion status 
    is_done: bool = False
    complete: bool = False  # Backward compatibility
    
    # Additional response data
    html: str = ""
    warnings: List[str] = field(default_factory=list)
    correlation_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON response."""
        result = {
            "success": self.success,
            "is_done": self.is_done,
            "complete": self.complete
        }
        
        if self.error:
            result["error"] = self.error.to_dict()
        else:
            result["error"] = None
            
        if self.observation:
            result["observation"] = self.observation.to_dict()
        else:
            result["observation"] = None
            
        if self.html:
            result["html"] = self.html
            
        if self.warnings:
            result["warnings"] = self.warnings
            
        if self.correlation_id:
            result["correlation_id"] = self.correlation_id
            
        return result


def create_success_response(
    observation: ObservationData,
    is_done: bool = False,
    html: str = "",
    warnings: List[str] = None,
    correlation_id: str = None
) -> DSLResponse:
    """Create a successful DSL response."""
    return DSLResponse(
        success=True,
        observation=observation,
        is_done=is_done,
        complete=is_done,  # Backward compatibility
        html=html,
        warnings=warnings or [],
        correlation_id=correlation_id
    )


def create_error_response(
    error_code: DSLErrorCode,
    message: str,
    details: Dict[str, Any] = None,
    observation: ObservationData = None,
    warnings: List[str] = None,
    correlation_id: str = None
) -> DSLResponse:
    """Create an error DSL response."""
    error = DSLError(
        code=error_code,
        message=message,
        details=details
    )
    
    return DSLResponse(
        success=False,
        error=error,
        observation=observation,
        is_done=False,
        complete=False,
        warnings=warnings or [],
        correlation_id=correlation_id
    )


def create_catalog_outdated_response(
    current_version: str,
    expected_version: str,
    observation: ObservationData = None,
    correlation_id: str = None
) -> DSLResponse:
    """Create a catalog outdated error response with guidance."""
    message = f"Element catalog is outdated. Please run refresh_catalog action to update."
    details = {
        "current_catalog_version": current_version,
        "expected_catalog_version": expected_version,
        "suggested_action": "refresh_catalog"
    }
    
    return create_error_response(
        error_code=DSLErrorCode.CATALOG_OUTDATED,
        message=message,
        details=details,
        observation=observation,
        correlation_id=correlation_id
    )


def create_element_not_found_response(
    target: str,
    suggestions: List[str] = None,
    observation: ObservationData = None,
    correlation_id: str = None
) -> DSLResponse:
    """Create element not found error response with suggestions."""
    message = f"Element not found: {target}"
    details = {
        "target": target,
        "suggestions": suggestions or ["Try refresh_catalog", "Use scroll_to_text to find the element", "Check if element exists on current page"]
    }
    
    return create_error_response(
        error_code=DSLErrorCode.ELEMENT_NOT_FOUND,
        message=message,
        details=details,
        observation=observation,
        correlation_id=correlation_id
    )


def create_element_not_interactable_response(
    target: str,
    reason: str = "Element is not in an interactable state",
    suggestions: List[str] = None,
    observation: ObservationData = None,
    correlation_id: str = None
) -> DSLResponse:
    """Create element not interactable error response."""
    message = f"Element not interactable: {target}. {reason}"
    details = {
        "target": target,
        "reason": reason,
        "suggestions": suggestions or ["Wait for element to become enabled", "Check if element is covered by another element", "Try scrolling to bring element into view"]
    }
    
    return create_error_response(
        error_code=DSLErrorCode.ELEMENT_NOT_INTERACTABLE,
        message=message,
        details=details,
        observation=observation,
        correlation_id=correlation_id
    )


# Error code to user-friendly message mapping
ERROR_CODE_MESSAGES = {
    DSLErrorCode.ELEMENT_NOT_FOUND: "The requested element could not be found on the page.",
    DSLErrorCode.ELEMENT_NOT_INTERACTABLE: "The element exists but cannot be interacted with at this time.",
    DSLErrorCode.ELEMENT_NOT_VISIBLE: "The element is present but not visible to the user.",
    DSLErrorCode.ELEMENT_DISABLED: "The element is disabled and cannot be interacted with.",
    DSLErrorCode.CATALOG_OUTDATED: "The element catalog is outdated and needs to be refreshed.", 
    DSLErrorCode.INVALID_INDEX: "The specified element index is not valid.",
    DSLErrorCode.CATALOG_GENERATION_FAILED: "Failed to generate or refresh the element catalog.",
    DSLErrorCode.NAVIGATION_TIMEOUT: "Page navigation took too long to complete.",
    DSLErrorCode.ACTION_TIMEOUT: "The action timed out before completing.",
    DSLErrorCode.PAGE_LOAD_TIMEOUT: "The page took too long to load completely.",
    DSLErrorCode.UNSUPPORTED_ACTION: "The requested action is not supported.",
    DSLErrorCode.INVALID_SELECTOR: "The provided selector is invalid or malformed.",
    DSLErrorCode.INVALID_VALUE: "The provided value is invalid for this action.",
    DSLErrorCode.BROWSER_ERROR: "An error occurred in the browser engine.",
    DSLErrorCode.NETWORK_ERROR: "A network error prevented the action from completing.",
    DSLErrorCode.SECURITY_VIOLATION: "The action was blocked due to security restrictions."
}


def get_error_message(error_code: DSLErrorCode) -> str:
    """Get user-friendly message for an error code."""
    return ERROR_CODE_MESSAGES.get(error_code, "An unknown error occurred.")


def parse_legacy_warning_to_error(warning: str) -> Optional[DSLError]:
    """Parse legacy warning strings into structured errors for backward compatibility."""
    warning_lower = warning.lower()
    
    # Map common warning patterns to error codes
    if "element not found" in warning_lower or "locator not found" in warning_lower:
        return DSLError(DSLErrorCode.ELEMENT_NOT_FOUND, warning)
    elif "timeout" in warning_lower:
        if "navigation" in warning_lower:
            return DSLError(DSLErrorCode.NAVIGATION_TIMEOUT, warning)
        else:
            return DSLError(DSLErrorCode.ACTION_TIMEOUT, warning)
    elif "not visible" in warning_lower or "not interactable" in warning_lower:
        return DSLError(DSLErrorCode.ELEMENT_NOT_INTERACTABLE, warning)
    elif "disabled" in warning_lower:
        return DSLError(DSLErrorCode.ELEMENT_DISABLED, warning)
    elif "network" in warning_lower or "connection" in warning_lower:
        return DSLError(DSLErrorCode.NETWORK_ERROR, warning)
    elif "blocked" in warning_lower or "security" in warning_lower:
        return DSLError(DSLErrorCode.SECURITY_VIOLATION, warning)
    else:
        return DSLError(DSLErrorCode.BROWSER_ERROR, warning)