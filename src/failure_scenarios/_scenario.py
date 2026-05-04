"""Scenario: a named set of rules, their call history, and assertions."""

from __future__ import annotations

import fnmatch
import itertools
import random
import re
import threading
import time
import zlib
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

from ._faults import PASS, Fault
from ._plans import Plan, as_plan

__all__ = [
    "CallRecord",
    "FaultAssertionError",
    "RequestInfo",
    "Rule",
    "Scenario",
    "UnmatchedCallError",
    "active_scenario",
]

Matcher = str | re.Pattern[str]
_EPS = 1e-9  # tolerate float error in timestamps


class UnmatchedCallError(AssertionError):
    """Raised for a call no rule matches when the scenario uses ``unmatched="error"``."""


class FaultAssertionError(AssertionError):
    """An assertion about recorded calls failed."""


@dataclass(frozen=True)
class RequestInfo:
    """Client-neutral description of an intercepted call."""

    kind: Literal["http", "point"]
    target: str
    method: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes | None = None

    @property
    def url(self) -> str:
        return self.target

    @property
    def host(self) -> str:
        return urlsplit(self.target).hostname or ""

    @property
    def path(self) -> str:
        return urlsplit(self.target).path or "/"


@dataclass
class CallRecord:
    """One intercepted call and what the scenario did to it."""

    index: int
    kind: str
    target: str
    method: str
    rule: str | None
    rule_call: int | None
    fault: Fault
    started_at: float
    thread: str
    ended_at: float | None = None
    status: int | None = None
    error: str | None = None

    @property
    def outcome(self) -> str:
        return self.fault.describe()

    @property
    def duration(self) -> float | None:
        return None if self.ended_at is None else self.ended_at - self.started_at

    @property
    def result(self) -> str:
        if self.error:
            return f"raised {self.error}"
        if self.status is not None:
            return f"HTTP {self.status}"
        return "completed" if self.ended_at is not None else "in progress"

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "kind": self.kind,
            "method": self.method,
            "target": self.target,
            "rule": self.rule,
            "rule_call": self.rule_call,
            "outcome": self.outcome,
            "result": self.result,
            "status": self.status,
            "error": self.error,
            "started_at": round(self.started_at, 6),
            "duration": None if self.duration is None else round(self.duration, 6),
            "thread": self.thread,
        }


def _match_text(matcher: Matcher, value: str) -> bool:
    if isinstance(matcher, re.Pattern):
        return matcher.search(value) is not None
    if any(c in matcher for c in "*?["):
        return fnmatch.fnmatchcase(value, matcher)
    return value == matcher


