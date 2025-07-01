import requests
import logging

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
