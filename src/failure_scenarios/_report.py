"""Plain-text and JSON rendering of scenario history."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._scenario import Scenario

__all__ = ["render_text", "to_json", "write_json_report"]


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def render_text(scenario: Scenario, *, rule: str | None = None) -> str:
    """A compact, deterministic table of rules and calls."""
    lines = [f"scenario {scenario.name!r} (seed={scenario.seed!r}, unmatched={scenario.unmatched})"]
    if scenario.rules:
        lines.append("rules:")
        for r in scenario.rules:
            lines.append(
                f"  {_clip(r.name, 48):<48} {r.call_count:>3} call(s)  plan: {r.plan.describe()}"
            )
    calls = [c for c in scenario.calls if rule is None or c.rule == rule]
    if not calls:
        lines.append("calls: none")
        return "\n".join(lines)
    lines.append("calls:")
    lines.append(f"  {'#':>3}  {'t+s':>8}  {'request':<44} {'rule':<22} {'injected':<34} result")
    for c in calls:
        req = f"{c.method} {c.target}".strip()
        rule_label = "-" if c.rule is None else f"{c.rule}[{c.rule_call}]"
        lines.append(
            f"  {c.index:>3}  {c.started_at:>8.3f}  {_clip(req, 44):<44} "
            f"{_clip(rule_label, 22):<22} {_clip(c.outcome, 34):<34} {c.result}"
        )
    return "\n".join(lines)


def to_json(scenario: Scenario, *, indent: int | None = 2) -> str:
    return json.dumps(scenario.to_dict(), indent=indent, default=str)


def write_json_report(
    path: str | Path, entries: Iterable[dict[str, Any]], *, meta: dict[str, Any] | None = None
) -> Path:
    """Write a report file: ``{"meta": ..., "tests": [...]}``."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta or {}, "tests": list(entries)}
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return out
