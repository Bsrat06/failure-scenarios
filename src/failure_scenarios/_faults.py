"""Fault outcomes: what happens to a single intercepted call.

Faults are plain, immutable values. They describe an outcome; the HTTP-client
integrations and generic fault points decide how to realise it for their
client. Nothing here performs I/O.
"""

from __future__ import annotations

import json as _json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any, Literal

__all__ = [
    "PASS",
    "ConnectionFailure",
    "Delay",
    "Duplicate",
    "Fault",
    "LostResponse",
    "Passthrough",
    "Raise",
    "Respond",
    "Return",
    "Timeout",
    "empty_body",
    "http_error",
    "malformed_json",
    "rate_limited",
    "service_unavailable",
]

TimeoutPhase = Literal["connect", "read", "write", "pool"]


class Fault:
    """Base class for every outcome a plan can produce."""

    def describe(self) -> str:
        return type(self).__name__


@dataclass(frozen=True)
class Passthrough(Fault):
    """Let the call proceed untouched to the real dependency."""

    def describe(self) -> str:
        return "pass through"


PASS = Passthrough()


@dataclass(frozen=True)
class Respond(Fault):
    """Return a simulated HTTP response without contacting the dependency.

    This is an application-level response: the client receives a well-formed
    HTTP response. It does not simulate any network condition.
    """

    status: int = 200
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes | str | None = None
    json: Any = None

    def __post_init__(self) -> None:
        if self.body is not None and self.json is not None:
            raise ValueError("Respond accepts either body= or json=, not both")
        if not 100 <= self.status <= 599:
            raise ValueError(f"invalid HTTP status code: {self.status}")

    def content(self) -> bytes:
        if self.json is not None:
            return _json.dumps(self.json).encode()
        if isinstance(self.body, str):
            return self.body.encode()
        return self.body or b""

    def header_items(self) -> dict[str, str]:
        headers = dict(self.headers)
        if self.json is not None and not any(k.lower() == "content-type" for k in headers):
            headers["Content-Type"] = "application/json"
        return headers

    @property
    def reason(self) -> str:
        try:
            return HTTPStatus(self.status).phrase
        except ValueError:
            return ""

    def describe(self) -> str:
        return f"HTTP {self.status}" + (f" {self.reason}" if self.reason else "")


@dataclass(frozen=True)
class Timeout(Fault):
    """Raise the client's own timeout exception for the given phase.

    The exception is raised by the client integration immediately (or after an
    enclosing :class:`Delay`). No socket is opened or held: this simulates the
    error your code observes, not real network latency.
    """

    phase: TimeoutPhase = "read"

    def __post_init__(self) -> None:
        if self.phase not in ("connect", "read", "write", "pool"):
            raise ValueError(f"invalid timeout phase: {self.phase!r}")

    def describe(self) -> str:
        return f"{self.phase} timeout (simulated)"


@dataclass(frozen=True)
class ConnectionFailure(Fault):
    """Raise the client's connection-error exception (request never sent)."""

    message: str = "Connection refused (simulated)"

    def describe(self) -> str:
        return "connection failure (simulated)"


@dataclass(frozen=True)
class LostResponse(Fault):
    """Send the request for real, then discard the response and raise.

    The dependency *does* process the request, but the caller sees a transport
    error. This is the scenario that exposes non-idempotent retries.
    """

    def describe(self) -> str:
        return "request delivered, response lost"


@dataclass(frozen=True)
class Duplicate(Fault):
    """Deliver the request ``times`` times and return the final response.

    Simulates a duplicated delivery (for example a proxy or client retrying
    behind your back) so you can verify idempotency handling.
    """

    times: int = 2

    def __post_init__(self) -> None:
        if self.times < 2:
            raise ValueError("Duplicate.times must be at least 2")

    def describe(self) -> str:
        return f"request delivered {self.times}x"


@dataclass(frozen=True)
class Delay(Fault):
    """Wait ``seconds`` (really, using the scenario's sleep function), then apply ``then``."""

    seconds: float
    then: Fault = PASS

    def __post_init__(self) -> None:
        if self.seconds < 0:
            raise ValueError("Delay.seconds must be >= 0")
        if isinstance(self.then, Delay):
            raise ValueError("nested Delay is not supported; add the seconds together")

    def describe(self) -> str:
        return f"delay {self.seconds:g}s, then {self.then.describe()}"


@dataclass(frozen=True)
class Raise(Fault):
    """Raise an arbitrary exception (instance, or factory returning a fresh one)."""

    exception: BaseException | Callable[[], BaseException]

    def build(self) -> BaseException:
        exc = self.exception
        return exc if isinstance(exc, BaseException) else exc()

    def describe(self) -> str:
        exc = self.exception
        name = (
            type(exc).__name__
            if isinstance(exc, BaseException)
            else getattr(exc, "__name__", "exception")
        )
        return f"raise {name}"


@dataclass(frozen=True)
class Return(Fault):
    """Return ``value`` from a generic fault point instead of calling the function."""

    value: Any = None

    def describe(self) -> str:
        return f"return {self.value!r}"[:80]


def http_error(
    status: int,
    *,
    body: bytes | str | None = None,
    json: Any = None,
    headers: Mapping[str, str] | None = None,
) -> Respond:
    """A simulated HTTP error response with the given status."""
    return Respond(status=status, body=body, json=json, headers=dict(headers or {}))


def rate_limited(retry_after: float | None = None, *, json: Any = None) -> Respond:
    """HTTP 429, optionally with a ``Retry-After`` header (seconds)."""
    headers = {} if retry_after is None else {"Retry-After": f"{retry_after:g}"}
    return Respond(status=429, headers=headers, json=json)


def service_unavailable(retry_after: float | None = None, *, json: Any = None) -> Respond:
    """HTTP 503, optionally with a ``Retry-After`` header (seconds)."""
    headers = {} if retry_after is None else {"Retry-After": f"{retry_after:g}"}
    return Respond(status=503, headers=headers, json=json)


def malformed_json(status: int = 200, body: str = '{"id": "ch_1", "amount": ') -> Respond:
    """A response that claims to be JSON but cannot be parsed."""
    return Respond(status=status, body=body, headers={"Content-Type": "application/json"})


def empty_body(status: int = 200, content_type: str = "application/json") -> Respond:
    """A response with the right content type and no body at all."""
    return Respond(status=status, body=b"", headers={"Content-Type": content_type})
