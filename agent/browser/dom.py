from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class DOMElementNode:
    tagName: str = ""
    attributes: Dict[str, str] = field(default_factory=dict)
    text: Optional[str] = None
    xpath: str = ""
    isVisible: bool = False
    isInteractive: bool = False
    isTopElement: bool = False
    highlightIndex: Optional[int] = None
    children: List["DOMElementNode"] = field(default_factory=list)
    annotations: Optional[List[str]] = None
    excludedByParent: bool = False
    isNewElement: bool = False
    stable_id: Optional[str] = None
    frame_id: Optional[str] = None
    frame_path: Optional[List[str]] = None
    layout: Optional[Dict[str, Any]] = None

    @classmethod
    def from_json(cls, data: dict) -> "DOMElementNode":
        if data is None:
            return None
        if isinstance(data, dict) and "documents" in data:
            parser = _SnapshotParser(data)
            return parser.build()

        if data.get("nodeType") == "text":
            return cls(tagName="#text", text=data.get("text"))

        children = [cls.from_json(c) for c in data.get("children", []) if c]
        annotations = data.get("annotations")
        if annotations and not isinstance(annotations, list):
            annotations = None
        return cls(
            tagName=data.get("tagName", ""),
            attributes=data.get("attributes", {}),
            text=data.get("text"),
            xpath=data.get("xpath", ""),
            isVisible=data.get("isVisible", False),
            isInteractive=data.get("isInteractive", False),
            isTopElement=data.get("isTopElement", False),
            highlightIndex=data.get("highlightIndex"),
            children=children,
            annotations=annotations,
            excludedByParent=data.get("excludedByParent", False),
            isNewElement=data.get("isNewElement", False),
        )

    @classmethod
    def from_page(cls, page) -> "DOMElementNode":  # pragma: no cover - retained for compatibility
        raise RuntimeError(
            "DOMElementNode.from_page is no longer supported. Use the automation server snapshot API."
        )

    # Backwards compatible alias
    from_html = from_page

    def to_lines(
        self, depth: int = 0, max_lines: int | None = None, _lines=None
    ) -> List[str]:
        """Return structured text representation of the DOM tree optimized for LLM consumption."""
        if _lines is None:
            _lines = []
        if max_lines is not None and len(_lines) >= max_lines:
            return _lines

        indent = "  " * depth

        # Handle text nodes
        if self.tagName == "#text":
            if self.text and self.text.strip():
                _lines.append(f"{indent}{self.text.strip()}")
            return _lines

        # Skip excluded elements
        if self.excludedByParent:
            return _lines

        # Build element representation
        parts = []

        # Add interactive element index at the beginning
        if self.highlightIndex is not None:
            prefix = "*" if self.isNewElement else ""
            parts.append(f"[{prefix}{self.highlightIndex}]")

        # Add opening tag with attributes
        tag_parts = [f"<{self.tagName}"]

        # Add relevant attributes
        attr_parts = []
        for key, value in (self.attributes or {}).items():
            if value:  # Only add attributes with values
                if key in ["type", "name", "role", "title", "placeholder", "alt", "aria-label"]:
                    attr_parts.append(f'{key}="{value}"')
                elif key == "href" and value.startswith("http"):
                    if len(value) > 50:
                        attr_parts.append(f'href="{value[:47]}..."')
                    else:
                        attr_parts.append(f'href="{value}"')
                elif key in ["id", "class"] and len(value) <= 30:
                    attr_parts.append(f'{key}="{value}"')
        if attr_parts:
            tag_parts.append(" " + " ".join(attr_parts))

        text_content = self._collect_text_content()

        if not self.children or (
            len(self.children) == 1 and self.children[0].tagName == "#text"
        ):
            if text_content:
                tag_parts.append(f" /> {text_content}")
            else:
                tag_parts.append(" />")

            if self.annotations:
                for annotation in self.annotations:
                    tag_parts.append(f" |{annotation}|")

            parts.extend(tag_parts)
            _lines.append(f"{indent}{''.join(parts)}")
        else:
            tag_parts.append(">")
            if self.annotations:
                for annotation in self.annotations:
                    tag_parts.append(f" |{annotation}|")

            parts.extend(tag_parts)
            _lines.append(f"{indent}{''.join(parts)}")

            for ch in self.children:
                if max_lines is not None and len(_lines) >= max_lines:
                    break
                ch.to_lines(depth + 1, max_lines, _lines)

        return _lines

    def _collect_text_content(self) -> str:
        """Collect all text content from this element and its children."""
        texts = []

        def collect_text(node: "DOMElementNode"):
            if node.tagName == "#text" and node.text:
                texts.append(node.text.strip())
            else:
                for child in node.children:
                    collect_text(child)

        for child in self.children:
            collect_text(child)

        return " ".join(texts).strip()

    def to_text(
        self, max_lines: int | None = None, previous_dom: "DOMElementNode" = None
    ) -> str:
        """Generate structured text representation with scroll position annotations."""
        if previous_dom:
            self._mark_new_elements(previous_dom)

        lines = self.to_lines(max_lines=max_lines)

        result_lines = []

        scroll_info = getattr(self, "_scroll_info", None)
        if scroll_info:
            if scroll_info.get("pixels_above", 0) > 0:
                result_lines.append(f"... {scroll_info['pixels_above']} pixels above ...")

        result_lines.extend(lines)

        if scroll_info:
            if scroll_info.get("pixels_below", 0) > 0:
                result_lines.append(f"... {scroll_info['pixels_below']} pixels below ...")

        return "\n".join(result_lines)

    def _mark_new_elements(self, previous_dom: "DOMElementNode"):
        """Mark elements that are new compared to previous DOM."""
        previous_elements = set()

        def collect_elements(node: "DOMElementNode", elements_set: set[str]):
            if node.xpath:
                elements_set.add(node.xpath)
            for child in node.children:
                collect_elements(child, elements_set)

        collect_elements(previous_dom, previous_elements)

        def mark_new(node: "DOMElementNode"):
            if node.xpath and node.xpath not in previous_elements:
                node.isNewElement = True
            for child in node.children:
                mark_new(child)

        mark_new(self)

    def set_scroll_info(self, pixels_above: int = 0, pixels_below: int = 0):
        """Set scroll position information for annotations."""
        self._scroll_info = {
            "pixels_above": pixels_above,
            "pixels_below": pixels_below,
        }


