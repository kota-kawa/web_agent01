"""
Action DSL Processor for LLM Commands

Handles JSON-based action commands from LLM and executes them on the browser.
Supports click, type, scroll operations with stable reference resolution.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Union
from enum import Enum

from playwright.async_api import Page
from .data_supply_stack import DataSupplyStack, ReferenceId, ExtractionMetrics

logger = logging.getLogger(__name__)


class ActionType(Enum):
    """Supported action types."""
    PLAN = "plan"
    ACT = "act"
    ASK = "ask"
    RETRY = "retry"


class OperationType(Enum):
    """Supported operation types."""
    CLICK = "click"
    TYPE = "type"
    SCROLL = "scroll"
    WAIT = "wait"
    NAVIGATE = "navigate"


@dataclass
class ActionCommand:
    """Single action command."""
    op: str
    target: Optional[str] = None
    text: Optional[str] = None
    direction: Optional[str] = None
    amount: Optional[int] = None
    url: Optional[str] = None
    timeout: Optional[int] = None


@dataclass
class ActionRequest:
    """Complete action request from LLM."""
    type: str
    actions: Optional[List[ActionCommand]] = None
    message: Optional[str] = None
    retry_reason: Optional[str] = None


@dataclass
class ActionResult:
    """Result of action execution."""
    success: bool
    message: str
    error_code: Optional[str] = None
    updated_targets: Optional[List[str]] = None


@dataclass
class ValidationError:
    """Action validation error."""
    target: str
    reason: str
    error_code: str


class ActionProcessor:
    """Processes and executes LLM action commands."""
    
    def __init__(self, data_stack: DataSupplyStack):
        self.data_stack = data_stack
        self.page: Optional[Page] = None
        self.last_snapshot: Optional[Dict[str, Any]] = None
        self.execution_metrics = ExtractionMetrics()
        
    async def initialize(self, page: Page) -> None:
        """Initialize with Playwright page."""
        self.page = page
    
    async def process_action_request(self, request_json: str) -> Dict[str, Any]:
        """Process JSON action request from LLM."""
        try:
            request_data = json.loads(request_json)
            request = ActionRequest(**request_data)
            
            if request.type == ActionType.PLAN.value:
                return await self._handle_plan_request(request)
            elif request.type == ActionType.ACT.value:
                return await self._handle_act_request(request)
            elif request.type == ActionType.ASK.value:
                return await self._handle_ask_request(request)
            elif request.type == ActionType.RETRY.value:
                return await self._handle_retry_request(request)
            else:
                return self._create_error_response(f"Unknown action type: {request.type}")
                
        except json.JSONDecodeError as e:
            return self._create_error_response(f"Invalid JSON: {e}")
        except Exception as e:
            logger.error(f"Action processing failed: {e}")
            return self._create_error_response(f"Processing error: {e}")
    
    async def _handle_plan_request(self, request: ActionRequest) -> Dict[str, Any]:
        """Handle planning request - return current state."""
        try:
            # Extract current page state
            formats = await self.data_stack.extract_all_formats(staged=True)
            
            return {
                "type": "plan_response",
                "success": True,
                "current_state": formats,
                "message": "Current page state extracted"
            }
            
        except Exception as e:
            return self._create_error_response(f"Failed to extract page state: {e}")
    
    async def _handle_act_request(self, request: ActionRequest) -> Dict[str, Any]:
        """Handle action execution request."""
        if not request.actions:
            return self._create_error_response("No actions provided")
        
        # Validate all actions first
        validation_errors = await self._validate_actions(request.actions)
        if validation_errors:
            return self._create_validation_error_response(validation_errors)
        
        # Execute actions sequentially
        results = []
        for action in request.actions:
            result = await self._execute_single_action(action)
            results.append(result)
            
            # Stop on first failure
            if not result.success:
                break
        
        # Update metrics
        successful_actions = sum(1 for r in results if r.success)
        self.execution_metrics.click_success_rate = successful_actions / len(results) if results else 0
        
        return {
            "type": "act_response",
            "success": all(r.success for r in results),
            "results": [self._result_to_dict(r) for r in results],
            "metrics": self.execution_metrics
        }
    
    async def _handle_ask_request(self, request: ActionRequest) -> Dict[str, Any]:
        """Handle ask request - clarification needed."""
        return {
            "type": "ask_response",
            "success": True,
            "message": request.message or "Clarification request received"
        }
    
    async def _handle_retry_request(self, request: ActionRequest) -> Dict[str, Any]:
        """Handle retry request - re-extract page state."""
        try:
            # Re-extract all formats to get updated state
            formats = await self.data_stack.extract_all_formats(staged=False)
            self.last_snapshot = formats
            
            self.execution_metrics.retry_count += 1
            
            return {
                "type": "retry_response", 
                "success": True,
                "updated_state": formats,
                "message": f"Page state re-extracted due to: {request.retry_reason or 'unknown reason'}"
            }
            
        except Exception as e:
            return self._create_error_response(f"Failed to re-extract state: {e}")
    
    async def _validate_actions(self, actions: List[ActionCommand]) -> List[ValidationError]:
        """Validate all actions against current page state."""
        errors = []
        
        # Get current page state for validation
        try:
            current_state = await self.data_stack.extract_all_formats(staged=True)
            self.last_snapshot = current_state
        except Exception as e:
            logger.error(f"Failed to get current state for validation: {e}")
            return [ValidationError("SYSTEM", f"Failed to get page state: {e}", "EXTRACT_ERROR")]
        
        for action in actions:
            if action.target:
                error = await self._validate_target_reference(action.target, current_state)
                if error:
                    errors.append(error)
            
            # Validate operation-specific requirements
            error = self._validate_operation(action)
            if error:
                errors.append(error)
        
        return errors
    
    async def _validate_target_reference(self, target: str, current_state: Dict[str, Any]) -> Optional[ValidationError]:
        """Validate that target reference exists and is actionable."""
        try:
            ref_id = ReferenceId.from_string(target)
        except ValueError as e:
            return ValidationError(target, f"Invalid reference format: {e}", "INVALID_FORMAT")
        
        # Check if target exists in current state
        target_found = False
        target_visible = False
        target_clickable = False
        
        # Check IDX-Text index_map
        idx_text = current_state.get("idx_text")
        if idx_text and hasattr(idx_text, "index_map"):
            for idx, ref_info in idx_text.index_map.items():
                if (ref_info.get("frameId") == ref_id.frame_id and 
                    ref_info.get("backendNodeId") == ref_id.backend_node_id):
                    target_found = True
                    target_visible = True  # Elements in index_map are visible by definition
                    target_clickable = True  # And interactive
                    break
        
        # Check AX-Slim nodes
        if not target_found:
            ax_slim = current_state.get("ax_slim")
            if ax_slim and hasattr(ax_slim, "ax_nodes"):
                for node in ax_slim.ax_nodes:
                    if (node.get("backendNodeId") == ref_id.backend_node_id or
                        node.get("axId") == f"AX-{ref_id.ax_node_id}"):
                        target_found = True
                        target_visible = node.get("visible", False)
                        target_clickable = True  # AX nodes are interactive by definition
                        break
        
        # Check DOM-Lite nodes
        if not target_found:
            dom_lite = current_state.get("dom_lite")
            if dom_lite and hasattr(dom_lite, "nodes"):
                for node in dom_lite.nodes:
                    if node.backend_node_id == ref_id.backend_node_id:
                        target_found = True
                        target_visible = node.bbox != [0, 0, 0, 0]
                        target_clickable = node.clickable
                        break
        
        if not target_found:
            self.execution_metrics.not_found_rate += 1
            return ValidationError(target, "Target element not found in current page state", "NOT_FOUND")
        
        if not target_visible:
            return ValidationError(target, "Target element is not visible", "NOT_VISIBLE")
        
        if not target_clickable:
            return ValidationError(target, "Target element is not clickable", "NOT_CLICKABLE")
        
        return None
    
    def _validate_operation(self, action: ActionCommand) -> Optional[ValidationError]:
        """Validate operation-specific requirements."""
        op = action.op.lower()
        
        if op == OperationType.CLICK.value:
            if not action.target:
                return ValidationError("", "Click operation requires target", "MISSING_TARGET")
        
        elif op == OperationType.TYPE.value:
            if not action.target:
                return ValidationError("", "Type operation requires target", "MISSING_TARGET")
            if not action.text:
                return ValidationError(action.target or "", "Type operation requires text", "MISSING_TEXT")
        
        elif op == OperationType.SCROLL.value:
            if not action.direction:
                return ValidationError("", "Scroll operation requires direction", "MISSING_DIRECTION")
            if action.direction not in ["up", "down", "left", "right"]:
                return ValidationError("", f"Invalid scroll direction: {action.direction}", "INVALID_DIRECTION")
            if action.amount is not None and action.amount <= 0:
                return ValidationError("", "Scroll amount must be positive", "INVALID_AMOUNT")
        
        elif op == OperationType.NAVIGATE.value:
            if not action.url:
                return ValidationError("", "Navigate operation requires URL", "MISSING_URL")
        
        elif op == OperationType.WAIT.value:
            if action.timeout is not None and action.timeout <= 0:
                return ValidationError("", "Wait timeout must be positive", "INVALID_TIMEOUT")
        
        else:
            return ValidationError("", f"Unknown operation type: {op}", "UNKNOWN_OPERATION")
        
        return None
    
    async def _execute_single_action(self, action: ActionCommand) -> ActionResult:
        """Execute a single action command."""
        if not self.page:
            return ActionResult(False, "Page not initialized", "NO_PAGE")
        
        try:
            op = action.op.lower()
            
            if op == OperationType.CLICK.value:
                return await self._execute_click(action)
            elif op == OperationType.TYPE.value:
                return await self._execute_type(action)
            elif op == OperationType.SCROLL.value:
                return await self._execute_scroll(action)
            elif op == OperationType.WAIT.value:
                return await self._execute_wait(action)
            elif op == OperationType.NAVIGATE.value:
                return await self._execute_navigate(action)
            else:
                return ActionResult(False, f"Unknown operation: {op}", "UNKNOWN_OP")
                
        except Exception as e:
            logger.error(f"Action execution failed: {e}")
            return ActionResult(False, f"Execution error: {e}", "EXECUTION_ERROR")
    
    async def _execute_click(self, action: ActionCommand) -> ActionResult:
        """Execute click action."""
        try:
            element = await self._resolve_target_to_element(action.target)
            if not element:
                return ActionResult(False, f"Could not resolve target: {action.target}", "TARGET_RESOLUTION_FAILED")
            
            await element.click()
            
            # Wait for potential navigation/changes
            await asyncio.sleep(0.5)
            
            return ActionResult(True, f"Successfully clicked {action.target}")
            
        except Exception as e:
            return ActionResult(False, f"Click failed: {e}", "CLICK_FAILED")
    
    async def _execute_type(self, action: ActionCommand) -> ActionResult:
        """Execute type action."""
        try:
            element = await self._resolve_target_to_element(action.target)
            if not element:
                return ActionResult(False, f"Could not resolve target: {action.target}", "TARGET_RESOLUTION_FAILED")
            
            # Clear existing content first
            await element.click()
            await element.fill("")
            
            # Type new content
            await element.type(action.text)
            
            return ActionResult(True, f"Successfully typed '{action.text}' into {action.target}")
            
        except Exception as e:
            return ActionResult(False, f"Type failed: {e}", "TYPE_FAILED")
    
    async def _execute_scroll(self, action: ActionCommand) -> ActionResult:
        """Execute scroll action."""
        try:
            amount = action.amount or 800
            
            if action.direction == "down":
                await self.page.mouse.wheel(0, amount)
            elif action.direction == "up":
                await self.page.mouse.wheel(0, -amount)
            elif action.direction == "right":
                await self.page.mouse.wheel(amount, 0)
            elif action.direction == "left":
                await self.page.mouse.wheel(-amount, 0)
            
            # Wait for scroll to complete
            await asyncio.sleep(0.3)
            
            return ActionResult(True, f"Successfully scrolled {action.direction} by {amount}px")
            
        except Exception as e:
            return ActionResult(False, f"Scroll failed: {e}", "SCROLL_FAILED")
    
    async def _execute_wait(self, action: ActionCommand) -> ActionResult:
        """Execute wait action."""
        try:
            timeout = action.timeout or 1000
            await asyncio.sleep(timeout / 1000.0)
            
            return ActionResult(True, f"Waited {timeout}ms")
            
        except Exception as e:
            return ActionResult(False, f"Wait failed: {e}", "WAIT_FAILED")
    
    async def _execute_navigate(self, action: ActionCommand) -> ActionResult:
        """Execute navigate action."""
        try:
            await self.page.goto(action.url)
            await self.page.wait_for_load_state("networkidle")
            
            return ActionResult(True, f"Successfully navigated to {action.url}")
            
        except Exception as e:
            return ActionResult(False, f"Navigation failed: {e}", "NAVIGATION_FAILED")
    
    async def _resolve_target_to_element(self, target: str):
        """Resolve target reference to Playwright element."""
        if not target or not self.page:
            return None
        
        try:
            ref_id = ReferenceId.from_string(target)
        except ValueError:
            return None
        
        # Try to find element using stable reference
        if self.last_snapshot:
            # Check IDX-Text index_map for CSS selector
            idx_text = self.last_snapshot.get("idx_text")
            if idx_text and hasattr(idx_text, "index_map"):
                for idx, ref_info in idx_text.index_map.items():
                    if (ref_info.get("frameId") == ref_id.frame_id and 
                        ref_info.get("backendNodeId") == ref_id.backend_node_id):
                        css = ref_info.get("css")
                        if css:
                            try:
                                element = await self.page.query_selector(css)
                                if element:
                                    return element
                            except:
                                pass
        
        # Fallback: try common selectors based on backend node ID
        fallback_selectors = [
            f"[data-backend-node-id='{ref_id.backend_node_id}']",
            f"*:nth-child({ref_id.backend_node_id % 100})",  # Rough approximation
        ]
        
        for selector in fallback_selectors:
            try:
                element = await self.page.query_selector(selector)
                if element:
                    return element
            except:
                continue
        
        return None
    
    def _create_error_response(self, message: str) -> Dict[str, Any]:
        """Create error response."""
        return {
            "type": "error",
            "success": False,
            "message": message
        }
    
    def _create_validation_error_response(self, errors: List[ValidationError]) -> Dict[str, Any]:
        """Create validation error response."""
        error_details = [
            {
                "target": error.target,
                "reason": error.reason,
                "error_code": error.error_code
            }
            for error in errors
        ]
        
        return {
            "type": "validation_error",
            "success": False,
            "errors": error_details,
            "message": f"Validation failed for {len(errors)} action(s)"
        }
    
    def _result_to_dict(self, result: ActionResult) -> Dict[str, Any]:
        """Convert ActionResult to dictionary."""
        data = {
            "success": result.success,
            "message": result.message
        }
        
        if result.error_code:
            data["error_code"] = result.error_code
        
        if result.updated_targets:
            data["updated_targets"] = result.updated_targets
        
        return data


# Example usage functions for testing
async def create_sample_actions() -> List[Dict[str, Any]]:
    """Create sample action commands for testing."""
    return [
        {
            "type": "act",
            "actions": [
                {"op": "click", "target": "F0:BN-812346"},
                {"op": "type", "target": "F0:BN-812345", "text": "ノートPC"},
                {"op": "scroll", "direction": "down", "amount": 800}
            ]
        },
        {
            "type": "plan"
        },
        {
            "type": "retry",
            "retry_reason": "Target element became stale"
        }
    ]