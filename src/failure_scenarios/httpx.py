"""HTTPX integration: sync and async transports that apply a scenario.

Use the transports explicitly::

    client = httpx.Client(transport=FaultTransport(scenario))

or activate the scenario (``with scenario:``) to patch HTTPX's default
transports process-wide.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

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

__all__ = ["AsyncFaultTransport", "FaultTransport"]

_TIMEOUTS: dict[str, type[httpx.TimeoutException]] = {
    "connect": httpx.ConnectTimeout,
    "read": httpx.ReadTimeout,
    "write": httpx.WriteTimeout,
    "pool": httpx.PoolTimeout,
}


def _info(request: httpx.Request) -> RequestInfo:
    try:
        content: bytes | None = request.content
    except httpx.RequestNotRead:
        content = None
    return RequestInfo(
        kind="http",
        target=str(request.url),
        method=request.method,
        headers=dict(request.headers),
        content=content,
    )


def _immediate(fault: Fault, request: httpx.Request) -> httpx.Response:
    """Outcomes that never touch the network."""
    if isinstance(fault, Respond):
        return httpx.Response(
            fault.status, headers=fault.header_items(), content=fault.content(), request=request
        )
    if isinstance(fault, Timeout):
        raise _TIMEOUTS[fault.phase](fault.describe(), request=request)
    if isinstance(fault, ConnectionFailure):
        raise httpx.ConnectError(fault.message, request=request)
    if isinstance(fault, Raise):
        raise fault.build()
    if isinstance(fault, Return):
        raise TypeError("Return() only applies to fault points; use Respond() for HTTP rules")
    raise TypeError(f"unsupported fault for HTTPX: {fault!r}")


def _lost(request: httpx.Request) -> httpx.ReadError:
    return httpx.ReadError("response lost after request was delivered (simulated)", request=request)


def handle(
    scenario: Scenario,
    request: httpx.Request,
    forward: Callable[[httpx.Request], httpx.Response],
) -> httpx.Response:
    record = scenario.decide(_info(request))
    fault = record.fault
    try:
        if isinstance(fault, Delay):
            scenario.sleep(fault.seconds)
            fault = fault.then
        if isinstance(fault, Passthrough):
            response = forward(request)
        elif isinstance(fault, (LostResponse, Duplicate)):
            sends = 1 if isinstance(fault, LostResponse) else fault.times - 1
            for _ in range(sends):
                discarded = forward(request)
                discarded.read()
                discarded.close()
            if isinstance(fault, LostResponse):
                raise _lost(request)
            response = forward(request)
        else:
            response = _immediate(fault, request)
    except BaseException as exc:
        scenario.finish(record, error=exc)
        raise
    scenario.finish(record, status=response.status_code)
    return response


async def handle_async(
    scenario: Scenario,
    request: httpx.Request,
    forward: Callable[[httpx.Request], Awaitable[httpx.Response]],
) -> httpx.Response:
    record = scenario.decide(_info(request))
    fault = record.fault
    try:
        if isinstance(fault, Delay):
            await (scenario.async_sleep or asyncio.sleep)(fault.seconds)
            fault = fault.then
        if isinstance(fault, Passthrough):
            response = await forward(request)
        elif isinstance(fault, (LostResponse, Duplicate)):
            sends = 1 if isinstance(fault, LostResponse) else fault.times - 1
            for _ in range(sends):
                discarded = await forward(request)
                await discarded.aread()
                await discarded.aclose()
            if isinstance(fault, LostResponse):
                raise _lost(request)
            response = await forward(request)
        else:
            response = _immediate(fault, request)
    except BaseException as exc:
        scenario.finish(record, error=exc)
        raise
    scenario.finish(record, status=response.status_code)
    return response


class FaultTransport(httpx.BaseTransport):
    """Sync transport applying ``scenario``; forwards to ``wrapped`` (default: real network)."""

    def __init__(self, scenario: Scenario, wrapped: httpx.BaseTransport | None = None) -> None:
        self.scenario = scenario
        self.wrapped = wrapped if wrapped is not None else httpx.HTTPTransport()

    def _forward(self, request: httpx.Request) -> httpx.Response:
        with bypass():
            return self.wrapped.handle_request(request)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return handle(self.scenario, request, self._forward)

    def close(self) -> None:
        self.wrapped.close()


class AsyncFaultTransport(httpx.AsyncBaseTransport):
    """Async transport applying ``scenario``; forwards to ``wrapped`` (default: real network)."""

    def __init__(self, scenario: Scenario, wrapped: httpx.AsyncBaseTransport | None = None) -> None:
        self.scenario = scenario
        self.wrapped = wrapped if wrapped is not None else httpx.AsyncHTTPTransport()

    async def _forward(self, request: httpx.Request) -> httpx.Response:
        with bypass():
            return await self.wrapped.handle_async_request(request)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await handle_async(self.scenario, request, self._forward)

    async def aclose(self) -> None:
        await self.wrapped.aclose()


def patched_sync(original: Any) -> Any:
    def handle_request(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        scenario = active_scenario()
        if scenario is None or bypassed():
            return original(self, request)  # type: ignore[no-any-return]

        def forward(req: httpx.Request) -> httpx.Response:
            with bypass():
                return original(self, req)  # type: ignore[no-any-return]

        return handle(scenario, request, forward)

    return handle_request


def patched_async(original: Any) -> Any:
    async def handle_async_request(
        self: httpx.AsyncHTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        scenario = active_scenario()
        if scenario is None or bypassed():
            return await original(self, request)  # type: ignore[no-any-return]

        async def forward(req: httpx.Request) -> httpx.Response:
            with bypass():
                return await original(self, req)  # type: ignore[no-any-return]

        return await handle_async(scenario, request, forward)

    return handle_async_request
