"""
Async task manager for parallel Playwright execution and data fetching.
"""
import asyncio
import uuid
import logging
import time
import concurrent.futures
from typing import Dict, Any, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum

log = logging.getLogger(__name__)

# Pre-generated task ID pool for faster task creation
_task_id_pool = []
_task_id_pool_size = 100

def _ensure_task_id_pool():
    """Ensure task ID pool is populated for immediate task creation."""
    global _task_id_pool
    while len(_task_id_pool) < _task_id_pool_size:
        _task_id_pool.append(str(uuid.uuid4()))

# Initialize the pool at import time
_ensure_task_id_pool()


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ExecutionTask:
    """Represents an async execution task."""
    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    history_entry_id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "history_entry_id": self.history_entry_id,
            "duration": (self.completed_at - self.started_at) if self.started_at and self.completed_at else None
        }


class AsyncExecutor:
    """Manages async execution of Playwright operations and data fetching."""
    
    def __init__(self, max_workers: int = 4):
        self.tasks: Dict[str, ExecutionTask] = {}
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        self.cleanup_interval = 300  # Clean up completed tasks after 5 minutes
        
    def create_task(self, history_entry_id: Optional[int] = None) -> str:
        """Create a new task and return its ID (optimized for speed)."""
        global _task_id_pool

        # Use pre-generated task ID for immediate creation
        if _task_id_pool:
            task_id = _task_id_pool.pop()
        else:
            # Fallback to generating new ID if pool is empty
            task_id = str(uuid.uuid4())
            log.warning("Task ID pool exhausted, generating new ID")

        # Create task with minimal overhead
        self.tasks[task_id] = ExecutionTask(task_id=task_id, history_entry_id=history_entry_id)
        log.debug("Created task %s", task_id)
        
        # Replenish pool asynchronously to maintain performance
        if len(_task_id_pool) < 10:  # Replenish when low
            self.executor.submit(_ensure_task_id_pool)
        
        return task_id
    
    def submit_playwright_execution(
        self,
        task_id: str,
        execute_func: Callable,
        actions: list,
        payload: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Submit Playwright execution for async processing (optimized for immediate execution)."""
        task = self.tasks.get(task_id)
        if not task:
            log.error("Task %s not found", task_id)
            return False
            
        if task.status != TaskStatus.PENDING:
            log.error("Task %s is not in pending state: %s", task_id, task.status)
            return False
            
        def _truncate_warning(warning_msg, max_length=None):
            """Return warning message without truncation (character limits removed)."""
            # Character limits removed for conversation history as requested
            return warning_msg

        def _format_error_info(error_info: Any) -> str:
            """Normalize error information into a readable string."""
            if isinstance(error_info, dict):
                message = error_info.get("message") or "Unknown error"
                code = error_info.get("code")
                details = error_info.get("details")
                segments = [message]
                if code:
                    segments.append(f"code={code}")
                if details:
                    segments.append(f"details={details}")
                return " | ".join(segments)
            return str(error_info)

        def run_execution():
            try:
                task.status = TaskStatus.RUNNING
                task.started_at = time.time()
                log.debug("Starting execution for task %s", task_id)
                
                # Execute the Playwright operations immediately
                payload_data: Dict[str, Any] = {"actions": actions}
                if payload:
                    payload_data.update(payload)
                result = execute_func(payload_data)
                
                # Ensure warnings are properly formatted and truncated
                if result and isinstance(result, dict):
                    if "warnings" in result and result["warnings"]:
                        # Include all warning messages without character limits
                        result["warnings"] = [_truncate_warning(warning) for warning in result["warnings"]]

                    # If execution returned an error but not in warnings format, convert it
                    error_info = result.get("error")
                    if error_info:
                        formatted_error = _format_error_info(error_info)
                        if "warnings" not in result:
                            result["warnings"] = []
                        result["warnings"].append(_truncate_warning(f"ERROR:auto:{formatted_error}"))
                        result["error"] = None
                
                task.result = result
                task.status = TaskStatus.COMPLETED
                task.completed_at = time.time()
                log.info("Completed execution for task %s in %.2fs", 
                        task_id, task.completed_at - task.started_at)
                
                # Update conversation history with current URL after successful execution
                try:
                    from agent.browser.vnc import get_url
                    from agent.utils.history import load_hist, save_hist
                    
                    current_url = get_url()
                    if current_url:  # Only update if we got a valid URL
                        history_entry_id = task.history_entry_id
                        if history_entry_id is None:
                            log.debug(
                                "No history entry id associated with task %s; skipping URL update",
                                task_id,
                            )
                        else:
                            hist = load_hist()
                            if 0 <= history_entry_id < len(hist):
                                hist[history_entry_id]["url"] = current_url
                                save_hist(hist)
                                log.debug(
                                    "Updated conversation history URL for entry %s to: %s",
                                    history_entry_id,
                                    current_url,
                                )
                            else:
                                log.warning(
                                    "History entry %s not found when updating URL for task %s",
                                    history_entry_id,
                                    task_id,
                                )
                    else:
                        log.debug("No URL available to update conversation history")
                except Exception as url_error:
                    log.error("Failed to update conversation history URL: %s", url_error)
                
            except Exception as e:
                task.error = str(e)
                task.status = TaskStatus.FAILED
                task.completed_at = time.time()
                
                # Create comprehensive warnings from the exception
                error_type = type(e).__name__
                error_detail = str(e)
                
                # Try to get more detailed error information
                import traceback
                stack_info = traceback.format_exc()
                
                warnings = []
                warnings.append(_truncate_warning(f"ERROR:auto:Async execution failed ({error_type}) - {error_detail}"))
                
                # Include stack trace information if helpful
                if "playwright" in stack_info.lower() or "automation" in stack_info.lower():
                    stack_lines = stack_info.splitlines()
                    relevant_stack = [line for line in stack_lines if any(keyword in line.lower() for keyword in ['playwright', 'automation', 'error', 'exception', 'traceback'])]
                    if relevant_stack:
                        stack_warning = f"STACK:auto:{' | '.join(relevant_stack[:3])}"  # First 3 relevant lines
                        warnings.append(_truncate_warning(stack_warning))
                
                task.result = {
                    "html": "", 
                    "warnings": warnings
                }
                
                log.error("Failed execution for task %s: %s", task_id, e)
        
        # Submit to thread pool
        future = self.executor.submit(run_execution)
        return True
    
    def submit_parallel_data_fetch(self, task_id: str, fetch_funcs: Dict[str, Callable]) -> bool:
        """Submit parallel data fetching operations."""
        if task_id not in self.tasks:
            log.error("Task %s not found", task_id)
            return False
            
        def run_parallel_fetch():
            try:
                log.info("Starting parallel data fetch for task %s", task_id)
                
                # Run all fetch functions in parallel
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(fetch_funcs)) as fetch_executor:
                    future_to_name = {
                        fetch_executor.submit(func): name 
                        for name, func in fetch_funcs.items()
                    }
                    
                    fetch_results = {}
                    for future in concurrent.futures.as_completed(future_to_name):
                        name = future_to_name[future]
                        try:
                            fetch_results[name] = future.result()
                        except Exception as e:
                            log.error("Failed to fetch %s for task %s: %s", name, task_id, e)
                            fetch_results[name] = None
                
                # Update task result with fetched data
                task = self.tasks[task_id]
                if task.result is None:
                    task.result = {}
                task.result.update(fetch_results)
                
                log.info("Completed parallel data fetch for task %s", task_id)
                
            except Exception as e:
                log.error("Failed parallel data fetch for task %s: %s", task_id, e)
        
        # Submit to thread pool (non-blocking)
        future = self.executor.submit(run_parallel_fetch)
        return True
    
    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get current status of a task."""
        if task_id not in self.tasks:
            return None
        return self.tasks[task_id].to_dict()
    
    def is_task_complete(self, task_id: str) -> bool:
        """Check if a task is complete (successfully or failed)."""
        if task_id not in self.tasks:
            return False
        return self.tasks[task_id].status in [TaskStatus.COMPLETED, TaskStatus.FAILED]
    
    def cleanup_old_tasks(self):
        """Remove old completed tasks to prevent memory leaks."""
        current_time = time.time()
        to_remove = []
        
        for task_id, task in self.tasks.items():
            if (task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED] and 
                task.completed_at and 
                current_time - task.completed_at > self.cleanup_interval):
                to_remove.append(task_id)
        
        for task_id in to_remove:
            del self.tasks[task_id]
            log.debug("Cleaned up old task %s", task_id)
    
    def shutdown(self):
        """Shutdown the executor."""
        log.info("Shutting down AsyncExecutor")
        self.executor.shutdown(wait=True)


# Global instance
_async_executor = None


def get_async_executor() -> AsyncExecutor:
    """Get global async executor instance."""
    global _async_executor
    if _async_executor is None:
        _async_executor = AsyncExecutor()
    return _async_executor
