import requests
import logging
from .dom import DOMElementNode

VNC_API = "http://vnc:7000"
log = logging.getLogger(__name__)


def get_html() -> str:
    """Fetch current page HTML from the VNC automation server."""
    try:
        res = requests.get(f"{VNC_API}/source", timeout=(5, 30))
        res.raise_for_status()
        return res.text
    except Exception as e:
        log.error("get_html error: %s", e)
        return ""


import requests
import logging
import time
from .dom import DOMElementNode

VNC_API = "http://vnc:7000"
log = logging.getLogger(__name__)


def _check_health(timeout=5):
    """Check if the VNC automation server is healthy."""
    try:
        response = requests.get(f"{VNC_API}/healthz", timeout=timeout)
        return response.status_code == 200
    except Exception as e:
        log.warning("Health check failed: %s", e)
        return False


def get_html() -> str:
    """Fetch current page HTML from the VNC automation server."""
    try:
        res = requests.get(f"{VNC_API}/source", timeout=(5, 30))
        res.raise_for_status()
        return res.text
    except Exception as e:
        log.error("get_html error: %s", e)
        return ""


def _classify_error_type(error_msg: str) -> tuple[bool, str, int]:
    """Classify error type and return (is_retryable, friendly_msg, suggested_wait_time)."""
    error_lower = error_msg.lower()
    
    # Network/connection errors - retryable with longer waits
    if any(x in error_lower for x in ["connection error", "timeout", "failed to fetch", "network"]):
        return True, "ネットワーク接続の問題", 2
    
    # Server errors - retryable with shorter waits  
    if any(x in error_lower for x in ["500", "502", "503", "504", "internal server error"]):
        return True, "サーバーの一時的な問題", 1
    
    # Browser state errors - retryable with short waits
    if any(x in error_lower for x in ["page is navigating", "navigating and changing", "browser not initialized"]):
        return True, "ブラウザの状態確認中", 1
    
    # Element/interaction errors - retryable with minimal waits
    if any(x in error_lower for x in ["element not found", "locator not found", "not visible", "not enabled"]):
        return True, "要素の読み込み待機中", 0.5
    
    # Client errors - usually not retryable
    if any(x in error_lower for x in ["400", "401", "403", "404", "validation error", "invalid"]):
        return False, "設定またはリクエストの問題", 0
    
    # Default - treat as retryable for safety
    return True, "一時的な処理エラー", 1


