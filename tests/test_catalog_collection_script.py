import json
import os
import subprocess
import sys
import tempfile
import types

if "jsonschema" not in sys.modules:
    jsonschema_stub = types.ModuleType("jsonschema")

    class _DummyValidator:
        def __init__(self, schema):
            self.schema = schema

        def iter_errors(self, data):
            return []

    class _DummyValidationError(Exception):
        pass

    jsonschema_stub.Draft7Validator = _DummyValidator
    jsonschema_stub.ValidationError = _DummyValidationError
    sys.modules["jsonschema"] = jsonschema_stub

from vnc.automation_server import CATALOG_COLLECTION_SCRIPT


def _run_catalog_script(script: str) -> dict:
    node_code = f"""
const script = {json.dumps(script)};

function parseStyle(style) {{
  const result = {{ display: 'block', visibility: 'visible' }};
  if (!style) return result;
  style.split(';').forEach(part => {{
    const pieces = part.split(':');
    if (pieces.length < 2) return;
    const prop = pieces[0].trim();
    const value = pieces[1].trim();
    if (!prop || !value) return;
    if (prop === 'display') result.display = value;
    if (prop === 'visibility') result.visibility = value;
  }});
  return result;
}}

class FakeElement {{
  constructor(tagName, options = {{}}) {{
    this.tagName = tagName.toUpperCase();
    this.nodeType = 1;
    this.children = [];
    this.parentElement = null;
    this.innerText = options.innerText || '';
    this.textContent = this.innerText;
    this.value = options.value || '';
    this.id = options.id || '';
    this.disabled = !!options.disabled;
    this._rect = options.rect || {{ top: 20, left: 20, width: 160, height: 40 }};
    this._style = parseStyle(options.style || '');
    this.classList = options.classList ? options.classList.slice() : [];
    this._tabIndex = options.tabIndex !== undefined ? options.tabIndex : -1;
    this._attributes = new Map();
    if (this.id) this._attributes.set('id', this.id);
    if (options.style) this._attributes.set('style', options.style);
    if (options.onclick) {{
      this._attributes.set('onclick', options.onclick);
      this.onclick = function() {{}};
    }}
  }}

  appendChild(child) {{
    child.parentElement = this;
    this.children.push(child);
  }}

  contains(target) {{
    if (this === target) return true;
    return this.children.some(child => child.contains(target));
  }}

  setAttribute(name, value) {{
    const strValue = String(value);
    this._attributes.set(name, strValue);
    if (name === 'id') this.id = strValue;
    if (name === 'class') {{
      this.classList = strValue.split(/\\s+/).filter(Boolean);
    }}
    if (name === 'tabindex') {{
      const parsed = parseInt(strValue, 10);
      this._tabIndex = Number.isNaN(parsed) ? -1 : parsed;
    }}
    if (name === 'style') {{
      this._style = parseStyle(strValue);
    }}
  }}

  getAttribute(name) {{
    if (this._attributes.has(name)) return this._attributes.get(name);
    if (name === 'class') return this.classList.join(' ');
    return null;
  }}

  get attributes() {{
    return Array.from(this._attributes.entries()).map(([name, value]) => {{ return {{ name, value }}; }});
  }}

  getBoundingClientRect() {{
    return this._rect;
  }}

  get tabIndex() {{
    return this._tabIndex;
  }}

  set tabIndex(value) {{
    const parsed = parseInt(value, 10);
    this._tabIndex = Number.isNaN(parsed) ? -1 : parsed;
  }}

  get previousElementSibling() {{
    if (!this.parentElement) return null;
    const siblings = this.parentElement.children;
    const index = siblings.indexOf(this);
    return index > 0 ? siblings[index - 1] : null;
  }}
}}

const html = new FakeElement('html', {{ rect: {{ top: 0, left: 0, width: 1280, height: 720 }} }});
const body = new FakeElement('body', {{ rect: {{ top: 0, left: 0, width: 1280, height: 720 }} }});
const clickable = new FakeElement('div', {{
  innerText: 'Click me now',
  rect: {{ top: 20, left: 20, width: 160, height: 40 }},
  onclick: 'window.__clicked = true',
  id: 'clickable',
  style: 'display:block;visibility:visible'
}});

html.appendChild(body);
body.appendChild(clickable);

const allElements = [html, body, clickable];

const matchesSelector = (el, selector) => {{
  if (!selector) return false;
  if (selector === '*') return true;
  if (selector === 'input:not([type="hidden"])') {{
    return el.tagName.toLowerCase() === 'input' && (el.getAttribute('type') || '').toLowerCase() !== 'hidden';
  }}
  const tagAttr = selector.match(/^([a-zA-Z0-9_-]+)\\[([^=\\]]+)(="([^"]*)")?\\]$/);
  if (tagAttr) {{
    if (el.tagName.toLowerCase() !== tagAttr[1].toLowerCase()) return false;
    const attrName = tagAttr[2];
    const attrValue = tagAttr[4];
    const actual = el.getAttribute(attrName);
    if (attrValue === undefined) return actual !== null && actual !== undefined;
    return actual === attrValue;
  }}
  const attrOnly = selector.match(/^\\[([^=\\]]+)(="([^"]*)")?\\]$/);
  if (attrOnly) {{
    const attrName = attrOnly[1];
    const attrValue = attrOnly[3];
    const actual = el.getAttribute(attrName);
    if (attrValue === undefined) return actual !== null && actual !== undefined;
    return actual === attrValue;
  }}
  if (/^[a-zA-Z0-9_-]+$/.test(selector)) {{
    return el.tagName.toLowerCase() === selector.toLowerCase();
  }}
  return false;
}};

const document = {{
  body,
  documentElement: html,
  querySelectorAll: (selector) => {{
    const selectors = selector.split(',').map(s => s.trim()).filter(Boolean);
    const result = [];
    const seen = new Set();
    for (const sel of selectors) {{
      for (const el of allElements) {{
        if (matchesSelector(el, sel) && !seen.has(el)) {{
          seen.add(el);
          result.push(el);
        }}
      }}
    }}
    return result;
  }},
  getElementById: (id) => allElements.find(el => el.id === id) || null,
  elementFromPoint: () => clickable
}};

const windowObj = {{
  innerWidth: 1280,
  innerHeight: 720,
  getComputedStyle: (el) => {{
    return {{
      display: el._style.display || 'block',
      visibility: el._style.visibility || 'visible'
    }};
  }},
  CSS: {{ escape: (value) => String(value) }},
  __ag_get_events: () => []
}};

global.window = windowObj;
global.document = document;
global.Element = FakeElement;
global.Node = {{ ELEMENT_NODE: 1 }};
global.getEventListeners = undefined;
windowObj.document = document;

const result = eval(script);
console.log(JSON.stringify(result));
"""

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


def test_catalog_collection_indexes_clickable_div():
    result = _run_catalog_script(CATALOG_COLLECTION_SCRIPT)

    assert "elements" in result
    clickable = [
        el
        for el in result["elements"]
        if el.get("tag") == "div" and "Click me now" in el.get("primaryLabel", "")
    ]
    assert clickable, "Expected div with onclick handler to be indexed"

    entry = clickable[0]
    assert entry.get("index") == 0
    assert entry.get("role", "") == ""
    assert entry.get("selectors"), "Interactive entry should include selectors"
