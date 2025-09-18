import json
import os
import subprocess
import tempfile
import textwrap

from agent.browser.dom import DOMElementNode, DOM_SNAPSHOT_SCRIPT
from agent.controller.prompt import build_prompt


def _run_dom_snapshot(script: str) -> dict:
    backslash = "\\"
    node_code = textwrap.dedent(
        f"""
const script = {json.dumps(script)};

const NODE_ELEMENT = 1;
const NODE_TEXT = 3;

class FakeTextNode {{
  constructor(text) {{
    this.nodeType = NODE_TEXT;
    this.textContent = text;
    this.parentElement = null;
  }}
}}

class FakeElement {{
  constructor(tagName, options = {{}}) {{
    this.nodeType = NODE_ELEMENT;
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.childNodes = [];
    this.parentElement = null;
    this.innerText = options.innerText || '';
    this.textContent = this.innerText;
    this.id = options.id || '';
    this.classList = options.classList ? options.classList.slice() : [];
    this.isContentEditable = !!options.isContentEditable;
    this._tabIndex = options.tabIndex !== undefined ? options.tabIndex : -1;
    const style = options.style || {{}};
    this._style = {{
      display: style.display || 'block',
      visibility: style.visibility || 'visible',
      overflow: style.overflow || 'visible',
      overflowX: style.overflowX || 'visible',
      overflowY: style.overflowY || 'visible'
    }};
    const rect = options.rect || {{ top: 0, left: 0, width: 100, height: 20 }};
    this._rect = {{
      top: rect.top,
      left: rect.left,
      width: rect.width,
      height: rect.height,
      right: rect.left + rect.width,
      bottom: rect.top + rect.height
    }};
    this._attrs = new Map();
    if (this.id) this._attrs.set('id', this.id);
    if (options.href) this._attrs.set('href', options.href);
    if (options.role) this._attrs.set('role', options.role);
    if (options.placeholder) this._attrs.set('placeholder', options.placeholder);
    if (options.tabIndex !== undefined) this._attrs.set('tabindex', String(options.tabIndex));
    if (options.classList && options.classList.length) this._attrs.set('class', options.classList.join(' '));
  }}

  appendChild(child) {{
    child.parentElement = this;
    this.childNodes.push(child);
    if (child.nodeType === NODE_ELEMENT) {{
      this.children.push(child);
    }}
  }}

  contains(target) {{
    if (this === target) return true;
    for (const child of this.children) {{
      if (child.contains(target)) return true;
    }}
    return false;
  }}

  getBoundingClientRect() {{
    return this._rect;
  }}

  get previousElementSibling() {{
    if (!this.parentElement) return null;
    const siblings = this.parentElement.children;
    const idx = siblings.indexOf(this);
    return idx > 0 ? siblings[idx - 1] : null;
  }}

  getAttribute(name) {{
    if (name === 'class') return this.classList.join(' ');
    if (name === 'id') return this.id;
    if (this._attrs.has(name)) return this._attrs.get(name);
    return null;
  }}

  setAttribute(name, value) {{
    const str = String(value);
    this._attrs.set(name, str);
    if (name === 'id') this.id = str;
    if (name === 'class') this.classList = str.split(/{backslash}s+/).filter(Boolean);
  }}

  get attributes() {{
    const attrs = [];
    for (const [name, value] of this._attrs.entries()) {{
      attrs.push({{ name, value }});
    }}
    return attrs;
  }}

  get tabIndex() {{
    return this._tabIndex;
  }}
}}

const html = new FakeElement('html', {{ rect: {{ top: 0, left: 0, width: 800, height: 600 }} }});
const body = new FakeElement('body', {{ rect: {{ top: 0, left: 0, width: 800, height: 600 }} }});
html.appendChild(body);

const button = new FakeElement('button', {{
  innerText: 'Submit',
  rect: {{ top: 10, left: 10, width: 120, height: 40 }},
  id: 'submit',
}});
const link = new FakeElement('a', {{
  innerText: 'Learn more',
  rect: {{ top: 70, left: 10, width: 140, height: 40 }},
  href: 'https://example.com'
}});

button.appendChild(new FakeTextNode('Submit'));
link.appendChild(new FakeTextNode('Learn more'));
body.appendChild(button);
body.appendChild(link);

const allElements = [html, body, button, link];

const document = {{
  body,
  documentElement: html,
  querySelectorAll: (selector) => {{
    if (selector === '*') return allElements.slice();
    return allElements.filter(el => el.tagName.toLowerCase() === selector.toLowerCase());
  }},
  getElementById: (id) => allElements.find(el => el.id === id) || null,
  elementFromPoint: (x, y) => {{
    for (let i = allElements.length - 1; i >= 0; i--) {{
      const el = allElements[i];
      if (el.nodeType !== NODE_ELEMENT) continue;
      const rect = el.getBoundingClientRect();
      if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) {{
        return el;
      }}
    }}
    return body;
  }}
}};

const windowObj = {{
  innerWidth: 1280,
  innerHeight: 720,
  getComputedStyle: (el) => el._style
}};

global.window = windowObj;
global.document = document;
global.Element = FakeElement;
global.Node = {{ ELEMENT_NODE: NODE_ELEMENT, TEXT_NODE: NODE_TEXT }};
global.getEventListeners = undefined;
windowObj.document = document;

const result = eval(script);
console.log(JSON.stringify(result));
"""
    ).strip()

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as tmp:
        tmp.write(node_code)
        script_path = tmp.name

    try:
        completed = subprocess.run(
            ["node", script_path],
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        os.remove(script_path)

    output = completed.stdout.strip()
    return json.loads(output)


def test_dom_snapshot_uses_zero_based_highlight_indices():
    snapshot = _run_dom_snapshot(DOM_SNAPSHOT_SCRIPT)

    def _collect_indices(node: dict) -> list[int]:
        indices = []
        highlight = node.get("highlightIndex")
        if highlight is not None:
            indices.append(highlight)
        for child in node.get("children", []):
            indices.extend(_collect_indices(child))
        return indices

    highlight_indices = _collect_indices(snapshot)
    assert highlight_indices[:2] == [0, 1]

    dom_node = DOMElementNode.from_json(snapshot)
    dom_lines = "\n".join(dom_node.to_lines())
    assert "[0]" in dom_lines
    assert "[1]" in dom_lines

    prompt = build_prompt(
        cmd="テスト",
        page="<html><body></body></html>",
        hist=[],
        elements=dom_node,
            element_catalog_text="[0] button: Submit\n[1] link: Learn more",
            catalog_metadata={"index_mode_enabled": True},
    )

    assert "[0]<button" in prompt
    assert "[1]<a" in prompt
    assert "[0] button: Submit" in prompt
