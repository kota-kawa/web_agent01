"""
Index-based target resolution and structured response handling.

This module provides:
1. Polymorphic target resolution (index=N, css=..., xpath=...)
2. Robust selector execution with fallback
3. Structured response format
4. Catalog version validation
"""
import os
import logging
from typing import Dict, Any, Optional, List, Union
from dataclasses import dataclass
from enum import Enum

from agent.element_catalog import ElementCatalog, get_catalog_generator

log = logging.getLogger(__name__)


class ErrorCode(Enum):
    """Standard error codes for structured responses."""
    ELEMENT_NOT_FOUND = "ELEMENT_NOT_FOUND"
    ELEMENT_NOT_INTERACTABLE = "ELEMENT_NOT_INTERACTABLE"
    CATALOG_OUTDATED = "CATALOG_OUTDATED"
    NAVIGATION_TIMEOUT = "NAVIGATION_TIMEOUT"
    UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"
    DOMAIN_NOT_ALLOWED = "DOMAIN_NOT_ALLOWED"


@dataclass
class StructuredError:
    """Structured error information."""
    code: ErrorCode
    message: str
    details: Optional[Dict[str, Any]] = None


@dataclass
class Observation:
    """Page observation information."""
    url: str
    title: str
    short_summary: str
    catalog_version: Optional[str] = None
    nav_detected: bool = False


@dataclass
class StructuredResponse:
    """Complete structured response format."""
    success: bool
    error: Optional[StructuredError] = None
    observation: Optional[Observation] = None
    is_done: bool = False
    complete: bool = False  # Backward compatibility
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            "success": self.success,
            "is_done": self.is_done,
            "complete": self.complete
        }
        
        if self.error:
            result["error"] = {
                "code": self.error.code.value,
                "message": self.error.message
            }
            if self.error.details:
                result["error"]["details"] = self.error.details
        else:
            result["error"] = None
        
        if self.observation:
            result["observation"] = {
                "url": self.observation.url,
                "title": self.observation.title,
                "short_summary": self.observation.short_summary,
                "nav_detected": self.observation.nav_detected
            }
            if self.observation.catalog_version:
                result["observation"]["catalog_version"] = self.observation.catalog_version
        
        return result


class IndexResolver:
    """Resolves index-based targets to robust selectors."""
    
    def __init__(self):
        self.current_catalog: Optional[ElementCatalog] = None
        self.index_mode_enabled = self._get_index_mode_setting()
    
    def _get_index_mode_setting(self) -> bool:
        """Get INDEX_MODE setting from environment or default to True."""
        return os.getenv("INDEX_MODE", "true").lower() in ("true", "1", "yes", "on")
    
    def set_catalog(self, catalog: ElementCatalog):
        """Set the current element catalog."""
        self.current_catalog = catalog
    
    def resolve_target(self, target: str, expected_catalog_version: Optional[str] = None) -> Dict[str, Any]:
        """Resolve target specification to executable selectors.
        
        Args:
            target: Target specification (index=N, css=..., xpath=...)
            expected_catalog_version: Expected catalog version for validation
            
        Returns:
            Dictionary with resolved selector info or error
        """
        # Parse target type
        target_info = self._parse_target(target)
        
        if target_info["type"] == "index":
            return self._resolve_index_target(target_info["value"], expected_catalog_version)
        elif target_info["type"] in ["css", "xpath"]:
            # Backward compatibility - pass through existing selectors
            return {
                "success": True,
                "selector_type": target_info["type"],
                "selector": target_info["value"],
                "fallback_selectors": []
            }
        else:
            return {
                "success": False,
                "error": StructuredError(
                    code=ErrorCode.UNSUPPORTED_ACTION,
                    message=f"Unsupported target format: {target}",
                    details={"target": target}
                )
            }
    
    def _parse_target(self, target: str) -> Dict[str, Any]:
        """Parse target specification to determine type and value."""
        target = target.strip()
        
        # Check for index format
        if target.startswith("index="):
            try:
                index_value = int(target[6:])
                return {"type": "index", "value": index_value}
            except ValueError:
                return {"type": "unknown", "value": target}
        
        # Check for CSS format
        if target.startswith("css="):
            return {"type": "css", "value": target[4:]}
        
        # Check for XPath format
        if target.startswith("xpath="):
            return {"type": "xpath", "value": target[6:]}
        
        # Legacy format - assume CSS if no prefix
        if target.startswith("/") or target.startswith("//"):
            return {"type": "xpath", "value": target}
        else:
            return {"type": "css", "value": target}
    
    def _resolve_index_target(self, index: int, expected_catalog_version: Optional[str]) -> Dict[str, Any]:
        """Resolve index-based target to robust selectors."""
        if not self.index_mode_enabled:
            return {
                "success": False,
                "error": StructuredError(
                    code=ErrorCode.UNSUPPORTED_ACTION,
                    message="Index mode is disabled. Use css= or xpath= targets instead.",
                    details={"index_mode_enabled": False}
                )
            }
        
        if not self.current_catalog:
            return {
                "success": False,
                "error": StructuredError(
                    code=ErrorCode.CATALOG_OUTDATED,
                    message="No element catalog available. Please execute refresh_catalog first.",
                    details={"catalog_available": False}
                )
            }
        
        # Validate catalog version if provided
        if expected_catalog_version and expected_catalog_version != self.current_catalog.catalog_version:
            return {
                "success": False,
                "error": StructuredError(
                    code=ErrorCode.CATALOG_OUTDATED,
                    message="Element catalog is outdated. Please execute refresh_catalog to get updated indices.",
                    details={
                        "expected_version": expected_catalog_version,
                        "current_version": self.current_catalog.catalog_version
                    }
                )
            }
        
        # Find element by index
        if index not in self.current_catalog.full_view:
            return {
                "success": False,
                "error": StructuredError(
                    code=ErrorCode.ELEMENT_NOT_FOUND,
                    message=f"Element with index {index} not found in catalog.",
                    details={
                        "requested_index": index,
                        "available_indices": list(self.current_catalog.full_view.keys())
                    }
                )
            }
        
        element = self.current_catalog.full_view[index]
        
        # Check if element is interactable
        if element.disabled:
            return {
                "success": False,
                "error": StructuredError(
                    code=ErrorCode.ELEMENT_NOT_INTERACTABLE,
                    message=f"Element at index {index} is disabled.",
                    details={
                        "index": index,
                        "element_info": {
                            "tag": element.tag,
                            "role": element.role,
                            "primary_label": element.primary_label
                        }
                    }
                )
            }
        
        return {
            "success": True,
            "index": index,
            "element_info": element,
            "robust_selectors": element.robust_selectors,
            "primary_selector": element.robust_selectors[0] if element.robust_selectors else None,
            "fallback_selectors": element.robust_selectors[1:] if len(element.robust_selectors) > 1 else []
        }


