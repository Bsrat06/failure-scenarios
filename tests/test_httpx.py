from __future__ import annotations

import httpx
import pytest

import failure_scenarios as fs
from failure_scenarios.httpx import AsyncFaultTransport, FaultTransport


def _mock() -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"n": len(seen)})

    return httpx.MockTransport(handler), seen


def test_explicit_transport_outcomes() -> None:
    mock, seen = _mock()
    s = fs.Scenario(sleep=lambda _: None)
    rule = s.http(
        fs.sequence(
            fs.rate_limited(retry_after=2),
            fs.Timeout("connect"),
            fs.ConnectionFailure(),
            fs.malformed_json(),
            fs.Delay(5, then=fs.Timeout("read")),
        ),
        method="POST",
        url="https://pay.example/charges",
    )
    client = httpx.Client(transport=FaultTransport(s, mock))
    r = client.post("https://pay.example/charges")
    assert r.status_code == 429 and r.headers["Retry-After"] == "2"
    with pytest.raises(httpx.ConnectTimeout):
        client.post("https://pay.example/charges")
    with pytest.raises(httpx.ConnectError):
        client.post("https://pay.example/charges")
    with pytest.raises(ValueError):
        client.post("https://pay.example/charges").json()
    with pytest.raises(httpx.ReadTimeout):
        client.post("https://pay.example/charges")
    assert client.post("https://pay.example/charges").json() == {"n": 1}
    assert client.get("https://pay.example/other").status_code == 200  # unmatched passes
    assert len(seen) == 2
    rule.assert_called(6)
    assert [c.result for c in rule.calls][:2] == ["HTTP 429", "raised ConnectTimeout"]


def test_lost_response_and_duplicate_reach_upstream() -> None:
    mock, seen = _mock()
    s = fs.Scenario()
    s.http([fs.LostResponse(), fs.Duplicate(3)], url="https://pay.example/*")
    client = httpx.Client(transport=FaultTransport(s, mock))
    with pytest.raises(httpx.ReadError):
        client.post("https://pay.example/charges", json={"amount": 5})
    assert len(seen) == 1
    assert client.post("https://pay.example/charges").json() == {"n": 4}
    assert len(seen) == 4


async def test_async_transport() -> None:
    mock, _ = _mock()
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    s = fs.Scenario(async_sleep=fake_sleep)
    rule = s.http(
        fs.sequence(fs.service_unavailable(), fs.Delay(0.5), fs.LostResponse(), fs.Duplicate()),
        host="api.example",
    )
    async with httpx.AsyncClient(transport=AsyncFaultTransport(s, mock)) as client:
        assert (await client.get("https://api.example/a")).status_code == 503
        assert (await client.get("https://api.example/a")).status_code == 200
        with pytest.raises(httpx.ReadError):
            await client.get("https://api.example/a")
        assert (await client.get("https://api.example/a")).json() == {"n": 4}
    assert slept == [0.5]
    assert rule.faults_injected == 4


def test_activation_patches_default_transport(upstream) -> None:  # type: ignore[no-untyped-def]
    s = fs.Scenario(unmatched="error")
    s.http(fs.fail_times(1, fs.http_error(500, json={"error": "boom"})), url=upstream.url + "/*")
    with httpx.Client() as client:
        with s:
            assert client.get(upstream.url + "/x").json() == {"error": "boom"}
            assert client.get(upstream.url + "/x").json()["ok"] is True
            with pytest.raises(fs.UnmatchedCallError):
                client.get("http://127.0.0.1:9/never")
        # deactivated: no rules apply and nothing is recorded
        assert client.get(upstream.url + "/y").status_code == 200
    assert len(upstream.received) == 2
    assert len(s.calls) == 3


async def test_activation_patches_async_and_no_double_apply(upstream) -> None:  # type: ignore[no-untyped-def]
    s = fs.Scenario()
    rule = s.http(fs.fail_times(1, fs.service_unavailable()), host="127.0.0.1")
    with s:
        async with httpx.AsyncClient() as client:
            assert (await client.get(upstream.url)).status_code == 503
            assert (await client.get(upstream.url)).status_code == 200
        # explicit transport + active patching: rule applied once per request
        with httpx.Client(transport=FaultTransport(s)) as client:
            assert client.get(upstream.url).status_code == 200
    assert rule.call_count == 3
