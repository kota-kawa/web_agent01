import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from agent.browser.dom import DOMElementNode
from playwright.sync_api import sync_playwright


def find_by_id(node, element_id):
    if node.attributes.get("id") == element_id:
        return node
    for ch in node.children:
        found = find_by_id(ch, element_id)
        if found:
            return found
    return None


def test_react_dom_extraction():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
      <script crossorigin src="https://unpkg.com/react@18/umd/react.development.js"></script>
      <script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.development.js"></script>
    </head>
    <body>
      <div id="root"></div>
      <script>
        const e = React.createElement;
        function App() { return e('button', {id:'react-btn'}, 'React'); }
        ReactDOM.createRoot(document.getElementById('root')).render(e(App));
      </script>
    </body>
    </html>
    """
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html)
        page.wait_for_selector('#react-btn')
        dom = DOMElementNode.from_page(page)
        btn = find_by_id(dom, 'react-btn')
        assert btn is not None and btn.isInteractive and btn.isVisible
        browser.close()


def test_vue_dom_extraction():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
      <script src="https://unpkg.com/vue@3/dist/vue.global.js"></script>
    </head>
    <body>
      <div id="app"></div>
      <script>
        const { createApp, h } = Vue;
        createApp({
          render() {
            return h('button', {id:'vue-btn'}, 'Vue');
          }
        }).mount('#app');
      </script>
    </body>
    </html>
    """
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html)
        page.wait_for_selector('#vue-btn')
        dom = DOMElementNode.from_page(page)
        btn = find_by_id(dom, 'vue-btn')
        assert btn is not None and btn.isInteractive and btn.isVisible
        browser.close()


def test_jquery_dom_extraction():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
      <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
    </head>
    <body>
      <div id="container"></div>
      <script>
        $(function(){ $('#container').append('<button id="jq-btn">jQuery</button>'); });
      </script>
    </body>
    </html>
    """
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html)
        page.wait_for_selector('#jq-btn')
        dom = DOMElementNode.from_page(page)
        btn = find_by_id(dom, 'jq-btn')
        assert btn is not None and btn.isInteractive and btn.isVisible
        browser.close()
