"""Helpers for building stable element catalogs from ``browser_use`` DOM state.

This module translates the rich DOM metadata exposed by
``browser_use.dom.views.SerializedDOMState`` into a concise, stable textual
representation that can be fed to LLM prompts or stored in step history.  The
implementation mirrors the heuristics used inside the upstream project to
identify interactive elements while keeping the output intentionally compact so
that it fits within model context limits.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from browser_use.dom.views import EnhancedDOMTreeNode

KEY_ATTRIBUTES: tuple[str, ...] = (
    "id",
    "name",
    "data-testid",
    "data-test",
    "data-cy",
    "role",
    "type",
    "aria-label",
    "aria-describedby",
    "placeholder",
    "value",
)


def _trim(value: str, *, limit: int = 80) -> str:
    """Return *value* trimmed to ``limit`` characters."""

    stripped = (value or "").strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[: limit - 1] + "…"


@dataclass(slots=True)
class ElementCatalogEntry:
    """Human readable representation of an interactive DOM node."""

    index: int
    tag: str
    text: str
    attributes: dict[str, str]
    frame_id: str | None
    xpath: str
    is_visible: bool | None

    @classmethod
    def from_node(cls, index: int, node: EnhancedDOMTreeNode) -> "ElementCatalogEntry":
        attrs = node.attributes or {}
        relevant_attrs = {
            key: _trim(value)
            for key, value in attrs.items()
            if key in KEY_ATTRIBUTES and value
        }

        text_content = node.node_value or ""
        if not text_content and node.ax_node and getattr(node.ax_node, "name", None):
            text_content = node.ax_node.name or ""

        return cls(
            index=index,
            tag=node.tag_name,
            text=_trim(text_content, limit=120),
            attributes=relevant_attrs,
            frame_id=node.frame_id,
            xpath=node.xpath,
            is_visible=getattr(node, "is_visible", None),
        )

    def to_text(self) -> str:
        bits: list[str] = [f"[{self.index:02d}] <{self.tag}>"]
        if self.frame_id:
            bits.append(f"frame={self.frame_id[-4:]}")
        if self.is_visible is False:
            bits.append("hidden")

        if self.text:
            bits.append(f"text=\"{self.text}\"")

        for key in KEY_ATTRIBUTES:
            value = self.attributes.get(key)
            if value:
                bits.append(f"{key}={value}")

        bits.append(f"xpath=/{self.xpath}" if not self.xpath.startswith("/") else f"xpath={self.xpath}")
        return " | ".join(bits)


@dataclass(slots=True)
class ElementCatalogSnapshot:
    """Snapshot of the current catalog with metadata useful for debugging."""

    entries: list[ElementCatalogEntry]

    @property
    def text(self) -> str:
        if not self.entries:
            return ""
        return "\n".join(entry.to_text() for entry in self.entries)

    @property
    def metadata(self) -> dict[str, object]:
        counter = Counter(entry.tag for entry in self.entries)
        return {
            "total": len(self.entries),
            "tags": dict(counter),
        }


def build_element_catalog(
    selector_map: dict[int, EnhancedDOMTreeNode] | None,
) -> ElementCatalogSnapshot:
    """Build a deterministic catalog from *selector_map*.

    The selector map is produced by ``browser_use`` and contains interactive
    elements indexed in the order presented to the model.  The catalog mirrors
    this order so indices remain stable between the text representation and
    subsequent actions.
    """

    if not selector_map:
        return ElementCatalogSnapshot(entries=[])

    entries: list[ElementCatalogEntry] = []
    for index in sorted(selector_map):
        node = selector_map.get(index)
        if node is None:
            continue
        try:
            entries.append(ElementCatalogEntry.from_node(index, node))
        except AttributeError:  # pragma: no cover - defensive
            continue

    return ElementCatalogSnapshot(entries=entries)


def enumerate_catalog_entries(
    snapshot: ElementCatalogSnapshot,
) -> Iterable[ElementCatalogEntry]:
    """Yield entries from *snapshot* ensuring compatibility with older code."""

    yield from snapshot.entries


__all__ = [
    "ElementCatalogEntry",
    "ElementCatalogSnapshot",
    "build_element_catalog",
    "enumerate_catalog_entries",
]

