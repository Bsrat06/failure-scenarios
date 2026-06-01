"""A minimal circuit breaker around a Requests-based shipping-rates client.

After ``threshold`` consecutive failures the breaker opens and calls fail
fast with the cached fallback; after ``cooldown`` seconds one trial call is
let through (half-open) and success closes the breaker again.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import requests

RATES_URL = "https://shipping.example/v2/rates"


class CircuitOpen(Exception):
    pass


class Breaker:
    def __init__(
        self,
        threshold: int = 3,
        cooldown: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.threshold = threshold
        self.cooldown = cooldown
        self.clock = clock
        self.failures = 0
        self.opened_at: float | None = None

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half-open" if self.clock() - self.opened_at >= self.cooldown else "open"

    def call(self, func: Callable[[], float]) -> float:
        if self.state == "open":
            raise CircuitOpen
        try:
            result = func()
        except Exception:
            self.failures += 1
            if self.state == "half-open" or self.failures >= self.threshold:
                self.opened_at = self.clock()
            raise
        self.failures = 0
        self.opened_at = None
        return result


class RatesClient:
    FALLBACK_RATE = 9.99

    def __init__(self, breaker: Breaker, session: requests.Session | None = None) -> None:
        self.breaker = breaker
        self.session = session or requests.Session()

    def _fetch(self) -> float:
        response = self.session.get(RATES_URL, timeout=1.0)
        response.raise_for_status()
        return float(response.json()["standard"])

    def standard_rate(self) -> float:
        try:
            return self.breaker.call(self._fetch)
        except (CircuitOpen, requests.RequestException):
            return self.FALLBACK_RATE
