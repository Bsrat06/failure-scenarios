from __future__ import annotations

import re
import threading

import pytest

import failure_scenarios as fs


def test_plans_produce_expected_sequences() -> None:
    import random

    rng = random.Random(0)
    err = fs.service_unavailable()
    assert [fs.fail_times(2, err).outcome(i, rng) for i in range(4)] == [err, err, fs.PASS, fs.PASS]
    assert [fs.fail_after(1, err).outcome(i, rng) for i in range(3)] == [fs.PASS, err, err]
    a, b = fs.Timeout(), fs.ConnectionFailure()
    assert [fs.cycle(a, b).outcome(i, rng) for i in range(3)] == [a, b, a]
    assert fs.sequence(a, then=b).outcome(5, rng) == b


def test_invalid_values_rejected() -> None:
    with pytest.raises(ValueError):
        fs.Respond(status=99)
    with pytest.raises(ValueError):
        fs.Respond(body="x", json={})
    with pytest.raises(ValueError):
        fs.intermittent(fs.PASS, 1.5)
    with pytest.raises(ValueError):
        fs.Duplicate(times=1)
    with pytest.raises(TypeError):
        fs.sequence("nope")  # type: ignore[arg-type]


def test_intermittent_is_reproducible_per_seed() -> None:
    def run(seed: int) -> list[bool]:
        s = fs.Scenario(seed=seed)
        rule = s.point("p", fs.intermittent(fs.Return("x"), 0.5))
        for _ in range(30):
            with s:
                fs.inject("p", lambda: None)
        return [c.fault != fs.PASS for c in rule.calls]

    assert run(7) == run(7)
    assert run(7) != run(8)
    assert 0 < sum(run(7)) < 30


def test_rule_stream_independent_of_other_rules() -> None:
    def outcomes(extra: bool) -> list[bool]:
        s = fs.Scenario(seed=1)
        rule = s.point("a", fs.intermittent(fs.Return(1), 0.5))
        s.point("b", fs.intermittent(fs.Return(2), 0.5))
        with s:
            for _ in range(20):
                fs.inject("a", lambda: 0)
                if extra:
                    fs.inject("b", lambda: 0)
        return [c.fault != fs.PASS for c in rule.calls]

    assert outcomes(False) == outcomes(True)


def test_fault_point_decorator_sync_and_reset() -> None:
    sent: list[str] = []

    @fs.fault_point("email.send")
    def send(to: str) -> str:
        sent.append(to)
        return "queued"

    assert send("a@x") == "queued"  # no active scenario: untouched
    s = fs.Scenario()
    rule = s.point("email.send", fs.fail_times(2, fs.ConnectionFailure()))
    with s:
        for _ in range(2):
            with pytest.raises(ConnectionError):
                send("b@x")
        assert send("c@x") == "queued"
    rule.assert_called(3)
    rule.assert_retried(2)
    assert [c.result for c in rule.calls] == ["raised ConnectionError"] * 2 + ["completed"]
    s.reset()
    assert rule.call_count == 0 and s.calls == []


def test_point_faults() -> None:
    calls: list[int] = []

    def charge() -> int:
        calls.append(1)
        return len(calls)

    s = fs.Scenario(sleep=lambda _: None)
    s.point(
        "pay",
        fs.sequence(
            fs.Timeout(),
            fs.LostResponse(),
            fs.Duplicate(3),
            fs.Raise(lambda: KeyError("k")),
            fs.Delay(1, then=fs.Return(42)),
        ),
    )
    with s:
        with pytest.raises(TimeoutError):
            fs.inject("pay", charge)
        with pytest.raises(ConnectionError):
            fs.inject("pay", charge)
        assert fs.inject("pay", charge) == 4  # delivered three more times
        with pytest.raises(KeyError):
            fs.inject("pay", charge)
        assert fs.inject("pay", charge) == 42
    with s, pytest.raises(TypeError):
        s.point("http-only", fs.http_error(500))
        fs.inject("http-only", charge)


