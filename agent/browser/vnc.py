import requests
import logging
from bs4 import BeautifulSoup, NavigableString, Tag
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


def _html_to_dom(html: str) -> DOMElementNode | None:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.body or soup

    def traverse(node):
        if isinstance(node, NavigableString):
            text = node.strip()
            if not text:
                return None
            return DOMElementNode(tagName="#text", text=text)
        if not isinstance(node, Tag):
            return None
        attrs = {
            k: v for k, v in node.attrs.items() if isinstance(v, str)
        }
        children = []
        for ch in node.contents:
            n = traverse(ch)
            if n:
                children.append(n)
        return DOMElementNode(tagName=node.name, attributes=attrs, children=children)

    return traverse(root)


def execute_dsl(payload, timeout=120):
    """Forward DSL JSON to the automation server.

    Returns a tuple of (HTML string, error message).
    """
    if not payload.get("actions"):
        return "", ""
    try:
        r = requests.post(f"{VNC_API}/execute-dsl", json=payload, timeout=None)
        r.raise_for_status()
        if r.headers.get("content-type", "").startswith("application/json"):
            data = r.json()
            return data.get("html", ""), data.get("error", "")
        return r.text, ""
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
        return DOMElementNode.from_json(data), None
    except Exception as e:
        log.error("get_dom_tree error: %s", e)
        html = get_html()
        if html:
            try:
                return _html_to_dom(html), f"dom-tree failed: {e}"
            except Exception as e2:
                log.error("fallback parse error: %s", e2)
                return None, f"{e}; fallback parse error: {e2}"
        return None, str(e)
