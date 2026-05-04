"""Generic fault points for non-HTTP dependencies (SDK calls, email, queues...)."""

from __future__ import annotations

import asyncio
import functools
import inspect
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar, cast

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
from ._scenario import RequestInfo, Scenario, active_scenario

__all__ = ["fault_point", "inject", "inject_async"]

P = ParamSpec("P")
T = TypeVar("T")


def _unsupported(fault: Fault) -> TypeError:
    return TypeError(f"{fault.describe()} ({type(fault).__name__}) only applies to HTTP rules")


def _run_sync(
    scenario: Scenario, fault: Fault, func: Callable[..., Any], args: Any, kwargs: Any
) -> Any:
    if isinstance(fault, Delay):
        scenario.sleep(fault.seconds)
        fault = fault.then
    if isinstance(fault, Passthrough):
        return func(*args, **kwargs)
    if isinstance(fault, Return):
        return fault.value
    if isinstance(fault, Raise):
        raise fault.build()
    if isinstance(fault, Timeout):
        raise TimeoutError(f"{fault.describe()} at fault point")
    if isinstance(fault, ConnectionFailure):
        raise ConnectionError(fault.message)
    if isinstance(fault, LostResponse):
        func(*args, **kwargs)
        raise ConnectionError("result lost after the call completed (simulated)")
    if isinstance(fault, Duplicate):
        result = None
        for _ in range(fault.times):
            result = func(*args, **kwargs)
        return result
    raise _unsupported(fault)


async def _run_async(
    scenario: Scenario, fault: Fault, func: Callable[..., Awaitable[Any]], args: Any, kwargs: Any
) -> Any:
    if isinstance(fault, Delay):
        await (scenario.async_sleep or asyncio.sleep)(fault.seconds)
        fault = fault.then
    if isinstance(fault, Passthrough):
        return await func(*args, **kwargs)
    if isinstance(fault, LostResponse):
        await func(*args, **kwargs)
        raise ConnectionError("result lost after the call completed (simulated)")
    if isinstance(fault, Duplicate):
        result = None
        for _ in range(fault.times):
            result = await func(*args, **kwargs)
        return result
    if isinstance(fault, Respond):
        raise _unsupported(fault)
    return _run_sync(scenario, fault, func, args, kwargs)


def inject(name: str, func: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Call ``func`` through the fault point ``name`` of the active scenario.

    With no active scenario this is exactly ``func(*args, **kwargs)``.
    """
    scenario = active_scenario()
    if scenario is None:
        return func(*args, **kwargs)
    record = scenario.decide(RequestInfo(kind="point", target=name))
    try:
        result = _run_sync(scenario, record.fault, func, args, kwargs)
    except BaseException as exc:
        scenario.finish(record, error=exc)
        raise
    scenario.finish(record)
    return cast(T, result)


async def inject_async(
    name: str, func: Callable[P, Awaitable[T]], *args: P.args, **kwargs: P.kwargs
) -> T:
    """Async counterpart of :func:`inject`."""
    scenario = active_scenario()
    if scenario is None:
        return await func(*args, **kwargs)
    record = scenario.decide(RequestInfo(kind="point", target=name))
    try:
        result = await _run_async(scenario, record.fault, func, args, kwargs)
    except BaseException as exc:
        scenario.finish(record, error=exc)
        raise
    scenario.finish(record)
    return cast(T, result)


def fault_point(name: str) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Decorator marking a function as fault point ``name``. Works on sync and async functions.

    Outside an active scenario the decorated function behaves exactly as before.
    """

    def decorate(func: Callable[P, T]) -> Callable[P, T]:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                return await inject_async(name, func, *args, **kwargs)

            return cast(Callable[P, T], async_wrapper)

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            return inject(name, func, *args, **kwargs)

        return wrapper

    return decorate
