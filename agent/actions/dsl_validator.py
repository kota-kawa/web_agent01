"""
Action DSL validation and execution system.

Handles LLM action requests with types: "plan", "act", "ask", "retry"
Validates targets against current DOM state and provides error handling.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Union, Any, Tuple
from dataclasses import dataclass
from enum import Enum

from ..browser.data_supply import DataSupplyManager, StableNodeRef

log = logging.getLogger(__name__)


class ActionType(Enum):
    """Supported action types from LLM."""
    PLAN = "plan"
    ACT = "act"
    ASK = "ask"
    RETRY = "retry"


class OperationType(Enum):
    """Supported operation types."""
    CLICK = "click"
    TYPE = "type"
    SCROLL = "scroll"
    HOVER = "hover"
    NAVIGATE = "navigate"
    WAIT = "wait"
    SCREENSHOT = "screenshot"


@dataclass
class ActionRequest:
    """Parsed action request from LLM."""
    type: ActionType
    actions: List[Dict[str, Any]] = None
    message: str = ""
    plan: List[str] = None
    question: str = ""


@dataclass
class ActionValidationResult:
    """Result of action validation."""
    valid: bool
    errors: List[str] = None
    warnings: List[str] = None
    normalized_actions: List[Dict[str, Any]] = None


@dataclass
class ActionExecutionResult:
    """Result of action execution."""
    success: bool
    message: str = ""
    errors: List[str] = None
    warnings: List[str] = None
    retry_suggested: bool = False
    new_snapshot_needed: bool = False


class DSLValidator:
    """Validates and normalizes LLM action requests."""
    
    def __init__(self, data_supply_manager: DataSupplyManager):
        self.data_supply = data_supply_manager
        
    def parse_request(self, request_data: Dict[str, Any]) -> ActionRequest:
        """Parse incoming LLM request."""
        try:
            action_type = ActionType(request_data.get("type", "act"))
            
            return ActionRequest(
                type=action_type,
                actions=request_data.get("actions", []),
                message=request_data.get("message", ""),
                plan=request_data.get("plan", []),
                question=request_data.get("question", "")
            )
        except ValueError as e:
            log.error(f"Invalid action type: {e}")
            return ActionRequest(
                type=ActionType.RETRY,
                message=f"Invalid action type: {request_data.get('type')}"
            )
    
    async def validate_actions(self, actions: List[Dict[str, Any]]) -> ActionValidationResult:
        """Validate a list of actions."""
        if not actions:
            return ActionValidationResult(
                valid=False,
                errors=["No actions provided"]
            )
        
        errors = []
        warnings = []
        normalized_actions = []
        
        for i, action in enumerate(actions):
            try:
                validation_result = await self._validate_single_action(action)
                
                if not validation_result.valid:
                    errors.extend([f"Action {i}: {error}" for error in validation_result.errors])
                
                if validation_result.warnings:
                    warnings.extend([f"Action {i}: {warning}" for warning in validation_result.warnings])
                
                if validation_result.normalized_actions:
                    normalized_actions.extend(validation_result.normalized_actions)
                    
            except Exception as e:
                errors.append(f"Action {i}: Validation exception: {str(e)}")
        
        return ActionValidationResult(
            valid=len(errors) == 0,
            errors=errors if errors else None,
            warnings=warnings if warnings else None,
            normalized_actions=normalized_actions if normalized_actions else None
        )
    
    async def _validate_single_action(self, action: Dict[str, Any]) -> ActionValidationResult:
        """Validate a single action."""
        errors = []
        warnings = []
        normalized_action = action.copy()
        
        # Check required fields
        op = action.get("op")
        if not op:
            errors.append("Missing 'op' field")
            return ActionValidationResult(valid=False, errors=errors)
        
        try:
            operation = OperationType(op.lower())
        except ValueError:
            errors.append(f"Unknown operation: {op}")
            return ActionValidationResult(valid=False, errors=errors)
        
        # Validate operation-specific requirements
        if operation in [OperationType.CLICK, OperationType.TYPE, OperationType.HOVER]:
            target = action.get("target")
            if not target:
                errors.append(f"Operation '{op}' requires 'target' field")
            else:
                # Validate target exists and is actionable
                is_valid, stable_ref = await self.data_supply.validate_target(target)
                if not is_valid:
                    errors.append(f"Target '{target}' not found or not actionable")
                elif stable_ref:
                    # Normalize target to stable reference format
                    normalized_action["target"] = stable_ref.to_string()
        
        if operation == OperationType.TYPE:
            if "text" not in action:
                errors.append("Type operation requires 'text' field")
        
        if operation == OperationType.SCROLL:
            direction = action.get("direction", "down")
            if direction not in ["up", "down", "left", "right"]:
                errors.append(f"Invalid scroll direction: {direction}")
            
            amount = action.get("amount", 800)
            if not isinstance(amount, int) or amount <= 0:
                warnings.append(f"Invalid scroll amount {amount}, using default 800")
                normalized_action["amount"] = 800
        
        if operation == OperationType.WAIT:
            duration = action.get("duration", action.get("ms", 500))
            if not isinstance(duration, int) or duration < 0:
                warnings.append(f"Invalid wait duration {duration}, using default 500ms")
                normalized_action["duration"] = 500
        
        if operation == OperationType.NAVIGATE:
            url = action.get("url", action.get("target"))
            if not url:
                errors.append("Navigate operation requires 'url' field")
            elif not self._is_valid_url(url):
                warnings.append(f"URL might be invalid: {url}")
        
        return ActionValidationResult(
            valid=len(errors) == 0,
            errors=errors if errors else None,
            warnings=warnings if warnings else None,
            normalized_actions=[normalized_action] if len(errors) == 0 else None
        )
    
    def _is_valid_url(self, url: str) -> bool:
        """Basic URL validation."""
        return (
            url.startswith(("http://", "https://", "data:", "about:")) or
            url.startswith("/") or  # Relative URL
            "." in url  # Domain-like
        )


class ActionExecutor:
    """Executes validated actions."""
    
    def __init__(self, data_supply_manager: DataSupplyManager):
        self.data_supply = data_supply_manager
        self.execution_stats = {
            "click_success_rate": 0.0,
            "retry_count": 0,
            "not_found_rate": 0.0,
            "total_executions": 0,
            "successful_executions": 0
        }
    
    async def execute_actions(self, actions: List[Dict[str, Any]]) -> ActionExecutionResult:
        """Execute a list of validated actions."""
        results = []
        overall_success = True
        all_errors = []
        all_warnings = []
        retry_needed = False
        snapshot_needed = False
        
        for i, action in enumerate(actions):
            try:
                result = await self._execute_single_action(action)
                results.append(result)
                
                if not result.success:
                    overall_success = False
                    if result.errors:
                        all_errors.extend([f"Action {i}: {error}" for error in result.errors])
                
                if result.warnings:
                    all_warnings.extend([f"Action {i}: {warning}" for warning in result.warnings])
                
                if result.retry_suggested:
                    retry_needed = True
                
                if result.new_snapshot_needed:
                    snapshot_needed = True
                    
            except Exception as e:
                log.error(f"Action execution failed: {e}")
                overall_success = False
                all_errors.append(f"Action {i}: Execution exception: {str(e)}")
        
        # Update stats
        self.execution_stats["total_executions"] += len(actions)
        if overall_success:
            self.execution_stats["successful_executions"] += len(actions)
        else:
            self.execution_stats["retry_count"] += 1
        
        # Calculate rates
        total = self.execution_stats["total_executions"]
        if total > 0:
            self.execution_stats["click_success_rate"] = (
                self.execution_stats["successful_executions"] / total
            )
            self.execution_stats["not_found_rate"] = (
                self.execution_stats["retry_count"] / total
            )
        
        return ActionExecutionResult(
            success=overall_success,
            message=f"Executed {len(actions)} actions" + (" successfully" if overall_success else " with errors"),
            errors=all_errors if all_errors else None,
            warnings=all_warnings if all_warnings else None,
            retry_suggested=retry_needed,
            new_snapshot_needed=snapshot_needed
        )
    
    async def _execute_single_action(self, action: Dict[str, Any]) -> ActionExecutionResult:
        """Execute a single action."""
        op = action.get("op", "").lower()
        
        try:
            if op == "click":
                return await self._execute_click(action)
            elif op == "type":
                return await self._execute_type(action)
            elif op == "scroll":
                return await self._execute_scroll(action)
            elif op == "hover":
                return await self._execute_hover(action)
            elif op == "navigate":
                return await self._execute_navigate(action)
            elif op == "wait":
                return await self._execute_wait(action)
            elif op == "screenshot":
                return await self._execute_screenshot(action)
            else:
                return ActionExecutionResult(
                    success=False,
                    errors=[f"Unknown operation: {op}"]
                )
                
        except Exception as e:
            log.error(f"Action execution failed for {op}: {e}")
            return ActionExecutionResult(
                success=False,
                errors=[f"Execution failed: {str(e)}"],
                retry_suggested=True,
                new_snapshot_needed=True
            )
    
    async def _execute_click(self, action: Dict[str, Any]) -> ActionExecutionResult:
        """Execute click action."""
        target = action.get("target")
        stable_ref = StableNodeRef.from_string(target)
        
        if not stable_ref:
            return ActionExecutionResult(
                success=False,
                errors=["Invalid target reference"]
            )
        
        try:
            page = self.data_supply.page
            
            # Try to click using stable reference
            if stable_ref.backend_node_id:
                # Use CDP to resolve node and click
                cdp = self.data_supply.data_supply.cdp_session
                if cdp:
                    try:
                        # Get node location
                        node_info = await cdp.send("DOM.describeNode", {
                            "backendNodeId": stable_ref.backend_node_id
                        })
                        
                        # Get content quads for precise clicking
                        quads = await cdp.send("DOM.getContentQuads", {
                            "backendNodeId": stable_ref.backend_node_id
                        })
                        
                        if quads.get("quads"):
                            quad = quads["quads"][0]
                            # Calculate center point
                            x = (quad[0] + quad[4]) / 2
                            y = (quad[1] + quad[5]) / 2
                            
                            await page.mouse.click(x, y)
                            
                            return ActionExecutionResult(
                                success=True,
                                message=f"Clicked at ({x}, {y})"
                            )
                    except Exception as e:
                        log.warning(f"CDP click failed, falling back to selector: {e}")
            
            # Fallback to CSS selector
            if stable_ref.css_selector:
                element = await page.query_selector(stable_ref.css_selector)
                if element:
                    await element.click()
                    return ActionExecutionResult(
                        success=True,
                        message="Clicked using CSS selector"
                    )
                else:
                    return ActionExecutionResult(
                        success=False,
                        errors=["Element not found"],
                        retry_suggested=True,
                        new_snapshot_needed=True
                    )
            
            return ActionExecutionResult(
                success=False,
                errors=["Could not resolve target for clicking"]
            )
            
        except Exception as e:
            return ActionExecutionResult(
                success=False,
                errors=[f"Click failed: {str(e)}"],
                retry_suggested=True
            )
    
    async def _execute_type(self, action: Dict[str, Any]) -> ActionExecutionResult:
        """Execute type action."""
        target = action.get("target")
        text = action.get("text", "")
        
        stable_ref = StableNodeRef.from_string(target)
        if not stable_ref:
            return ActionExecutionResult(
                success=False,
                errors=["Invalid target reference"]
            )
        
        try:
            page = self.data_supply.page
            
            # Find element and type text
            if stable_ref.css_selector:
                element = await page.query_selector(stable_ref.css_selector)
                if element:
                    # Clear existing text and type new text
                    await element.click()  # Focus element
                    await element.fill(text)
                    
                    return ActionExecutionResult(
                        success=True,
                        message=f"Typed '{text}' into element"
                    )
                else:
                    return ActionExecutionResult(
                        success=False,
                        errors=["Input element not found"],
                        retry_suggested=True,
                        new_snapshot_needed=True
                    )
            
            return ActionExecutionResult(
                success=False,
                errors=["Could not resolve target for typing"]
            )
            
        except Exception as e:
            return ActionExecutionResult(
                success=False,
                errors=[f"Type failed: {str(e)}"],
                retry_suggested=True
            )
    
    async def _execute_scroll(self, action: Dict[str, Any]) -> ActionExecutionResult:
        """Execute scroll action."""
        direction = action.get("direction", "down")
        amount = action.get("amount", 800)
        
        try:
            page = self.data_supply.page
            
            if direction == "down":
                await page.mouse.wheel(0, amount)
            elif direction == "up":
                await page.mouse.wheel(0, -amount)
            elif direction == "right":
                await page.mouse.wheel(amount, 0)
            elif direction == "left":
                await page.mouse.wheel(-amount, 0)
            
            return ActionExecutionResult(
                success=True,
                message=f"Scrolled {direction} by {amount}px"
            )
            
        except Exception as e:
            return ActionExecutionResult(
                success=False,
                errors=[f"Scroll failed: {str(e)}"]
            )
    
    async def _execute_hover(self, action: Dict[str, Any]) -> ActionExecutionResult:
        """Execute hover action."""
        target = action.get("target")
        stable_ref = StableNodeRef.from_string(target)
        
        if not stable_ref:
            return ActionExecutionResult(
                success=False,
                errors=["Invalid target reference"]
            )
        
        try:
            page = self.data_supply.page
            
            if stable_ref.css_selector:
                element = await page.query_selector(stable_ref.css_selector)
                if element:
                    await element.hover()
                    return ActionExecutionResult(
                        success=True,
                        message="Hovered over element"
                    )
                else:
                    return ActionExecutionResult(
                        success=False,
                        errors=["Element not found for hovering"],
                        retry_suggested=True,
                        new_snapshot_needed=True
                    )
            
            return ActionExecutionResult(
                success=False,
                errors=["Could not resolve target for hovering"]
            )
            
        except Exception as e:
            return ActionExecutionResult(
                success=False,
                errors=[f"Hover failed: {str(e)}"]
            )
    
    async def _execute_navigate(self, action: Dict[str, Any]) -> ActionExecutionResult:
        """Execute navigate action."""
        url = action.get("url", action.get("target"))
        
        try:
            page = self.data_supply.page
            await page.goto(url, wait_until="load", timeout=30000)
            
            return ActionExecutionResult(
                success=True,
                message=f"Navigated to {url}",
                new_snapshot_needed=True
            )
            
        except Exception as e:
            return ActionExecutionResult(
                success=False,
                errors=[f"Navigation failed: {str(e)}"]
            )
    
    async def _execute_wait(self, action: Dict[str, Any]) -> ActionExecutionResult:
        """Execute wait action."""
        import asyncio
        
        duration = action.get("duration", action.get("ms", 500))
        
        try:
            await asyncio.sleep(duration / 1000.0)  # Convert ms to seconds
            
            return ActionExecutionResult(
                success=True,
                message=f"Waited {duration}ms"
            )
            
        except Exception as e:
            return ActionExecutionResult(
                success=False,
                errors=[f"Wait failed: {str(e)}"]
            )
    
    async def _execute_screenshot(self, action: Dict[str, Any]) -> ActionExecutionResult:
        """Execute screenshot action."""
        try:
            # Take screenshot and get VIS-ROI data
            vis_roi = await self.data_supply.data_supply.extract_vis_roi()
            
            return ActionExecutionResult(
                success=True,
                message=f"Screenshot captured: {vis_roi.image['id']}"
            )
            
        except Exception as e:
            return ActionExecutionResult(
                success=False,
                errors=[f"Screenshot failed: {str(e)}"]
            )


class DSLProcessor:
    """High-level processor for LLM action DSL."""
    
    def __init__(self, data_supply_manager: DataSupplyManager):
        self.data_supply = data_supply_manager
        self.validator = DSLValidator(data_supply_manager)
        self.executor = ActionExecutor(data_supply_manager)
    
    async def process_request(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming LLM action request."""
        try:
            # Parse request
            action_request = self.validator.parse_request(request_data)
            
            if action_request.type == ActionType.PLAN:
                return {
                    "type": "plan_received",
                    "plan": action_request.plan,
                    "message": "Plan received and stored"
                }
            
            elif action_request.type == ActionType.ASK:
                return {
                    "type": "question",
                    "question": action_request.question,
                    "message": "User input needed"
                }
            
            elif action_request.type == ActionType.RETRY:
                # Get fresh snapshot and return it
                snapshot_data = await self.data_supply.get_all_formats(include_screenshot=True)
                return {
                    "type": "retry_data",
                    "snapshot": snapshot_data,
                    "message": action_request.message or "Fresh snapshot for retry"
                }
            
            elif action_request.type == ActionType.ACT:
                if not action_request.actions:
                    return {
                        "type": "error",
                        "message": "No actions provided for execution"
                    }
                
                # Validate actions
                validation_result = await self.validator.validate_actions(action_request.actions)
                
                if not validation_result.valid:
                    return {
                        "type": "retry",
                        "errors": validation_result.errors,
                        "warnings": validation_result.warnings,
                        "message": "Action validation failed - snapshot update needed"
                    }
                
                # Execute validated actions
                execution_result = await self.executor.execute_actions(validation_result.normalized_actions)
                
                response = {
                    "type": "execution_result",
                    "success": execution_result.success,
                    "message": execution_result.message
                }
                
                if execution_result.errors:
                    response["errors"] = execution_result.errors
                
                if execution_result.warnings:
                    response["warnings"] = execution_result.warnings
                
                if execution_result.retry_suggested:
                    response["retry_suggested"] = True
                
                if execution_result.new_snapshot_needed:
                    response["new_snapshot_needed"] = True
                
                # Include execution statistics
                response["stats"] = self.executor.execution_stats
                
                return response
            
            else:
                return {
                    "type": "error",
                    "message": f"Unknown action type: {action_request.type}"
                }
                
        except Exception as e:
            log.error(f"DSL processing failed: {e}")
            return {
                "type": "error", 
                "message": f"Processing failed: {str(e)}"
            }