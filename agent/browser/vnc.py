import requests
import logging
from typing import Tuple

from .dom import DOMSnapshot

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


def get_dom_tree() -> Tuple[DOMSnapshot | None, str | None]:
    """Retrieve the compact DOM snapshot from the automation server."""
    try:
        res = requests.get(f"{VNC_API}/dom", timeout=30)
        res.raise_for_status()
        data = res.json()
        snapshot = DOMSnapshot.from_json(data)
        err = data.get("error")
        return snapshot, str(err) if err else None
    except Exception as e:
        log.error("get_dom_tree error: %s", e)
        return None, str(e)