class Rule:
    """A matcher plus a plan. Created with :meth:`Scenario.http` or :meth:`Scenario.point`."""

    def __init__(
        self,
        scenario: Scenario,
        plan: Plan,
        *,
        name: str,
        kind: Literal["http", "point"],
        method: str | None = None,
        url: Matcher | None = None,
        host: Matcher | None = None,
        path: Matcher | None = None,
        point: str | None = None,
        when: Callable[[RequestInfo], bool] | None = None,
    ) -> None:
        self.scenario = scenario
        self.plan = plan
        self.name = name
        self.kind = kind
        self.method = method.upper() if method else None
        self.url = url
        self.host = host
        self.path = path
        self.point = point
        self.when = when
        self._count = 0
        self._rng = random.Random()
        self.reseed()

    def reseed(self) -> None:
        # Each rule gets its own stream so interleaving with other rules
        # never changes its outcomes.
        self._rng.seed(f"{self.scenario.seed}:{self.name}")

    def matches(self, info: RequestInfo) -> bool:
        if info.kind != self.kind:
            return False
        if self.kind == "point":
            if self.point is None or info.target != self.point:
                return False
        else:
            if self.method and info.method.upper() != self.method:
                return False
            if self.url is not None and not (
                _match_text(self.url, info.url)
                or (isinstance(self.url, str) and _match_text(self.url, info.url.split("?")[0]))
            ):
                return False
            if self.host is not None and not _match_text(self.host, info.host):
                return False
            if self.path is not None and not _match_text(self.path, info.path):
                return False
        return self.when is None or bool(self.when(info))

    def _next(self) -> tuple[int, Fault]:
        index = self._count
        self._count += 1
        return index, self.plan.outcome(index, self._rng)

    # -- inspection -----------------------------------------------------

    @property
    def calls(self) -> list[CallRecord]:
        return [c for c in self.scenario.calls if c.rule == self.name]

    @property
    def call_count(self) -> int:
        return self._count

    @property
    def faults_injected(self) -> int:
        return sum(1 for c in self.calls if c.fault != PASS)

    def intervals(self) -> list[float]:
        """Seconds between the starts of consecutive calls matched by this rule."""
        starts = [c.started_at for c in self.calls]
        return [b - a for a, b in itertools.pairwise(starts)]

    # -- assertions -----------------------------------------------------

    def _fail(self, message: str) -> None:
        raise FaultAssertionError(
            f"rule {self.name!r}: {message}\n\n{self.scenario.summary(rule=self.name)}"
        )

    def assert_called(
        self,
        times: int | None = None,
        *,
        at_least: int | None = None,
        at_most: int | None = None,
    ) -> None:
        n = self.call_count
        if times is not None and n != times:
            self._fail(f"expected exactly {times} call(s), got {n}")
        if at_least is not None and n < at_least:
            self._fail(f"expected at least {at_least} call(s), got {n}")
        if at_most is not None and n > at_most:
            self._fail(f"expected at most {at_most} call(s), got {n}")
        if times is None and at_least is None and at_most is None and n == 0:
            self._fail("expected at least one call, got none")

    def assert_not_called(self) -> None:
        if self.call_count:
            self._fail(f"expected no calls, got {self.call_count}")

    def assert_retried(self, retries: int) -> None:
        """Exactly one initial attempt plus ``retries`` retries were made."""
        n = self.call_count
        if n != retries + 1:
            self._fail(f"expected 1 attempt + {retries} retries = {retries + 1} calls, got {n}")

    def assert_min_interval(self, seconds: float) -> None:
        """Every gap between consecutive calls is at least ``seconds``."""
        gaps = self.intervals()
        if any(g < seconds - _EPS for g in gaps):
            self._fail(
                f"expected gaps >= {seconds}s between calls, got {[round(g, 4) for g in gaps]}"
            )

    def assert_backoff(self, *, factor: float = 1.0, min_first: float = 0.0) -> None:
        """Gaps between calls grow by at least ``factor`` each time (exponential backoff)."""
        gaps = self.intervals()
        if not gaps:
            self._fail("backoff needs at least two calls")
        if gaps[0] < min_first - _EPS:
            self._fail(f"first retry came after {gaps[0]:.4f}s, expected >= {min_first}s")
        for i, (a, b) in enumerate(itertools.pairwise(gaps), start=1):
            if b < a * factor - _EPS:
                self._fail(
                    f"gap {i + 1} ({b:.4f}s) is not >= {factor:g} x gap {i} ({a:.4f}s); "
                    f"gaps: {[round(g, 4) for g in gaps]}"
                )

    def __repr__(self) -> str:
        return f"<Rule {self.name!r} calls={self._count} plan={self.plan.describe()!r}>"


_active: list[Scenario] = []
_active_lock = threading.Lock()


def active_scenario() -> Scenario | None:
    """The most recently activated scenario, if any."""
    with _active_lock:
        return _active[-1] if _active else None