async def test_async_fault_point() -> None:
    @fs.fault_point("queue.publish")
    async def publish() -> str:
        return "ok"

    s = fs.Scenario()
    s.point("queue.publish", [fs.Timeout(), fs.Duplicate()])
    with s:
        with pytest.raises(TimeoutError):
            await publish()
        assert await publish() == "ok"
        assert await publish() == "ok"


def test_unmatched_error_and_assertion_messages() -> None:
    s = fs.Scenario("strict", unmatched="error")
    rule = s.point("known", fs.PASS)
    with s, pytest.raises(fs.UnmatchedCallError, match="unknown"):
        fs.inject("unknown", lambda: None)
    assert len(s.unmatched_calls) == 1
    with pytest.raises(fs.FaultAssertionError) as info:
        rule.assert_called(times=2)
    assert "expected exactly 2 call(s), got 0" in str(info.value)
    assert "scenario 'strict'" in str(info.value)
    with pytest.raises(fs.FaultAssertionError, match="never matched"):
        s.assert_all_triggered()


def test_timing_assertions_with_fake_clock() -> None:
    now = [0.0]
    s = fs.Scenario(clock=lambda: now[0])
    rule = s.point("p", fs.PASS)
    with s:
        for t in (0.0, 0.1, 0.3, 0.7):
            now[0] = t
            fs.inject("p", lambda: None)
    assert [round(g, 3) for g in rule.intervals()] == [0.1, 0.2, 0.4]
    rule.assert_backoff(factor=2.0, min_first=0.1)
    rule.assert_min_interval(0.1)
    with pytest.raises(fs.FaultAssertionError):
        rule.assert_backoff(factor=3.0)
    with pytest.raises(fs.FaultAssertionError):
        rule.assert_min_interval(0.2)


def test_http_rule_matching() -> None:
    s = fs.Scenario()
    by_glob = s.http(fs.PASS, method="post", url="https://api.example.com/v1/*")
    by_host = s.http(fs.PASS, host="auth.example.com", path=re.compile(r"^/token"))
    by_when = s.http(fs.PASS, when=lambda r: r.headers.get("x-tenant") == "b")

    def info(method: str, url: str, **headers: str) -> fs.RequestInfo:
        return fs.RequestInfo(kind="http", target=url, method=method, headers=headers)

    assert by_glob.matches(info("POST", "https://api.example.com/v1/charges"))
    assert not by_glob.matches(info("GET", "https://api.example.com/v1/charges"))
    assert by_host.matches(info("GET", "https://auth.example.com/token?x=1"))
    assert not by_host.matches(info("GET", "https://auth.example.com/me"))
    assert by_when.matches(info("GET", "https://other/", **{"x-tenant": "b"}))
    exact = s.http(fs.PASS, url="https://api.example.com/v1/charges")
    assert exact.matches(info("GET", "https://api.example.com/v1/charges?limit=1"))
    assert s.http(fs.PASS, url="https://api.example.com/v1/charges").name.endswith("#2")


def test_concurrent_calls_are_counted_exactly() -> None:
    s = fs.Scenario()
    rule = s.point("p", fs.fail_times(50, fs.Return("fail")))
    results: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        for _ in range(25):
            r = fs.inject("p", lambda: "ok")
            with lock:
                results.append(r)

    with s:
        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert rule.call_count == 200
    assert results.count("fail") == 50
    assert sorted(c.index for c in s.calls) == list(range(200))


def test_nested_activation_and_report() -> None:
    outer, inner = fs.Scenario("outer"), fs.Scenario("inner")
    with outer:
        with inner:
            assert fs.active_scenario() is inner
        assert fs.active_scenario() is outer
    assert fs.active_scenario() is None
    s = fs.Scenario("report", seed=3)
    s.point("p", fs.fail_times(1, fs.Return(0)))
    with s:
        fs.inject("p", lambda: 1)
        fs.inject("p", lambda: 1)
    data = s.to_dict()
    assert data["seed"] == 3 and data["totals"] == {
        "calls": 2,
        "faults_injected": 1,
        "unmatched": 0,
    }
    assert '"scenario": "report"' in fs.to_json(s)
    text = s.summary()
    assert "return 0" in text and "pass through" in text