class StructuredExecutor:
    """Executes actions with structured response handling."""
    
    def __init__(self):
        self.index_resolver = IndexResolver()
        self.domain_allowlist = self._get_domain_allowlist()
    
    def _get_domain_allowlist(self) -> Optional[List[str]]:
        """Get allowed domains from configuration."""
        allowed_domains = os.getenv("ALLOWED_DOMAINS")
        if allowed_domains:
            return [domain.strip() for domain in allowed_domains.split(",")]
        return None
    
    def execute_action_with_structure(
        self, 
        action: Dict[str, Any], 
        execute_func, 
        current_url: str = "",
        current_title: str = "",
        expected_catalog_version: Optional[str] = None
    ) -> StructuredResponse:
        """Execute action with structured response format."""
        try:
            # Check domain allowlist if configured
            if self.domain_allowlist and current_url:
                if not self._is_domain_allowed(current_url):
                    return StructuredResponse(
                        success=False,
                        error=StructuredError(
                            code=ErrorCode.DOMAIN_NOT_ALLOWED,
                            message=f"Domain not in allowlist: {current_url}",
                            details={"url": current_url, "allowed_domains": self.domain_allowlist}
                        )
                    )
            
            # Handle special new actions
            action_type = action.get("action", "").lower()
            
            if action_type == "refresh_catalog":
                return self._handle_refresh_catalog(current_url, current_title)
            elif action_type == "scroll_to_text":
                return self._handle_scroll_to_text(action, execute_func, current_url, current_title)
            elif action_type == "wait" and "until" in action:
                return self._handle_wait_until(action, execute_func, current_url, current_title)
            
            # Handle target resolution for existing actions
            if "target" in action:
                target_result = self.index_resolver.resolve_target(
                    action["target"], 
                    expected_catalog_version
                )
                
                if not target_result["success"]:
                    return StructuredResponse(
                        success=False,
                        error=target_result["error"]
                    )
                
                # If using index, update action with resolved selector
                if "index" in target_result:
                    # Use primary selector for execution
                    primary_selector = target_result["primary_selector"]
                    if primary_selector:
                        action = dict(action)  # Create copy
                        action["target"] = primary_selector
            
            # Execute the action
            result = execute_func({"actions": [action]})
            
            # Create observation
            observation = Observation(
                url=current_url,
                title=current_title,
                short_summary=f"Executed {action_type}",
                catalog_version=self.index_resolver.current_catalog.catalog_version if self.index_resolver.current_catalog else None,
                nav_detected=self._detect_navigation(result)
            )
            
            # Check for execution errors
            if result and result.get("warnings"):
                # Look for error patterns in warnings
                error_warnings = [w for w in result["warnings"] if w.startswith("ERROR:")]
                if error_warnings:
                    return StructuredResponse(
                        success=False,
                        error=StructuredError(
                            code=ErrorCode.ELEMENT_NOT_FOUND,  # Default error code
                            message=error_warnings[0],
                            details={"warnings": result["warnings"]}
                        ),
                        observation=observation
                    )
            
            return StructuredResponse(
                success=True,
                observation=observation,
                is_done=action.get("complete", False),
                complete=action.get("complete", False)
            )
            
        except Exception as e:
            log.error("Error executing action with structure: %s", e)
            return StructuredResponse(
                success=False,
                error=StructuredError(
                    code=ErrorCode.UNSUPPORTED_ACTION,
                    message=f"Execution error: {str(e)}",
                    details={"exception_type": type(e).__name__}
                )
            )
    
    def _handle_refresh_catalog(self, current_url: str, current_title: str) -> StructuredResponse:
        """Handle refresh_catalog action."""
        try:
            # Import here to avoid circular imports
            from agent.browser.vnc import get_dom_tree
            
            dom_tree, dom_error = get_dom_tree()
            if dom_tree is None:
                return StructuredResponse(
                    success=False,
                    error=StructuredError(
                        code=ErrorCode.UNSUPPORTED_ACTION,
                        message=f"Failed to get DOM tree: {dom_error}",
                        details={"dom_error": dom_error}
                    )
                )
            
            # Generate new catalog
            generator = get_catalog_generator()
            catalog = generator.generate_catalog(dom_tree, current_url, current_title)
            
            # Update resolver
            self.index_resolver.set_catalog(catalog)
            
            observation = Observation(
                url=current_url,
                title=current_title,
                short_summary=catalog.short_summary,
                catalog_version=catalog.catalog_version,
                nav_detected=False
            )
            
            return StructuredResponse(
                success=True,
                observation=observation
            )
            
        except Exception as e:
            log.error("Error refreshing catalog: %s", e)
            return StructuredResponse(
                success=False,
                error=StructuredError(
                    code=ErrorCode.UNSUPPORTED_ACTION,
                    message=f"Failed to refresh catalog: {str(e)}"
                )
            )
    
    def _handle_scroll_to_text(
        self, 
        action: Dict[str, Any], 
        execute_func, 
        current_url: str, 
        current_title: str
    ) -> StructuredResponse:
        """Handle scroll_to_text action."""
        text = action.get("text", "")
        if not text:
            return StructuredResponse(
                success=False,
                error=StructuredError(
                    code=ErrorCode.UNSUPPORTED_ACTION,
                    message="scroll_to_text requires 'text' parameter"
                )
            )
        
        # Convert to scroll action with text-based targeting
        scroll_action = {
            "action": "scroll",
            "target": f"text={text}",
            "amount": 200
        }
        
        result = execute_func({"actions": [scroll_action]})
        
        observation = Observation(
            url=current_url,
            title=current_title,
            short_summary=f"Scrolled to text: {text[:30]}...",
            catalog_version=self.index_resolver.current_catalog.catalog_version if self.index_resolver.current_catalog else None,
            nav_detected=False
        )
        
        return StructuredResponse(
            success=True,
            observation=observation
        )
    
    def _handle_wait_until(
        self, 
        action: Dict[str, Any], 
        execute_func, 
        current_url: str, 
        current_title: str
    ) -> StructuredResponse:
        """Handle wait until action."""
        until = action.get("until", "")
        timeout = action.get("timeout", 5000)
        value = action.get("value", "")
        
        if until == "network_idle":
            wait_action = {"action": "wait", "ms": timeout}
        elif until == "selector":
            wait_action = {"action": "wait_for_selector", "target": value, "ms": timeout}
        elif until == "timeout":
            wait_action = {"action": "wait", "ms": int(value) if value else timeout}
        else:
            return StructuredResponse(
                success=False,
                error=StructuredError(
                    code=ErrorCode.UNSUPPORTED_ACTION,
                    message=f"Unsupported wait condition: {until}"
                )
            )
        
        result = execute_func({"actions": [wait_action]})
        
        observation = Observation(
            url=current_url,
            title=current_title,
            short_summary=f"Waited for {until}",
            catalog_version=self.index_resolver.current_catalog.catalog_version if self.index_resolver.current_catalog else None,
            nav_detected=False
        )
        
        return StructuredResponse(
            success=True,
            observation=observation
        )
    
    def _is_domain_allowed(self, url: str) -> bool:
        """Check if URL domain is in allowlist."""
        if not self.domain_allowlist:
            return True
        
        try:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc.lower()
            return any(allowed.lower() in domain for allowed in self.domain_allowlist)
        except:
            return False
    
    def _detect_navigation(self, result: Dict[str, Any]) -> bool:
        """Detect if navigation occurred based on execution result."""
        if not result:
            return False
        
        # Check for navigation-related warnings
        warnings = result.get("warnings", [])
        nav_indicators = ["navigation", "navigate", "url changed", "page loaded"]
        
        for warning in warnings:
            warning_lower = warning.lower()
            if any(indicator in warning_lower for indicator in nav_indicators):
                return True
        
        return False


# Global instance
_structured_executor = None


def get_structured_executor() -> StructuredExecutor:
    """Get global structured executor instance."""
    global _structured_executor
    if _structured_executor is None:
        _structured_executor = StructuredExecutor()
    return _structured_executor