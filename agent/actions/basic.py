"""Action helpers used by the controller."""

from typing import Dict


def click(target: str) -> Dict:
    return {"action": "click", "target": target}


def click_text(text: str) -> Dict:
    return {"action": "click_text", "text": text, "target": text}


def navigate(url: str) -> Dict:
    return {"action": "navigate", "target": url}


def type_text(target: str, value: str) -> Dict:
    return {"action": "type", "target": target, "value": value}


def wait(ms: int = 500, retry: int | None = None) -> Dict:
    act = {"action": "wait", "ms": ms}
    if retry is not None:
        act["retry"] = retry
    return act


def wait_for_selector(target: str, ms: int = 3000) -> Dict:
    return {"action": "wait_for_selector", "target": target, "ms": ms}


def go_back() -> Dict:
    return {"action": "go_back"}


def go_forward() -> Dict:
    return {"action": "go_forward"}


def hover(target: str) -> Dict:
    return {"action": "hover", "target": target}


def select_option(target: str, value: str) -> Dict:
    return {"action": "select_option", "target": target, "value": value}


def press_key(key: str, target: str | None = None) -> Dict:
    act = {"action": "press_key", "key": key}
    if target:
        act["target"] = target
    return act


def extract_text(target: str) -> Dict:
    return {"action": "extract_text", "target": target}


def eval_js(script: str) -> Dict:
    """Execute JavaScript in the page and store the result.

    Use this when built-in actions cannot express a complex operation or when
    page state must be inspected via DOM APIs.  The returned value is recorded
    by the automation server and can be fetched with :func:`get_eval_results`.
    """
    return {"action": "eval_js", "script": script}


def search_google(query: str, new_tab: bool = False) -> Dict:
    return {"action": "search_google", "query": query, "new_tab": new_tab}


def go_to_url(url: str, new_tab: bool = False) -> Dict:
    return {"action": "go_to_url", "target": url, "new_tab": new_tab}


def click_element_by_index(index: int, ctrl: bool | None = None) -> Dict:
    act = {"action": "click_element_by_index", "index": index}
    if ctrl is not None:
        act["while_holding_ctrl"] = ctrl
    return act


def input_text(index: int, text: str, clear_existing: bool = True) -> Dict:
    return {
        "action": "input_text",
        "index": index,
        "text": text,
        "clear_existing": clear_existing,
    }


def scroll_pages(down: bool = True, num_pages: float = 1.0, frame_index: int | None = None) -> Dict:
    act: Dict = {"action": "scroll", "down": down, "num_pages": num_pages}
    if frame_index is not None:
        act["frame_element_index"] = frame_index
    return act


def scroll_to_text(text: str) -> Dict:
    return {"action": "scroll_to_text", "text": text}


def send_keys(keys: str) -> Dict:
    return {"action": "send_keys", "keys": keys}


def switch_tab(tab_id: str) -> Dict:
    return {"action": "switch_tab", "tab_id": tab_id}


def close_tab(tab_id: str | None = None) -> Dict:
    act = {"action": "close_tab"}
    if tab_id:
        act["tab_id"] = tab_id
    return act


def get_dropdown_options(index: int) -> Dict:
    return {"action": "get_dropdown_options", "index": index}


def select_dropdown_option(index: int, text: str) -> Dict:
    return {"action": "select_dropdown_option", "index": index, "text": text}


def upload_file_to_element(index: int, path: str | list[str]) -> Dict:
    return {"action": "upload_file_to_element", "index": index, "path": path}


def extract_structured_data(index: int | None = None, target: str | None = None) -> Dict:
    act: Dict = {"action": "extract_structured_data"}
    if index is not None:
        act["index"] = index
    if target:
        act["target"] = target
    return act
