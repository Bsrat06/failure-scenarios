"""Verifying a circuit breaker and its fallback with a Requests client."""

from __future__ import annotations

import pytest

import failure_scenarios as fs

from .breaker import RATES_URL, Breaker, RatesClient

RATE = fs.Respond(200, json={"standard": 4.5})

pytestmark = pytest.mark.fault_scenario(unmatched="error")


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_opens_after_threshold_and_stops_calling_provider(fault_scenario) -> None:
    rates = fault_scenario.http(fs.always(fs.Timeout("read")), url=RATES_URL)
    client = RatesClient(Breaker(threshold=3, cooldown=30, clock=FakeClock()))

    results = [client.standard_rate() for _ in range(10)]

    assert results == [RatesClient.FALLBACK_RATE] * 10
    rates.assert_called(times=3)  # the other 7 calls never left the process
    assert client.breaker.state == "open"


def test_half_open_trial_closes_breaker_on_recovery(fault_scenario) -> None:
    clock = FakeClock()
    rates = fault_scenario.http(
        fs.fail_times(3, fs.service_unavailable(), then=RATE), url=RATES_URL
    )
    client = RatesClient(Breaker(threshold=3, cooldown=30, clock=clock))

    for _ in range(3):
        client.standard_rate()
    assert client.breaker.state == "open"

    clock.now = 31
    assert client.standard_rate() == 4.5
    assert client.breaker.state == "closed"
    rates.assert_called(times=4)


def test_failed_trial_reopens_immediately(fault_scenario) -> None:
    clock = FakeClock()
    rates = fault_scenario.http(fs.fail_times(4, fs.ConnectionFailure(), then=RATE), url=RATES_URL)
    client = RatesClient(Breaker(threshold=3, cooldown=30, clock=clock))

    for _ in range(3):
        client.standard_rate()
    clock.now = 31
    client.standard_rate()  # trial call fails

    assert client.breaker.state == "open"
    client.standard_rate()
    rates.assert_called(times=4)


def test_intermittent_failures_never_surface_to_caller(fault_scenario) -> None:
    fault_scenario.http(
        fs.intermittent(fs.service_unavailable(), 0.3, otherwise=RATE), url=RATES_URL
    )
    client = RatesClient(Breaker(threshold=50))

    rates = {client.standard_rate() for _ in range(40)}

    assert rates == {4.5, RatesClient.FALLBACK_RATE}  # same mix on every run for this test's seed
