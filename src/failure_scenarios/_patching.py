"""Process-wide patching of installed HTTP clients while a scenario is active.

Only the transport layer is patched (``httpx.HTTPTransport``,
``httpx.AsyncHTTPTransport`` and ``requests.adapters.HTTPAdapter``), so
clients, sessions, auth, redirects and event hooks all still run normally.
Custom transports (for example ``httpx.MockTransport``) are not patched; wrap
them explicitly with ``FaultTransport`` instead.
"""

from __future__ import annotations

import importlib.util
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_bypass: ContextVar[bool] = ContextVar("failure_scenarios_bypass", default=False)
_lock = threading.Lock()
_depth = 0
_undo: list[Callable[[], None]] = []


@contextmanager
def bypass() -> Iterator[None]:
    """Calls made inside this block skip patched transports (used when forwarding)."""
    token = _bypass.set(True)
    try:
        yield
    finally:
        _bypass.reset(token)


def bypassed() -> bool:
    return _bypass.get()


def _patch(owner: Any, attr: str, make: Callable[[Any], Any]) -> None:
    original = getattr(owner, attr)
    setattr(owner, attr, make(original))
    _undo.append(lambda: setattr(owner, attr, original))


def _available(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def install() -> None:
    global _depth
    with _lock:
        _depth += 1
        if _depth > 1:
            return
        if _available("httpx"):
            import httpx

            from .httpx import patched_async, patched_sync

            _patch(httpx.HTTPTransport, "handle_request", patched_sync)
            _patch(httpx.AsyncHTTPTransport, "handle_async_request", patched_async)
        if _available("requests"):
            import requests.adapters

            from .requests import patched_send

            _patch(requests.adapters.HTTPAdapter, "send", patched_send)


def uninstall() -> None:
    global _depth
    with _lock:
        if _depth == 0:
            return
        _depth -= 1
        if _depth:
            return
        while _undo:
            _undo.pop()()
