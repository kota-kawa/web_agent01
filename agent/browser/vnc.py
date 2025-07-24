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
        return ""
    try:
        r = requests.post(f"{VNC_API}/execute-dsl", json=payload, timeout=None)
        r.raise_for_status()
        return r.text
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


def get_dom_tree() -> tuple[DOMElementNode | None, str | None]:
    """Retrieve full DOM tree structure.

    Returns a tuple of (DOM tree or None, error message or None).
    """
    try:
        res = requests.get(f"{VNC_API}/dom-tree", timeout=30)
        res.raise_for_status()
        data = res.json()
        if not data:
            raise ValueError("empty dom tree")
        return DOMElementNode.from_json(data), None
    except Exception as e:
        log.error("get_dom_tree error: %s", e)
        # Fallback to parsing raw HTML so that the caller still gets a DOM tree
        try:
            html = get_html()
            if html:
                return DOMElementNode.from_html(html), str(e)
        except Exception as fe:
            log.error("fallback dom_tree parse error: %s", fe)
        return None, str(e)
