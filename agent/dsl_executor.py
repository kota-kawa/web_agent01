"""
Enhanced DSL Executor with Browser Use-style Element Specification

This module extends the existing DSL execution with:
- Index-based element targeting (index=N)
- Structured response format with success/error/observation/is_done
- Catalog version verification
- Robust selector resolution
- Error code standardization
- Backward compatibility
"""

from __future__ import annotations

import os
import logging
import time
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass

from .browser.vnc import execute_dsl as vnc_execute_dsl, get_url, get_html
from .element_catalog import ElementCatalogGenerator, ElementCatalog
from .browser.dom import DOMElementNode

# Configuration
INDEX_MODE = os.getenv("INDEX_MODE", "true").lower() == "true"

log = logging.getLogger(__name__)


@dataclass
class DSLResponse:
    """Structured DSL response format"""
    success: bool
    error: Optional[Dict[str, Any]] = None
    observation: Optional[Dict[str, Any]] = None
    is_done: bool = False
    complete: bool = False  # Backward compatibility
    html: str = ""
    warnings: List[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        result = {
            "success": self.success,
            "html": self.html,
            "warnings": self.warnings or []
        }
        
        if self.error:
            result["error"] = self.error
        
        if self.observation:
            result["observation"] = self.observation
        
        # Include both for compatibility
        result["is_done"] = self.is_done
        result["complete"] = self.complete
        
        return result


class EnhancedDSLExecutor:
    """Enhanced DSL executor with index-based targeting and robust execution"""
    
    def __init__(self):
        self.catalog_generator = ElementCatalogGenerator()
        self.current_catalog: Optional[ElementCatalog] = None
        self.element_cache: Dict[str, Any] = {}
    
    def execute_dsl(
        self,
        payload: Dict[str, Any],
        expected_catalog_version: Optional[str] = None,
        timeout: int = 120
    ) -> DSLResponse:
        """
        Execute DSL with enhanced features and structured response
        
        Args:
            payload: DSL payload with actions
            expected_catalog_version: Expected catalog version for consistency check
            timeout: Execution timeout in seconds
        
        Returns:
            DSLResponse with structured result
        """
        try:
            # Check if INDEX_MODE is enabled
            if not INDEX_MODE:
                return self._execute_legacy_dsl(payload, timeout)
            
            # Preprocess actions for index-based targeting
            processed_payload = self._preprocess_actions(payload, expected_catalog_version)
            
            if "error" in processed_payload:
                return DSLResponse(
                    success=False,
                    error=processed_payload["error"],
                    observation=self._generate_observation()
                )
            
            # Execute actions
            result = vnc_execute_dsl(processed_payload, timeout)
            
            # Generate structured response
            return self._create_structured_response(result, payload)
            
        except Exception as e:
            log.exception("DSL execution failed: %s", e)
            return DSLResponse(
                success=False,
                error={
                    "code": "EXECUTION_ERROR",
                    "message": f"DSL execution failed: {str(e)}",
                    "details": {"exception": str(type(e).__name__)}
                },
                observation=self._generate_observation()
            )
    
    def _execute_legacy_dsl(self, payload: Dict[str, Any], timeout: int) -> DSLResponse:
        """Execute DSL in legacy mode (INDEX_MODE=false)"""
        result = vnc_execute_dsl(payload, timeout)
        
        # Convert legacy response to structured format
        success = "error" not in result
        error = None
        
        if not success:
            error = {
                "code": "LEGACY_ERROR",
                "message": result.get("error", "Unknown error"),
                "details": {}
            }
        
        # Check for complete flag
        complete = payload.get("complete", False)
        
        return DSLResponse(
            success=success,
            error=error,
            observation=self._generate_observation(),
            is_done=complete,
            complete=complete,
            html=result.get("html", ""),
            warnings=result.get("warnings", [])
        )
    
    def _preprocess_actions(
        self,
        payload: Dict[str, Any],
        expected_catalog_version: Optional[str] = None
    ) -> Dict[str, Any]:
        """Preprocess actions to resolve index-based targets"""
        actions = payload.get("actions", [])
        processed_actions = []
        
        # Update catalog if needed
        catalog_update_result = self._update_catalog_if_needed()
        if catalog_update_result.get("error"):
            return catalog_update_result
        
        # Verify catalog version if expected
        if expected_catalog_version and self.current_catalog:
            if self.current_catalog.version != expected_catalog_version:
                return {
                    "error": {
                        "code": "CATALOG_OUTDATED",
                        "message": "Catalog version mismatch. Please execute refresh_catalog action.",
                        "details": {
                            "expected": expected_catalog_version,
                            "current": self.current_catalog.version
                        }
                    }
                }
        
        # Process each action
        for action in actions:
            processed_action = self._process_action(action)
            if "error" in processed_action:
                return processed_action
            processed_actions.append(processed_action["action"])
        
        return {**payload, "actions": processed_actions}
    
    def _process_action(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single action, resolving index-based targets"""
        action_type = action.get("action", "")
        
        # Handle new auxiliary actions
        if action_type == "refresh_catalog":
            return self._handle_refresh_catalog(action)
        elif action_type == "scroll_to_text":
            return self._handle_scroll_to_text(action)
        elif action_type in ["wait_network", "wait_selector", "wait_timeout"]:
            return self._handle_enhanced_wait(action)
        
        # Process target if present
        target = action.get("target")
        if target and isinstance(target, str) and target.startswith("index="):
            resolved_target = self._resolve_index_target(target)
            if "error" in resolved_target:
                return resolved_target
            
            # Replace target with resolved selector
            processed_action = action.copy()
            processed_action["target"] = resolved_target["selector"]
            return {"action": processed_action}
        
        # Return action as-is for backward compatibility
        return {"action": action}
    
    def _resolve_index_target(self, target: str) -> Dict[str, Any]:
        """Resolve index=N target to robust selectors"""
        try:
            # Parse index from target (format: "index=N")
            index_str = target.split("=", 1)[1]
            index = int(index_str)
        except (ValueError, IndexError):
            return {
                "error": {
                    "code": "INVALID_INDEX",
                    "message": f"Invalid index format: {target}. Expected 'index=N' where N is a number.",
                    "details": {"target": target}
                }
            }
        
        if not self.current_catalog:
            return {
                "error": {
                    "code": "CATALOG_NOT_AVAILABLE",
                    "message": "Element catalog not available. Please refresh the catalog first.",
                    "details": {"index": index}
                }
            }
        
        # Look up element in catalog
        if index not in self.current_catalog.index_map:
            return {
                "error": {
                    "code": "ELEMENT_NOT_FOUND",
                    "message": f"Element with index {index} not found in catalog.",
                    "details": {
                        "index": index,
                        "available_indices": list(self.current_catalog.index_map.keys())
                    }
                }
            }
        
        element = self.current_catalog.index_map[index]
        
        # Check if element is interactable
        if element.disabled:
            return {
                "error": {
                    "code": "ELEMENT_NOT_INTERACTABLE", 
                    "message": f"Element at index {index} is disabled.",
                    "details": {
                        "index": index,
                        "label": element.primary_label,
                        "state": element.state_hint
                    }
                }
            }
        
        if not element.visible:
            return {
                "error": {
                    "code": "ELEMENT_NOT_VISIBLE",
                    "message": f"Element at index {index} is not visible.",
                    "details": {
                        "index": index,
                        "label": element.primary_label
                    }
                }
            }
        
        # Return first robust selector (they are ordered by reliability)
        if element.robust_selectors:
            return {"selector": element.robust_selectors[0]}
        
        return {
            "error": {
                "code": "NO_SELECTOR_AVAILABLE",
                "message": f"No selector available for element at index {index}.",
                "details": {
                    "index": index,
                    "label": element.primary_label
                }
            }
        }
    
    def _handle_refresh_catalog(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """Handle refresh_catalog action"""
        try:
            self._force_catalog_refresh()
            # Convert to a no-op action that the executor can handle
            return {"action": {"action": "eval_js", "script": "true"}}
        except Exception as e:
            return {
                "error": {
                    "code": "CATALOG_REFRESH_FAILED",
                    "message": f"Failed to refresh catalog: {str(e)}",
                    "details": {}
                }
            }
    
    def _handle_scroll_to_text(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """Handle scroll_to_text action"""
        text = action.get("text", "")
        if not text:
            return {
                "error": {
                    "code": "INVALID_PARAMETER",
                    "message": "scroll_to_text requires 'text' parameter.",
                    "details": {"action": action}
                }
            }
        
        # Convert to scroll action with text selector
        return {
            "action": {
                "action": "scroll",
                "target": f"text={text}",
                "direction": "down",
                "amount": 200
            }
        }
    
    def _handle_enhanced_wait(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """Handle enhanced wait actions"""
        action_type = action.get("action", "")
        
        if action_type == "wait_network":
            # Convert to wait action
            return {
                "action": {
                    "action": "wait",
                    "ms": action.get("timeout", 3000)
                }
            }
        elif action_type == "wait_selector":
            # Convert to wait_for_selector
            return {
                "action": {
                    "action": "wait_for_selector",
                    "target": action.get("selector", "body"),
                    "ms": action.get("timeout", 3000)
                }
            }
        elif action_type == "wait_timeout":
            # Convert to wait
            return {
                "action": {
                    "action": "wait",
                    "ms": action.get("timeout", 1000)
                }
            }
        
        return {"action": action}
    
    def _update_catalog_if_needed(self) -> Dict[str, Any]:
        """Update element catalog if needed"""
        try:
            # Get current page info
            url = get_url()
            
            # Generate catalog from current DOM
            # Note: In a full implementation, this would get DOM elements from the browser
            # For now, we'll create a placeholder catalog
            self.current_catalog = self._generate_placeholder_catalog(url)
            
            return {"success": True}
            
        except Exception as e:
            log.exception("Failed to update catalog: %s", e)
            return {
                "error": {
                    "code": "CATALOG_UPDATE_FAILED",
                    "message": f"Failed to update element catalog: {str(e)}",
                    "details": {}
                }
            }
    
    def _force_catalog_refresh(self):
        """Force refresh of the element catalog"""
        self.current_catalog = None
        self.element_cache.clear()
        self._update_catalog_if_needed()
    
    def _generate_placeholder_catalog(self, url: str) -> ElementCatalog:
        """Generate a placeholder catalog for testing (would be replaced with real DOM processing)"""
        from .element_catalog import ElementCatalog
        
        # This is a placeholder - in real implementation this would:
        # 1. Get DOM elements from browser
        # 2. Process them through ElementCatalogGenerator
        # 3. Return real catalog
        
        return ElementCatalog(
            version="placeholder_v1",
            url=url,
            title="Placeholder Page",
            short_summary="Placeholder catalog for testing",
            nav_detected=False,
            abbreviated_entries=[],
            index_map={}
        )
    
    def _create_structured_response(
        self,
        result: Dict[str, Any],
        original_payload: Dict[str, Any]
    ) -> DSLResponse:
        """Create structured response from execution result"""
        # Determine success
        success = "error" not in result and not any(
            w.startswith("ERROR:") for w in result.get("warnings", [])
        )
        
        # Extract error information
        error = None
        if not success:
            error = self._extract_error_from_result(result)
        
        # Generate observation
        observation = self._generate_observation()
        
        # Determine completion status
        complete = original_payload.get("complete", False)
        is_done = complete  # For now, same as complete
        
        return DSLResponse(
            success=success,
            error=error,
            observation=observation,
            is_done=is_done,
            complete=complete,
            html=result.get("html", ""),
            warnings=result.get("warnings", [])
        )
    
    def _extract_error_from_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Extract and categorize error information from result"""
        if "error" in result:
            message = result["error"]
        else:
            # Look for error in warnings
            error_warnings = [w for w in result.get("warnings", []) if w.startswith("ERROR:")]
            message = error_warnings[0] if error_warnings else "Unknown error"
        
        # Categorize error
        error_code = self._categorize_error(message)
        
        return {
            "code": error_code,
            "message": message,
            "details": {}
        }
    
    def _categorize_error(self, message: str) -> str:
        """Categorize error message into standard error codes"""
        message_lower = message.lower()
        
        if "timeout" in message_lower or "timed out" in message_lower:
            return "NAVIGATION_TIMEOUT"
        elif "element not found" in message_lower or "locator" in message_lower:
            return "ELEMENT_NOT_FOUND"
        elif "not clickable" in message_lower or "not interactable" in message_lower:
            return "ELEMENT_NOT_INTERACTABLE"
        elif "catalog" in message_lower and "outdated" in message_lower:
            return "CATALOG_OUTDATED"
        elif "unsupported" in message_lower:
            return "UNSUPPORTED_ACTION"
        else:
            return "EXECUTION_ERROR"
    
    def _generate_observation(self) -> Dict[str, Any]:
        """Generate observation data for the current page state"""
        try:
            url = get_url()
            
            # Basic observation - in real implementation would include more details
            observation = {
                "url": url,
                "title": "Current Page",  # Would extract from DOM
                "short_summary": "Page observation",
                "nav_detected": False
            }
            
            if self.current_catalog:
                observation["catalog_version"] = self.current_catalog.version
                observation["short_summary"] = self.current_catalog.short_summary
                observation["nav_detected"] = self.current_catalog.nav_detected
            
            return observation
            
        except Exception as e:
            log.exception("Failed to generate observation: %s", e)
            return {
                "url": "",
                "title": "Unknown",
                "short_summary": "Failed to generate observation",
                "nav_detected": False
            }
    
    def get_current_catalog(self) -> Optional[ElementCatalog]:
        """Get the current element catalog"""
        return self.current_catalog
    
    def get_abbreviated_catalog(self) -> List[Dict[str, Any]]:
        """Get abbreviated catalog for LLM consumption"""
        if not self.current_catalog:
            return []
        return self.current_catalog.abbreviated_entries


# Global executor instance
_executor = EnhancedDSLExecutor()


def execute_enhanced_dsl(
    payload: Dict[str, Any],
    expected_catalog_version: Optional[str] = None,
    timeout: int = 120
) -> Dict[str, Any]:
    """
    Execute DSL with enhanced features (main entry point)
    
    Args:
        payload: DSL payload
        expected_catalog_version: Expected catalog version
        timeout: Execution timeout
    
    Returns:
        Dictionary response with structured format
    """
    response = _executor.execute_dsl(payload, expected_catalog_version, timeout)
    return response.to_dict()


def get_element_catalog() -> Optional[ElementCatalog]:
    """Get current element catalog"""
    return _executor.get_current_catalog()


def get_abbreviated_catalog() -> List[Dict[str, Any]]:
    """Get abbreviated catalog for LLM"""
    return _executor.get_abbreviated_catalog()


def refresh_catalog() -> Dict[str, Any]:
    """Force refresh of element catalog"""
    try:
        _executor._force_catalog_refresh()
        return {"success": True, "message": "Catalog refreshed successfully"}
    except Exception as e:
        return {"success": False, "error": str(e)}