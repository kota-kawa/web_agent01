"""Utility helpers for validating component dependencies.

This module centralizes dependency management logic for both the VNC
automation service and the Flask web frontend.  It provides helpers to
parse the component specific ``requirements.txt`` files, check which
packages are currently installed, and emit user friendly diagnostics.

The dependency data is also exposed through a small CLI so the complete
dependency picture can be inspected without starting either service::

    python -m vnc.dependency_check --component all

The CLI exits with a non-zero status code when required packages are
missing or have incompatible versions which makes it suitable for use in
CI or local smoke tests.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

try:  # Optional dependency; available in most Python environments.
    from packaging.requirements import Requirement as PackagingRequirement  # type: ignore
    from packaging.specifiers import SpecifierSet  # type: ignore
    from packaging.version import Version  # type: ignore
except Exception:  # pragma: no cover - best effort without packaging
    PackagingRequirement = None  # type: ignore[assignment]
    SpecifierSet = None  # type: ignore[assignment]
    Version = None  # type: ignore[assignment]

# Repository root (``.../web_agent01``)
_ROOT_DIR = Path(__file__).resolve().parents[1]

# Mapping of logical components to their requirement files.
_COMPONENT_REQUIREMENTS: Dict[str, Path] = {
    "vnc": _ROOT_DIR / "vnc" / "requirements.txt",
    "web": _ROOT_DIR / "web" / "requirements.txt",
}


@dataclass(slots=True)
class DependencyRecord:
    """Representation of a single requirement line."""

    name: str
    specifier: str
    extras: Tuple[str, ...]
    raw: str

    def as_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "specifier": self.specifier,
            "extras": list(self.extras),
            "raw": self.raw,
        }


@dataclass(slots=True)
class ComponentReport:
    """Result of analysing a component's dependencies."""

    component: str
    requirements_file: Path
    dependencies: List[DependencyRecord] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    mismatched: List[str] = field(default_factory=list)
    installed: Dict[str, str] = field(default_factory=dict)
    unparsed: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
        return {
            "component": self.component,
            "requirements_file": str(self.requirements_file),
            "dependencies": [dep.as_dict() for dep in self.dependencies],
            "missing": self.missing,
            "mismatched": self.mismatched,
            "installed": self.installed,
            "unparsed": self.unparsed,
        }


_REQ_SPLIT = re.compile(r"([<>=!~]=?.*)")


def _canonical_candidates(name: str) -> List[str]:
    """Return possible distribution names for a requirement name."""

    candidates = {name}
    if "-" in name:
        candidates.add(name.replace("-", "_"))
    if "_" in name:
        candidates.add(name.replace("_", "-"))
    canonical = re.sub(r"[-_.]+", "-", name).lower()
    candidates.add(canonical)
    return list(candidates)


def _parse_requirement_line(raw_line: str) -> Optional[DependencyRecord]:
    """Parse a single requirement line to :class:`DependencyRecord`."""

    stripped = raw_line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith(("-r", "--", "git+", "http://", "https://")):
        return None

    line = stripped.split("#", 1)[0].strip()
    if not line:
        return None

    if PackagingRequirement is not None:
        try:
            req = PackagingRequirement(line)
            spec = str(req.specifier) if req.specifier else ""
            extras = tuple(sorted(req.extras))
            return DependencyRecord(req.name, spec, extras, line)
        except Exception:
            # Fall back to manual parsing if ``packaging`` rejects the line.
            pass

    # Basic manual parsing that understands extras and simple specifiers.
    name_part = re.split(r"[<>=!~;\s]", line, maxsplit=1)[0]
    extras: Tuple[str, ...] = ()
    if "[" in name_part and "]" in name_part:
        base, rest = name_part.split("[", 1)
        extras = tuple(
            seg.strip()
            for seg in rest.split("]", 1)[0].split(",")
            if seg.strip()
        )
        name_part = base
    name = name_part.strip()
    if not name:
        return None

    spec_match = _REQ_SPLIT.search(line)
    specifier = spec_match.group(1).strip() if spec_match else ""

    return DependencyRecord(name=name, specifier=specifier, extras=extras, raw=line)


