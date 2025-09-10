import requests
import logging
from .dom import DOMElementNode

VNC_API = "http://vnc:7000"
log = logging.getLogger(__name__)


def get_html() -> str:
    """Fetch current page HTML from the VNC automation server."""
    try:
        res = requests.get(f"{VNC_API}/source", timeout=30)
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
        r = requests.post(f"{VNC_API}/execute-dsl", json=payload, timeout=None)
        r.raise_for_status()
        return r.json()
    except requests.Timeout:
        log.error("execute_dsl timeout")
        raise


def get_elements() -> list:
    """Get clickable/input elements with index info."""
    try:
        res = requests.get(f"{VNC_API}/elements", timeout=30)
        res.raise_for_status()
        return res.json()
    except Exception as e:
        log.error("get_elements error: %s", e)
        return []


def get_extracted() -> list:
    """Retrieve texts captured via extract_text action."""
    try:
        res = requests.get(f"{VNC_API}/extracted", timeout=30)
        res.raise_for_status()
        return res.json()
    except Exception as e:
        log.error("get_extracted error: %s", e)
        return []


def get_eval_results() -> list:
    """Retrieve results of the most recent eval_js calls."""
    try:
        res = requests.get(f"{VNC_API}/eval_results", timeout=30)
        res.raise_for_status()
        return res.json()
    except Exception as e:
        log.error("get_eval_results error: %s", e)
        return []


def eval_js(script: str):
    """Execute JavaScript and return its result if any."""
    payload = {"actions": [{"action": "eval_js", "script": script}]}
    execute_dsl(payload)
    res = get_eval_results()
    return res[-1] if res else None


def get_dom_tree() -> tuple[DOMElementNode | None, str | None]:
    """Retrieve the DOM tree using interactive buildDomTree.js script.

    Returns a tuple of (DOM tree or None, error message or None).
    """
    try:
        # Try to get interactive DOM tree from the VNC server
        res = requests.get(f"{VNC_API}/dom-tree", timeout=30)
        if res.ok:
            dom_data = res.json()
            if isinstance(dom_data, dict) and not dom_data.get("error"):
                return DOMElementNode.from_json(dom_data), None
            else:
                error_msg = dom_data.get("error", "Unknown DOM tree error") if isinstance(dom_data, dict) else "Invalid DOM data format"
                log.warning("Interactive DOM tree failed: %s, falling back to HTML parsing", error_msg)
        else:
            log.warning("DOM tree endpoint failed with status %s, falling back to HTML parsing", res.status_code)
        
        # Fallback to HTML parsing if interactive DOM tree fails
        html = get_html()
        if not html:
            raise ValueError("empty html and DOM tree endpoint failed")
        return DOMElementNode.from_html(html), None
    except Exception as e:
        log.error("get_dom_tree error: %s", e)
        return None, str(e)
