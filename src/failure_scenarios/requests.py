"""Requests integration: a transport adapter that applies a scenario.

Mount it on a session::

    session.mount("https://", FaultAdapter(scenario))

or activate the scenario (``with scenario:``) to patch ``HTTPAdapter.send``
process-wide. Requests has no write/pool timeout exceptions, so those phases
raise ``requests.Timeout``.
"""

from __future__ import annotations

import io
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import requests
from requests.adapters import BaseAdapter, HTTPAdapter
from requests.structures import CaseInsensitiveDict
from requests.utils import get_encoding_from_headers

from ._faults import (
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
)
from ._patching import bypass, bypassed
from ._scenario import RequestInfo, Scenario, active_scenario

__all__ = ["FaultAdapter"]

_TIMEOUTS: dict[str, type[requests.exceptions.RequestException]] = {
    "connect": requests.exceptions.ConnectTimeout,
    "read": requests.exceptions.ReadTimeout,
    "write": requests.exceptions.Timeout,
    "pool": requests.exceptions.Timeout,
}


def _info(request: requests.PreparedRequest) -> RequestInfo:
    body = request.body
    content = body.encode() if isinstance(body, str) else body if isinstance(body, bytes) else None
    return RequestInfo(
        kind="http",
        target=request.url or "",
        method=request.method or "",
        headers=dict(request.headers),
        content=content,
    )


def build_response(fault: Respond, request: requests.PreparedRequest) -> requests.Response:
    response = requests.Response()
    response.status_code = fault.status
    response.reason = fault.reason
    response.headers = CaseInsensitiveDict(fault.header_items())
    content = fault.content()
    response._content = content
    response.raw = io.BytesIO(content)
    response.url = request.url or ""
    response.request = request
    response.encoding = get_encoding_from_headers(response.headers)
    response.elapsed = timedelta(0)
    return response


def _immediate(fault: Fault, request: requests.PreparedRequest) -> requests.Response:
    if isinstance(fault, Respond):
        return build_response(fault, request)
    if isinstance(fault, Timeout):
        raise _TIMEOUTS[fault.phase](fault.describe(), request=request)
    if isinstance(fault, ConnectionFailure):
        raise requests.exceptions.ConnectionError(fault.message, request=request)
    if isinstance(fault, Raise):
        raise fault.build()
    if isinstance(fault, Return):
        raise TypeError("Return() only applies to fault points; use Respond() for HTTP rules")
    raise TypeError(f"unsupported fault for Requests: {fault!r}")


def handle(
    scenario: Scenario,
    request: requests.PreparedRequest,
    forward: Callable[[], requests.Response],
) -> requests.Response:
    record = scenario.decide(_info(request))
    fault = record.fault
    try:
        if isinstance(fault, Delay):
            (scenario.sleep or time.sleep)(fault.seconds)
            fault = fault.then
        if isinstance(fault, Passthrough):
            response = forward()
        elif isinstance(fault, (LostResponse, Duplicate)):
            sends = 1 if isinstance(fault, LostResponse) else fault.times - 1
            for _ in range(sends):
                discarded = forward()
                discarded.close()
            if isinstance(fault, LostResponse):
                raise requests.exceptions.ConnectionError(
                    "response lost after request was delivered (simulated)", request=request
                )
            response = forward()
        else:
            response = _immediate(fault, request)
    except BaseException as exc:
        scenario.finish(record, error=exc)
        raise
    scenario.finish(record, status=response.status_code)
    return response


class FaultAdapter(HTTPAdapter):
    """Adapter applying ``scenario``; forwards to ``wrapped`` or to a real ``HTTPAdapter``."""

    def __init__(
        self, scenario: Scenario, wrapped: BaseAdapter | None = None, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self.scenario = scenario
        self.wrapped = wrapped

    def send(
        self, request: requests.PreparedRequest, *args: Any, **kwargs: Any
    ) -> requests.Response:
        def forward() -> requests.Response:
            with bypass():
                if self.wrapped is not None:
                    return self.wrapped.send(request, *args, **kwargs)
                return super(FaultAdapter, self).send(request, *args, **kwargs)

        return handle(self.scenario, request, forward)

    def close(self) -> None:
        if self.wrapped is not None:
            self.wrapped.close()
        super().close()


def patched_send(original: Any) -> Any:
    def send(
        self: HTTPAdapter, request: requests.PreparedRequest, *args: Any, **kwargs: Any
    ) -> requests.Response:
        scenario = active_scenario()
        if scenario is None or bypassed():
            return original(self, request, *args, **kwargs)  # type: ignore[no-any-return]

        def forward() -> requests.Response:
            with bypass():
                return original(self, request, *args, **kwargs)  # type: ignore[no-any-return]

        return handle(scenario, request, forward)

    return send
