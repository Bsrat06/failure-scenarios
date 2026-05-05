from __future__ import annotations

import pytest
import requests

import failure_scenarios as fs
from failure_scenarios.requests import FaultAdapter


def test_adapter_outcomes(upstream) -> None:  # type: ignore[no-untyped-def]
    s = fs.Scenario(sleep=lambda _: None)
    rule = s.http(
        fs.sequence(
            fs.http_error(503, json={"error": "down"}, headers={"Retry-After": "1"}),
            fs.Timeout("connect"),
            fs.Timeout("read"),
            fs.Timeout("write"),
            fs.ConnectionFailure(),
            fs.LostResponse(),
            fs.Duplicate(),
            fs.Delay(2, then=fs.empty_body()),
        ),
        url=upstream.url + "/*",
    )
    session = requests.Session()
    session.mount("http://", FaultAdapter(s))
    url = upstream.url + "/charge"
    r = session.post(url, json={"a": 1})
    assert (
        r.status_code == 503 and r.json() == {"error": "down"} and r.reason == "Service Unavailable"
    )
    assert r.headers["retry-after"] == "1"
    with pytest.raises(requests.ConnectTimeout):
        session.post(url)
    with pytest.raises(requests.ReadTimeout):
        session.post(url)
    with pytest.raises(requests.Timeout):
        session.post(url)
    with pytest.raises(requests.ConnectionError):
        session.post(url)
    assert upstream.received == []
    with pytest.raises(requests.ConnectionError):
        session.post(url)
    assert len(upstream.received) == 1
    assert session.post(url).json()["n"] == 3
    assert session.post(url).content == b""
    assert session.post(url).json()["n"] == 4
    rule.assert_called(9)


def test_activation_patches_requests(upstream) -> None:  # type: ignore[no-untyped-def]
    s = fs.Scenario()
    rule = s.http(fs.fail_times(2, fs.rate_limited()), method="GET", host="127.0.0.1")
    with s:
        statuses = [requests.get(upstream.url).status_code for _ in range(3)]
        # mounted adapter under active patching applies the rule only once
        session = requests.Session()
        session.mount("http://", FaultAdapter(s))
        assert session.get(upstream.url).status_code == 200
    assert statuses == [429, 429, 200]
    assert rule.call_count == 4
    assert requests.get(upstream.url).status_code == 200
    assert len(upstream.received) == 3