class Scenario:
    """A set of failure rules plus a reproducible record of every intercepted call.

    :param name: label used in reports.
    :param seed: seed for randomized plans; defaults to a stable hash of ``name``.
    :param unmatched: ``"pass"`` lets calls no rule matches through; ``"error"``
        raises :class:`UnmatchedCallError` instead (useful to forbid real network).
    :param sleep: function used by :class:`Delay` in sync code (default ``time.sleep``).
    :param async_sleep: coroutine function used by :class:`Delay` in async code.
    :param clock: monotonic clock used for call timestamps.
    """

    def __init__(
        self,
        name: str = "scenario",
        *,
        seed: int | str | None = None,
        unmatched: Literal["pass", "error"] = "pass",
        sleep: Callable[[float], Any] | None = None,
        async_sleep: Callable[[float], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if unmatched not in ("pass", "error"):
            raise ValueError("unmatched must be 'pass' or 'error'")
        self.name = name
        self.seed: int | str = zlib.crc32(name.encode()) if seed is None else seed
        self.unmatched = unmatched
        self.sleep = sleep or time.sleep
        self.async_sleep = async_sleep
        self.clock = clock
        self.rules: list[Rule] = []
        self._calls: list[CallRecord] = []
        self._lock = threading.Lock()
        self._t0 = clock()

    # -- building -------------------------------------------------------

    def _unique(self, name: str) -> str:
        existing = {r.name for r in self.rules}
        if name not in existing:
            return name
        i = 2
        while f"{name} #{i}" in existing:
            i += 1
        return f"{name} #{i}"

    def http(
        self,
        plan: Plan | Fault | Sequence[Fault],
        *,
        method: str | None = None,
        url: Matcher | None = None,
        host: Matcher | None = None,
        path: Matcher | None = None,
        when: Callable[[RequestInfo], bool] | None = None,
        name: str | None = None,
    ) -> Rule:
        """Add a rule for HTTP requests. Matchers are exact strings, globs or regexes.

        Rules are evaluated in the order they were added; the first match wins.
        """
        if name is None:
            parts = [method.upper() if method else "*"]
            target = url if url is not None else host if host is not None else path
            parts.append(target.pattern if isinstance(target, re.Pattern) else (target or "*"))
            if host is not None and url is None and path is not None:
                parts.append(path.pattern if isinstance(path, re.Pattern) else path)
            name = " ".join(parts)
        rule = Rule(
            self,
            as_plan(plan),
            name=self._unique(name),
            kind="http",
            method=method,
            url=url,
            host=host,
            path=path,
            when=when,
        )
        self.rules.append(rule)
        return rule

    def point(
        self,
        point: str,
        plan: Plan | Fault | Sequence[Fault],
        *,
        when: Callable[[RequestInfo], bool] | None = None,
        name: str | None = None,
    ) -> Rule:
        """Add a rule for a named generic fault point (see :func:`fault_point`)."""
        rule = Rule(
            self,
            as_plan(plan),
            name=self._unique(name or point),
            kind="point",
            point=point,
            when=when,
        )
        self.rules.append(rule)
        return rule

    # -- engine ---------------------------------------------------------

    def decide(self, info: RequestInfo) -> CallRecord:
        """Pick the outcome for ``info`` and record the call. Thread-safe."""
        with self._lock:
            rule = next((r for r in self.rules if r.matches(info)), None)
            if rule is None:
                rule_name, rule_call = None, None
                fault: Fault = PASS
            else:
                rule_call, fault = rule._next()
                rule_name = rule.name
            record = CallRecord(
                index=len(self._calls),
                kind=info.kind,
                target=info.target,
                method=info.method,
                rule=rule_name,
                rule_call=rule_call,
                fault=fault,
                started_at=self.clock() - self._t0,
                thread=threading.current_thread().name,
            )
            self._calls.append(record)
        if rule is None and self.unmatched == "error":
            record.error = "UnmatchedCallError"
            record.ended_at = record.started_at
            label = f"{info.method} {info.target}" if info.kind == "http" else info.target
            raise UnmatchedCallError(
                f"scenario {self.name!r} has no rule matching {info.kind} call {label}"
            )
        return record

    def finish(
        self, record: CallRecord, *, status: int | None = None, error: BaseException | None = None
    ) -> None:
        record.ended_at = self.clock() - self._t0
        record.status = status
        if error is not None:
            record.error = type(error).__name__

    # -- lifecycle ------------------------------------------------------

    def reset(self) -> None:
        """Clear call history and rewind every rule's plan (rules are kept)."""
        with self._lock:
            self._calls.clear()
            for r in self.rules:
                r._count = 0
                r.reseed()
            self._t0 = self.clock()

    def clear(self) -> None:
        """Remove all rules and history."""
        with self._lock:
            self.rules.clear()
            self._calls.clear()

    def activate(self) -> None:
        """Make this the active scenario and patch installed HTTP clients."""
        from . import _patching

        with _active_lock:
            _active.append(self)
        _patching.install()

    def deactivate(self) -> None:
        from . import _patching

        with _active_lock:
            if self in _active:
                # Remove the most recent activation of this scenario.
                del _active[len(_active) - 1 - _active[::-1].index(self)]
        _patching.uninstall()

    @contextmanager
    def active(self) -> Iterator[Scenario]:
        """Context manager form of :meth:`activate` / :meth:`deactivate`."""
        self.activate()
        try:
            yield self
        finally:
            self.deactivate()

    def __enter__(self) -> Scenario:
        self.activate()
        return self

    def __exit__(self, *exc: object) -> None:
        self.deactivate()

    # -- inspection -----------------------------------------------------

    @property
    def calls(self) -> list[CallRecord]:
        with self._lock:
            return list(self._calls)

    @property
    def unmatched_calls(self) -> list[CallRecord]:
        return [c for c in self.calls if c.rule is None]

    @property
    def faults_injected(self) -> int:
        return sum(1 for c in self.calls if c.fault != PASS)

    def rule(self, name: str) -> Rule:
        for r in self.rules:
            if r.name == name:
                return r
        raise KeyError(f"no rule named {name!r}; rules: {[r.name for r in self.rules]}")

    def assert_all_triggered(self) -> None:
        """Every rule matched at least one call (catches mistyped URLs)."""
        idle = [r.name for r in self.rules if r.call_count == 0]
        if idle:
            raise FaultAssertionError(f"rules never matched any call: {idle}\n\n{self.summary()}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.name,
            "seed": self.seed,
            "unmatched": self.unmatched,
            "rules": [
                {
                    "name": r.name,
                    "kind": r.kind,
                    "plan": r.plan.describe(),
                    "calls": r.call_count,
                    "faults_injected": r.faults_injected,
                }
                for r in self.rules
            ],
            "calls": [c.to_dict() for c in self.calls],
            "totals": {
                "calls": len(self.calls),
                "faults_injected": self.faults_injected,
                "unmatched": len(self.unmatched_calls),
            },
        }

    def summary(self, *, rule: str | None = None) -> str:
        from ._report import render_text

        return render_text(self, rule=rule)

    def __repr__(self) -> str:
        return f"<Scenario {self.name!r} seed={self.seed!r} rules={len(self.rules)}>"