def _iter_requirements(path: Path) -> Tuple[List[DependencyRecord], List[str]]:
    dependencies: List[DependencyRecord] = []
    unparsed: List[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        raise FileNotFoundError(f"requirements file not found: {path}") from None

    for raw in lines:
        parsed = _parse_requirement_line(raw)
        if parsed is None:
            if raw.strip() and not raw.strip().startswith("#"):
                unparsed.append(raw.strip())
            continue
        dependencies.append(parsed)
    return dependencies, unparsed


def _evaluate_dependency(dep: DependencyRecord) -> Tuple[bool, bool, Optional[str], Optional[str]]:
    """Return installation status, version compatibility and version."""

    candidates = _canonical_candidates(dep.name)
    for candidate in candidates:
        try:
            version = metadata.version(candidate)
            meets_spec = True
            if dep.specifier and SpecifierSet is not None and Version is not None:
                try:
                    spec = SpecifierSet(dep.specifier)
                    meets_spec = Version(version) in spec
                except Exception:
                    meets_spec = True  # Graceful degradation
            return True, meets_spec, version, candidate
        except metadata.PackageNotFoundError:
            continue
    return False, False, None, None


def check_component(component: str) -> ComponentReport:
    """Generate a :class:`ComponentReport` for *component*."""

    if component not in _COMPONENT_REQUIREMENTS:
        raise KeyError(f"Unknown component '{component}'")

    requirements_path = _COMPONENT_REQUIREMENTS[component]
    deps, unparsed = _iter_requirements(requirements_path)
    report = ComponentReport(
        component=component,
        requirements_file=requirements_path,
        dependencies=deps,
        unparsed=unparsed,
    )

    for dep in deps:
        installed, meets_spec, version, dist_name = _evaluate_dependency(dep)
        if not installed:
            report.missing.append(dep.raw)
            continue
        if version is not None and dist_name is not None:
            report.installed[dist_name] = version
        if not meets_spec:
            descriptor = dep.raw
            if version:
                descriptor += f" (installed {version})"
            report.mismatched.append(descriptor)
    return report


def ensure_component_dependencies(
    component: str,
    *,
    strict: bool = True,
    logger: Optional[logging.Logger] = None,
) -> ComponentReport:
    """Validate dependencies for *component* and optionally raise."""

    log = logger or logging.getLogger(__name__)
    report = check_component(component)

    if report.missing or report.mismatched:
        details: List[str] = []
        if report.missing:
            details.append("missing: " + ", ".join(report.missing))
        if report.mismatched:
            details.append("version mismatch: " + ", ".join(report.mismatched))
        advice = (
            f"Dependency check failed for component '{component}': "
            + "; ".join(details)
        )
        suggestion = f"Install dependencies with: pip install -r {report.requirements_file}"
        if strict:
            raise RuntimeError(f"{advice}. {suggestion}")
        log.warning("%s. %s", advice, suggestion)
    else:
        log.info(
            "All dependencies satisfied for component '%s' (%d packages)",
            component,
            len(report.dependencies),
        )

    if report.unparsed:
        log.debug(
            "Skipped non-standard requirement lines for component '%s': %s",
            component,
            "; ".join(report.unparsed),
        )

    return report


def _format_report(report: ComponentReport) -> str:
    lines = [f"Component: {report.component}"]
    lines.append(f"  requirements: {report.requirements_file}")
    if report.missing:
        lines.append("  Missing dependencies:")
        lines.extend(f"    - {item}" for item in report.missing)
    else:
        lines.append("  Missing dependencies: none")
    if report.mismatched:
        lines.append("  Version mismatches:")
        lines.extend(f"    - {item}" for item in report.mismatched)
    else:
        lines.append("  Version mismatches: none")
    if report.unparsed:
        lines.append("  Unparsed requirement lines:")
        lines.extend(f"    - {item}" for item in report.unparsed)
    if report.installed:
        lines.append("  Installed versions:")
        for name, version in sorted(report.installed.items()):
            lines.append(f"    - {name}=={version}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect project dependencies")
    parser.add_argument(
        "--component",
        choices=sorted(list(_COMPONENT_REQUIREMENTS.keys()) + ["all"]),
        default="all",
        help="Component to inspect (default: all)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of human readable text",
    )
    args = parser.parse_args(argv)

    components = (
        sorted(_COMPONENT_REQUIREMENTS)
        if args.component == "all"
        else [args.component]
    )

    reports = [check_component(component) for component in components]

    if args.json:
        payload = [report.as_dict() for report in reports]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for report in reports:
            print(_format_report(report))
            print()

    return 0 if all(not (r.missing or r.mismatched) for r in reports) else 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
