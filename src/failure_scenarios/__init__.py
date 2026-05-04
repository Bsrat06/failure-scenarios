"""Deterministic, reproducible dependency-failure scenarios for tests.

The core has no third-party dependencies. HTTPX, Requests and pytest
support live in optional modules that are only imported when used.
"""

from __future__ import annotations

from ._faults import (
    PASS,
    ConnectionFailure,
    Delay,
    Duplicate,
    Fault,
    LostResponse,
    Passthrough,
    Raise,
    Respond,
    Return,
    Timeout,
    empty_body,
    http_error,
    malformed_json,
    rate_limited,
    service_unavailable,
)
from ._plans import Plan, always, cycle, fail_after, fail_times, intermittent, sequence
from ._points import fault_point, inject, inject_async
from ._report import to_json
from ._scenario import (
    CallRecord,
    FaultAssertionError,
    RequestInfo,
    Rule,
    Scenario,
    UnmatchedCallError,
    active_scenario,
)

__version__ = "0.1.0"

__all__ = [
    "PASS",
    "CallRecord",
    "ConnectionFailure",
    "Delay",
    "Duplicate",
    "Fault",
    "FaultAssertionError",
    "LostResponse",
    "Passthrough",
    "Plan",
    "Raise",
    "RequestInfo",
    "Respond",
    "Return",
    "Rule",
    "Scenario",
    "Timeout",
    "UnmatchedCallError",
    "active_scenario",
    "always",
    "cycle",
    "empty_body",
    "fail_after",
    "fail_times",
    "fault_point",
    "http_error",
    "inject",
    "inject_async",
    "intermittent",
    "malformed_json",
    "rate_limited",
    "sequence",
    "service_unavailable",
    "to_json",
]