class _SnapshotParser:
    """Convert a DOM snapshot payload into a DOMElementNode tree."""

    def __init__(self, snapshot: Dict[str, Any]) -> None:
        self.snapshot = snapshot or {}
        self.documents: Dict[int, Dict[str, Any]] = {}
        self.nodes_by_doc: Dict[int, Dict[int, Dict[str, Any]]] = {}

        for doc in self.snapshot.get("documents", []) or []:
            if not isinstance(doc, dict):
                continue
            doc_index = doc.get("index")
            if not isinstance(doc_index, int):
                continue
            self.documents[doc_index] = doc
            node_map: Dict[int, Dict[str, Any]] = {}
            for node in doc.get("nodes", []) or []:
                if not isinstance(node, dict):
                    continue
                node_index = node.get("index")
                if isinstance(node_index, int):
                    node_map[node_index] = node
            self.nodes_by_doc[doc_index] = node_map

        self.interactive_counter = itertools.count(1)
        self._document_stack: set[int] = set()

    def build(self) -> Optional[DOMElementNode]:
        root_index = self._root_document_index()
        if root_index is None:
            return None
        return self._build_document(root_index)

    def _root_document_index(self) -> Optional[int]:
        for index, doc in self.documents.items():
            if not doc.get("owner"):
                return index
        if self.documents:
            return min(self.documents.keys())
        return None

    def _build_document(self, doc_index: int) -> Optional[DOMElementNode]:
        if doc_index in self._document_stack:
            return None
        self._document_stack.add(doc_index)
        try:
            root_node_index = self._find_document_root(doc_index)
            if root_node_index is None:
                return None
            return self._build_node(doc_index, root_node_index)
        finally:
            self._document_stack.discard(doc_index)

    def _find_document_root(self, doc_index: int) -> Optional[int]:
        node_map = self.nodes_by_doc.get(doc_index)
        if not node_map:
            return None

        body_candidate = None
        html_candidate = None
        first_candidate = None

        for idx, node in node_map.items():
            if node.get("node_type") != 1:
                continue
            parent_idx = node.get("parent")
            parent = node_map.get(parent_idx)
            if parent is not None and parent.get("node_type") != 9:
                continue
            name = (node.get("node_name") or "").lower()
            if first_candidate is None:
                first_candidate = idx
            if name == "body":
                body_candidate = idx
            elif name == "html":
                html_candidate = idx

        if body_candidate is not None:
            return body_candidate
        if html_candidate is not None:
            return html_candidate
        if first_candidate is not None:
            return first_candidate

        doc_node = next((n for n in node_map.values() if n.get("node_type") == 9), None)
        if doc_node:
            for child_idx in doc_node.get("children") or []:
                child = node_map.get(child_idx)
                if child and child.get("node_type") == 1:
                    return child_idx
        return None

    def _build_node(self, doc_index: int, node_index: int) -> Optional[DOMElementNode]:
        node_map = self.nodes_by_doc.get(doc_index, {})
        node = node_map.get(node_index)
        if not node:
            return None

        node_type = node.get("node_type")
        doc = self.documents.get(doc_index, {})

        if node_type == 3:
            text = node.get("text_content") or node.get("node_value") or ""
            text = text.strip()
            if not text:
                return None
            return DOMElementNode(
                tagName="#text",
                text=text,
                xpath=node.get("dom_path", ""),
                frame_id=doc.get("frame_id"),
                frame_path=list(doc.get("frame_path") or []),
            )

        if node_type == 9:
            for child_idx in node.get("children") or []:
                child = self._build_node(doc_index, child_idx)
                if child:
                    return child
            return None

        if node_type != 1:
            return None

        attrs = dict(node.get("attributes") or {})
        children_nodes: List[DOMElementNode] = []

        for child_idx in node.get("children") or []:
            child_node = self._build_node(doc_index, child_idx)
            if child_node:
                children_nodes.append(child_node)

        for child_doc_index in node.get("child_documents") or []:
            child_doc_node = self._build_document(child_doc_index)
            if child_doc_node:
                children_nodes.append(child_doc_node)

        is_interactive = bool(node.get("is_interactive"))
        highlight_index = next(self.interactive_counter) if is_interactive else None
        annotations = list(node.get("annotations") or [])

        layout = node.get("layout")
        if isinstance(layout, dict):
            layout_data = dict(layout)
        else:
            layout_data = None

        dom_node = DOMElementNode(
            tagName=(node.get("node_name") or "").lower(),
            attributes=attrs,
            xpath=node.get("dom_path", ""),
            isVisible=bool(node.get("is_visible")),
            isInteractive=is_interactive,
            isTopElement=is_interactive,
            highlightIndex=highlight_index,
            children=children_nodes,
            annotations=annotations or None,
            stable_id=node.get("stable_id"),
            frame_id=doc.get("frame_id"),
            frame_path=list(doc.get("frame_path") or []),
            layout=layout_data,
        )

        return dom_node