def execute_dsl(payload, timeout=120):
    """Forward DSL JSON to the automation server with enhanced retry logic."""
    if not payload.get("actions"):
        return {"html": "", "warnings": []}
    
    max_retries = 4  # Increased from 2 to handle more intermittent issues
    last_error = None
    consecutive_failures = 0
    
    for attempt in range(1, max_retries + 1):
        # Enhanced server health check and adaptive waiting
        if attempt > 1:
            log.info("DSL retry attempt %d/%d, checking server health...", attempt, max_retries)
            server_healthy = _check_health()
            
            if not server_healthy:
                log.warning("Server health check failed on retry attempt %d", attempt)
                consecutive_failures += 1
                
                # Longer wait for consecutive failures
                if consecutive_failures >= 2:
                    adaptive_wait = min(2.0 + consecutive_failures, 5.0)  # Up to 5s for persistent issues
                    log.info("Multiple consecutive failures detected, using extended wait: %.1fs", adaptive_wait)
                else:
                    adaptive_wait = min(1.0 + (attempt - 1) * 0.5, 3.0)
                
                time.sleep(adaptive_wait)
            else:
                log.info("Server health check passed on retry attempt %d", attempt)
                consecutive_failures = 0  # Reset on successful health check
        
        try:
            r = requests.post(f"{VNC_API}/execute-dsl", json=payload, timeout=(5, timeout))
            r.raise_for_status()
            result = r.json()
            
            # Success - log if this was a retry
            if attempt > 1:
                log.info("DSL execution succeeded on retry attempt %d", attempt)
            
            # Handle both old error format and new warnings format
            if "error" in result:
                # Convert old error format to new warnings format
                error_msg = result.get("message", result.get("error", "Unknown error"))
                return {"html": "", "warnings": [f"ERROR:auto:{error_msg}"]}
            
            return result
            
        except requests.Timeout:
            last_error = "Request timeout - The operation took too long to complete"
            log.error("execute_dsl timeout on attempt %d", attempt)
            
            is_retryable, friendly_msg, wait_time = _classify_error_type(last_error)
            if attempt < max_retries and is_retryable:
                # Enhanced backoff with health checking
                base_wait = wait_time * attempt  # Use classified wait time
                wait_time_final = base_wait * 0.5 if _check_health() else base_wait
                log.info("Retrying after %.1fs due to timeout (%s)...", wait_time_final, friendly_msg)
                time.sleep(wait_time_final)
                continue
                
        except requests.HTTPError as e:
            # Enhanced HTTP error handling with better classification
            status_code = e.response.status_code if e.response else 0
            error_text = f"HTTP {status_code} error"
            
            try:
                # Try to get more specific error info from response
                error_detail = e.response.json() if e.response else {}
                if error_detail.get("message"):
                    error_text += f" - {error_detail['message']}"
            except:
                error_text += f" - {str(e)}"
                
            last_error = error_text
            log.error("execute_dsl HTTP error on attempt %d: %s", attempt, last_error)
            
            is_retryable, friendly_msg, base_wait_time = _classify_error_type(last_error)
            
            # Enhanced retry logic for different HTTP error types
            if attempt < max_retries and is_retryable:
                wait_time_final = base_wait_time * attempt
                # For server errors, check health and adjust wait time
                if status_code >= 500:
                    wait_time_final = wait_time_final * 0.7 if _check_health() else wait_time_final
                
                log.info("Retrying after %.1fs due to HTTP error %d (%s)...", wait_time_final, status_code, friendly_msg)
                time.sleep(wait_time_final)
                continue
            elif status_code >= 500:
                # Final attempt failed with server error
                break
            else:
                # Client error - don't retry
                break
                
        except requests.ConnectionError as e:
            error_msg = f"Connection error - Could not connect to automation server"
            last_error = f"{error_msg}: {str(e)}"
            log.error("execute_dsl connection error on attempt %d: %s", attempt, last_error)
            
            is_retryable, friendly_msg, base_wait_time = _classify_error_type(last_error)
            if attempt < max_retries and is_retryable:
                # Enhanced connection error handling
                wait_time_final = base_wait_time * attempt * 2  # Longer waits for connection issues
                # Much shorter wait if server becomes responsive
                if _check_health():
                    wait_time_final = min(wait_time_final * 0.3, 2.0)
                    log.info("Server became responsive, reducing retry wait time")
                
                log.info("Retrying after %.1fs due to connection error (%s)...", wait_time_final, friendly_msg)
                time.sleep(wait_time_final)
                continue
                
        except requests.RequestException as e:
            error_msg = f"Request error - {str(e)}"
            last_error = error_msg
            log.error("execute_dsl request error on attempt %d: %s", attempt, last_error)
            
            is_retryable, friendly_msg, base_wait_time = _classify_error_type(last_error)
            if attempt < max_retries and is_retryable:
                wait_time_final = base_wait_time * attempt
                # Adaptive wait based on server health for general request errors
                wait_time_final = wait_time_final * 0.6 if _check_health() else wait_time_final
                log.info("Retrying after %.1fs due to request error (%s)...", wait_time_final, friendly_msg)
                time.sleep(wait_time_final)
                continue
        except Exception as e:
            last_error = f"Unexpected error - {str(e)}"
            log.error("execute_dsl unexpected error on attempt %d: %s", attempt, last_error)
            break  # Don't retry unexpected errors
    
    # All retries exhausted - provide enhanced error messaging
    log.error("execute_dsl failed after %d attempts: %s", max_retries, last_error)
    
    # Enhanced error message with guidance based on error type
    if last_error:
        is_retryable, friendly_msg, _ = _classify_error_type(last_error)
        if is_retryable:
            guidance_msg = f"通信エラーが継続しています ({friendly_msg}) - しばらく待ってから再試行してください"
        else:
            guidance_msg = f"設定を確認してください ({friendly_msg})"
        
        return {"html": "", "warnings": [f"ERROR:auto:{max_retries}回の再試行後も失敗 - {guidance_msg}"]}
    else:
        return {"html": "", "warnings": [f"ERROR:auto:原因不明のエラー - {max_retries}回再試行しましたが成功しませんでした"]}


def get_elements() -> list:
    """Get clickable/input elements with index info."""
    try:
        res = requests.get(f"{VNC_API}/elements", timeout=(5, 30))
        res.raise_for_status()
        return res.json()
    except Exception as e:
        log.error("get_elements error: %s", e)
        return []


def get_extracted() -> list:
    """Retrieve texts captured via extract_text action."""
    try:
        res = requests.get(f"{VNC_API}/extracted", timeout=(5, 30))
        res.raise_for_status()
        return res.json()
    except Exception as e:
        log.error("get_extracted error: %s", e)
        return []


def get_eval_results() -> list:
    """Retrieve results of the most recent eval_js calls."""
    try:
        res = requests.get(f"{VNC_API}/eval_results", timeout=(5, 30))
        res.raise_for_status()
        return res.json()
    except Exception as e:
        log.error("get_eval_results error: %s", e)
        return []


def eval_js(script: str):
    """Execute JavaScript and return its result if any."""
    payload = {"actions": [{"action": "eval_js", "script": script}]}
    result = execute_dsl(payload)
    
    # Check for warnings/errors
    if result.get("warnings"):
        log.warning("eval_js warnings: %s", result["warnings"])
    
    eval_results = get_eval_results()
    return eval_results[-1] if eval_results else None


def get_dom_tree() -> tuple[DOMElementNode | None, str | None]:
    """Retrieve the DOM tree by parsing the current page HTML.

    Returns a tuple of (DOM tree or None, error message or None).
    """
    try:
        html = get_html()
        if not html:
            raise ValueError("empty html")
        return DOMElementNode.from_html(html), None
    except Exception as e:
        log.error("get_dom_tree error: %s", e)
        return None, str(e)
