"""Data structures for selector resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .models import Selector


@dataclass(slots=True)
class CandidateScore:
    """Scoring metadata for a candidate element."""

    strategy: str
    score: float
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ResolvedNode:
    """Result of resolving a selector to a single DOM node."""

    selector: Selector
    stable_id: str
    score: float
    dom_path: str
    text_digest: str
    strategy: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    element: Any | None = field(default=None, repr=False)


@dataclass(slots=True)
class ResolutionAttempt:
    """Intermediate candidate before selecting the best match."""

    selector: Selector
    locator: Any
    dom_path: str
    text_digest: str
    strategy: str
    score: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_resolved(self, stable_id: str, *, warnings: Optional[list[str]] = None) -> ResolvedNode:
        return ResolvedNode(
            selector=self.selector,
            stable_id=stable_id,
            score=self.score,
            dom_path=self.dom_path,
            text_digest=self.text_digest,
            strategy=self.strategy,
            metadata=dict(self.metadata),
            warnings=list(warnings or []),
            element=self.locator,
        )
