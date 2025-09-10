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


def execute_dsl(payload, timeout=120):
    """Forward DSL JSON to the automation server."""
    if not payload.get("actions"):
        return {"html": "", "warnings": []}
    try:
        r = requests.post(f"{VNC_API}/execute-dsl", json=payload, timeout=(5, timeout))
        r.raise_for_status()
        result = r.json()
        
        # Handle both old error format and new warnings format
        if "error" in result:
            # Convert old error format to new warnings format
            error_msg = result.get("message", result.get("error", "Unknown error"))
            return {"html": "", "warnings": [f"ERROR:auto:{error_msg}"]}
        
        return result
        
    except requests.Timeout:
        log.error("execute_dsl timeout")
        return {"html": "", "warnings": ["ERROR:auto:Request timeout - The operation took too long to complete"]}
    except requests.RequestException as e:
        log.error("execute_dsl request error: %s", e)
        return {"html": "", "warnings": [f"ERROR:auto:Communication error - {str(e)}"]}
    except Exception as e:
        log.error("execute_dsl unexpected error: %s", e)
        return {"html": "", "warnings": [f"ERROR:auto:Unexpected error - {str(e)}"]}


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
