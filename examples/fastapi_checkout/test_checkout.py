"""Resilience tests for the checkout service using the ``fault_scenario`` fixture.

FastAPI's TestClient uses its own in-process transport, which is not patched;
only the service's outbound call to the payment provider is intercepted.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

import failure_scenarios as fs

from . import app as checkout
from .app import PROVIDER_URL, app

ORDER = {"order_id": "ord_42", "amount_cents": 1999}
PAID = fs.Respond(200, json={"id": "ch_1", "status": "succeeded"})

pytestmark = pytest.mark.fault_scenario(unmatched="error")  # no real network


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_recovers_after_transient_provider_failures(fault_scenario, client, monkeypatch) -> None:
    delays: list[float] = []

    async def record_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(checkout, "sleep", record_sleep)
    provider = fault_scenario.http(
        fs.sequence(fs.service_unavailable(), fs.Timeout("read"), then=PAID),
        method="POST",
        url=PROVIDER_URL,
    )

    response = client.post("/checkout", json=ORDER)

    assert response.json() == {"order_id": "ord_42", "charge_id": "ch_1", "status": "paid"}
    provider.assert_retried(2)
    assert delays == [0.05, 0.1]  # exponential backoff between the three attempts


def test_reuses_one_idempotency_key_across_retries(fault_scenario, client) -> None:
    provider = fault_scenario.http(
        fs.fail_times(2, fs.rate_limited(retry_after=0), then=PAID), url=PROVIDER_URL
    )

    assert client.post("/checkout", json=ORDER).status_code == 200

    provider.assert_called(times=3)
    keys = {c.request.headers["idempotency-key"] for c in provider.calls if c.request}
    assert len(keys) == 1


def test_gives_up_after_three_attempts(fault_scenario, client) -> None:
    provider = fault_scenario.http(fs.always(fs.service_unavailable()), url=PROVIDER_URL)

    response = client.post("/checkout", json=ORDER)

    assert response.status_code == 502
    provider.assert_called(times=3)


def test_connection_failures_are_reported_not_crashed(fault_scenario, client) -> None:
    fault_scenario.http(fs.ConnectionFailure(), url=PROVIDER_URL)

    assert client.post("/checkout", json=ORDER).status_code == 502


def test_malformed_provider_body_is_not_retried(fault_scenario, client) -> None:
    provider = fault_scenario.http(fs.malformed_json(), url=PROVIDER_URL)

    assert client.post("/checkout", json=ORDER).status_code == 502
    provider.assert_called(times=1)
