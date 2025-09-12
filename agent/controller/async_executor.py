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
            "duration": (self.completed_at - self.started_at) if self.started_at and self.completed_at else None
        }


class AsyncExecutor:
    """Manages async execution of Playwright operations and data fetching."""
    
    def __init__(self, max_workers: int = 4):
        self.tasks: Dict[str, ExecutionTask] = {}
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        self.cleanup_interval = 300  # Clean up completed tasks after 5 minutes
        
    def create_task(self) -> str:
        """Create a new task and return its ID."""
        task_id = str(uuid.uuid4())
        self.tasks[task_id] = ExecutionTask(task_id=task_id)
        log.info("Created task %s", task_id)
        return task_id
    
    def submit_playwright_execution(self, task_id: str, execute_func: Callable, actions: list) -> bool:
        """Submit Playwright execution for async processing."""
        if task_id not in self.tasks:
            log.error("Task %s not found", task_id)
            return False
            
        task = self.tasks[task_id]
        if task.status != TaskStatus.PENDING:
            log.error("Task %s is not in pending state: %s", task_id, task.status)
            return False
            
        def run_execution():
            try:
                task.status = TaskStatus.RUNNING
                task.started_at = time.time()
                log.info("Starting execution for task %s", task_id)
                
                # Execute the Playwright operations
                result = execute_func({"actions": actions})
                
                task.result = result
                task.status = TaskStatus.COMPLETED
                task.completed_at = time.time()
                log.info("Completed execution for task %s in %.2fs", 
                        task_id, task.completed_at - task.started_at)
                
            except Exception as e:
                task.error = str(e)
                task.status = TaskStatus.FAILED
                task.completed_at = time.time()
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
    
    def cancel_all_tasks(self):
        """Cancel all running and pending tasks."""
        cancelled_count = 0
        for task_id, task in list(self.tasks.items()):
            if task.status in [TaskStatus.PENDING, TaskStatus.RUNNING]:
                task.status = TaskStatus.FAILED
                task.error = "Cancelled by user reset"
                task.completed_at = time.time()
                cancelled_count += 1
                log.info("Cancelled task %s due to reset", task_id)
        
        log.info("Cancelled %d tasks due to reset", cancelled_count)
        return cancelled_count
    
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