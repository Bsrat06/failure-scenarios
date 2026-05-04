"""Plans decide which fault applies to the n-th matching call of a rule.

A plan is a pure function of the rule's call index and a seeded random
generator, so a scenario replays identically for the same seed.
"""

from __future__ import annotations

import random
from collections.abc import Sequence as _Seq
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ._faults import PASS, Fault

__all__ = [
    "Plan",
    "always",
    "cycle",
    "fail_after",
    "fail_times",
    "intermittent",
    "sequence",
]


@runtime_checkable
class Plan(Protocol):
    def outcome(self, index: int, rng: random.Random) -> Fault:
        """Return the fault for the zero-based ``index``-th matching call."""
        ...

    def describe(self) -> str: ...


@dataclass(frozen=True)
class _Always:
    fault: Fault

    def outcome(self, index: int, rng: random.Random) -> Fault:
        return self.fault

    def describe(self) -> str:
        return f"always {self.fault.describe()}"


@dataclass(frozen=True)
class _Sequence:
    outcomes: tuple[Fault, ...]
    then: Fault

    def outcome(self, index: int, rng: random.Random) -> Fault:
        return self.outcomes[index] if index < len(self.outcomes) else self.then

    def describe(self) -> str:
        steps = ", ".join(f.describe() for f in self.outcomes)
        return f"sequence [{steps}] then {self.then.describe()}"


@dataclass(frozen=True)
class _Cycle:
    outcomes: tuple[Fault, ...]

    def outcome(self, index: int, rng: random.Random) -> Fault:
        return self.outcomes[index % len(self.outcomes)]

    def describe(self) -> str:
        return "cycle [" + ", ".join(f.describe() for f in self.outcomes) + "]"


@dataclass(frozen=True)
class _Intermittent:
    fault: Fault
    rate: float
    otherwise: Fault

    def outcome(self, index: int, rng: random.Random) -> Fault:
        return self.fault if rng.random() < self.rate else self.otherwise

    def describe(self) -> str:
        return f"{self.fault.describe()} with probability {self.rate:g}"


def _check_faults(outcomes: _Seq[Fault]) -> tuple[Fault, ...]:
    for f in outcomes:
        if not isinstance(f, Fault):
            raise TypeError(f"expected a Fault, got {f!r}")
    return tuple(outcomes)


def always(fault: Fault) -> Plan:
    """Apply ``fault`` to every matching call."""
    (fault,) = _check_faults([fault])
    return _Always(fault)


def sequence(*outcomes: Fault, then: Fault = PASS) -> Plan:
    """Apply ``outcomes`` in order, one per call, then ``then`` forever."""
    if not outcomes:
        raise ValueError("sequence() needs at least one outcome")
    return _Sequence(_check_faults(outcomes), _check_faults([then])[0])


def cycle(*outcomes: Fault) -> Plan:
    """Repeat ``outcomes`` in order indefinitely."""
    if not outcomes:
        raise ValueError("cycle() needs at least one outcome")
    return _Cycle(_check_faults(outcomes))


def fail_times(n: int, fault: Fault, *, then: Fault = PASS) -> Plan:
    """Fail the first ``n`` calls with ``fault``, then succeed (recovery)."""
    if n < 1:
        raise ValueError("fail_times() needs n >= 1")
    return _Sequence((fault,) * n, then)


def fail_after(n: int, fault: Fault) -> Plan:
    """Let the first ``n`` calls through, then fail every later call."""
    if n < 0:
        raise ValueError("fail_after() needs n >= 0")
    return always(fault) if n == 0 else _Sequence((PASS,) * n, fault)


def intermittent(fault: Fault, rate: float, *, otherwise: Fault = PASS) -> Plan:
    """Apply ``fault`` to each call with probability ``rate`` (seeded, reproducible)."""
    if not 0.0 <= rate <= 1.0:
        raise ValueError("rate must be between 0 and 1")
    return _Intermittent(fault, rate, otherwise)


def as_plan(value: Plan | Fault | _Seq[Fault]) -> Plan:
    """Coerce a fault (→ always) or list of faults (→ sequence) into a plan."""
    if isinstance(value, Fault):
        return always(value)
    if isinstance(value, Plan):
        return value
    if isinstance(value, (list, tuple)):
        return sequence(*value)
    raise TypeError(f"expected a Plan, Fault or list of Faults, got {value!r}")
