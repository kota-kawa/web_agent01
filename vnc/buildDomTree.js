function buildDomTree(win = window, frameOffset = {x: 0, y: 0}) {
  let counter = 1;

  function isElementAccepted(el) {
    const tag = (el.tagName || '').toLowerCase();
    return !['script','style','meta','link','noscript'].includes(tag);
  }

  function isTextNodeVisible(node) {
    if (!node.nodeValue || !node.nodeValue.trim()) return false;
    const parent = node.parentElement;
    if (!parent) return false;
    const style = getComputedStyle(parent);
    return style.visibility !== 'hidden' && style.display !== 'none';
  }

  function isVisible(el) {
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0 &&
           style.visibility !== 'hidden' && style.display !== 'none';
  }

  function isInteractive(el) {
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role') || '';
    const tags = ['a','button','input','select','textarea','option'];
    const roles = ['button','link','checkbox','radio','textbox','tab','option','menuitem'];
    return tags.includes(tag) || roles.includes(role) ||
           typeof el.onclick === 'function' || typeof el.onchange === 'function';
  }

  function isTopElement(el) {
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return false;
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    const top = document.elementFromPoint(x, y);
    return top === el || el.contains(top);
  }

  function buildXPath(el) {
    if (!el || el.nodeType !== 1) return '';
    if (el === document.body) return '/html/body';
    let ix = 1;
    let sib = el.previousSibling;
    while (sib) {
      if (sib.nodeType === 1 && sib.tagName === el.tagName) ix++;
      sib = sib.previousSibling;
    }
    return buildXPath(el.parentNode) + '/' + el.tagName.toLowerCase() + '[' + ix + ']';
  }

  function traverse(node, offset) {
    if (node.nodeType === Node.TEXT_NODE) {
      if (!isTextNodeVisible(node)) return null;
      return { nodeType: 'text', text: node.nodeValue.trim() };
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return null;
    const el = node;
    if (!isElementAccepted(el)) return null;

    const attrs = {};
    for (const a of el.attributes) attrs[a.name] = a.value;

    const children = [];
    el.childNodes.forEach(c => { const r = traverse(c, offset); if (r) children.push(r); });
    if (el.shadowRoot) {
      el.shadowRoot.childNodes.forEach(c => { const r = traverse(c, offset); if (r) children.push(r); });
    }

    // iframe 対応 - クロスオリジンは無視
    if (el.tagName.toLowerCase() === 'iframe') {
      try {
        const doc = el.contentDocument;
        if (doc && doc.body) {
          const rect = el.getBoundingClientRect();
          const iframeOffset = {
            x: offset.x + rect.left,
            y: offset.y + rect.top,
          };
          doc.body.childNodes.forEach(c => { const r = traverse(c, iframeOffset); if (r) children.push(r); });
        }
      } catch (e) {
        // ignore cross origin iframe
      }
    }

    const visible = isVisible(el);
    const interactive = isInteractive(el) && visible && isTopElement(el);
    let hIdx = null;
    if (interactive) {
      hIdx = counter++;
    }

    const rect = el.getBoundingClientRect();
    const bounding = {
      x: offset.x + rect.left,
      y: offset.y + rect.top,
      width: rect.width,
      height: rect.height,
    };

    return {
      nodeType: 'element',
      tagName: el.tagName.toLowerCase(),
      attributes: attrs,
      xpath: buildXPath(el),
      isVisible: visible,
      isInteractive: interactive,
      isTopElement: isTopElement(el),
      highlightIndex: hIdx,
      boundingRect: bounding,
      children: children
    };
  }

  return traverse(win.document.body, frameOffset);
}

return buildDomTree();
