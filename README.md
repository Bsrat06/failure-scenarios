# failure-scenarios

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

Deterministic, reproducible dependency-failure scenarios for Python tests.

Your code calls payment providers, auth services, email APIs and queues. This
package lets a test say *"the first two charges return 503, the third times
out, then the provider recovers"*, and then check what your code did: how many
attempts it made, how far apart, with which idempotency key, and whether the
fallback kicked in.

```python
import failure_scenarios as fs

def test_checkout_survives_provider_blips(fault_scenario, client):
    provider = fault_scenario.http(
        fs.sequence(fs.service_unavailable(), fs.Timeout("read"),
                    then=fs.Respond(200, json={"id": "ch_1"})),
        method="POST", url="https://payments.example/v1/charges",
    )

    assert client.post("/checkout", json=ORDER).status_code == 200
    provider.assert_retried(2)
```

When a test fails, the report shows exactly what was injected. Here the provider never recovers within the app's three attempts:

```
------------------------------ failure scenario -------------------------------
scenario 'tests/test_checkout.py::test_checkout_recovers' (seed=340277690, unmatched=error)
rules:
  charge                                             3 call(s)  plan: sequence [HTTP 503 Service Unavailable, read timeout (simulated), read timeout (simulated)] then pass through
calls:
    #       t+s  request                                      rule                   injected                           result
    0     0.454  POST https://payments.example/v1/charges     charge[0]              HTTP 503 Service Unavailable       HTTP 503
    1     0.516  POST https://payments.example/v1/charges     charge[1]              read timeout (simulated)           raised ReadTimeout
    2     0.625  POST https://payments.example/v1/charges     charge[2]              read timeout (simulated)           raised ReadTimeout

reproduce: pytest 'tests/test_checkout.py::test_checkout_recovers'
```

## Where it fits

Mocking libraries such as respx, pytest-httpx, responses and requests-mock
replace a dependency with canned answers. They are the right tool for "return
this JSON". This package covers what they leave to hand-written callbacks:

- **Failure over time.** Ordered sequences, fail N times then recover, fail
  after N successes, cycles, and seeded intermittent failure.
- **Transport-level errors raised as the client's own exceptions.** You get
  `httpx.ReadTimeout` and `requests.ConnectTimeout`, the exceptions your retry
  code actually catches.
- **Delivered-but-lost and duplicated requests.** For idempotency testing,
  the request really reaches the upstream before the failure.
- **Behaviour assertions.** Attempt counts, retries, minimum gaps, exponential
  backoff, rules that never matched.
- **Reproducibility.** Every random decision is seeded per test and per rule,
  and the seed is printed with the failure.

It does not implement retries or circuit breakers. Use tenacity, stamina,
pybreaker or your own code for that. This package only lets you test them.

## Install

```bash
pip install failure-scenarios            # core: no dependencies
pip install "failure-scenarios[httpx]"   # + HTTPX sync/async support
pip install "failure-scenarios[requests]"
pip install "failure-scenarios[all]"     # httpx, requests, pytest
```

Python 3.10–3.14. The pytest plugin loads automatically when pytest is installed.

## Concepts

| Concept | What it is |
|---|---|
| **Fault** | One outcome for one call: `Respond`, `Timeout`, `ConnectionFailure`, `LostResponse`, `Duplicate`, `Delay`, `Raise`, `Return`, `PASS`. |
| **Plan** | Which fault the *n*-th matching call gets: `always`, `sequence`, `fail_times`, `fail_after`, `cycle`, `intermittent`. |
| **Rule** | A matcher (method, URL, host, path, predicate, or fault-point name) plus a plan. It records its calls and holds the assertions. |
| **Scenario** | Ordered rules (the first match wins), the call history, a seed, and activation. |

## Faults

| Fault | HTTPX | Requests | Fault point |
|---|---|---|---|
| `Respond(status, headers, body/json)` | `httpx.Response` | `requests.Response` | n/a |
| `Timeout("connect"/"read"/"write"/"pool")` | `ConnectTimeout` / `ReadTimeout` / `WriteTimeout` / `PoolTimeout` | `ConnectTimeout` / `ReadTimeout` / `Timeout` / `Timeout` | `TimeoutError` |
| `ConnectionFailure()` | `ConnectError` | `ConnectionError` | `ConnectionError` |
| `LostResponse()` | request sent, then `ReadError` | request sent, then `ConnectionError` | function runs, then `ConnectionError` |
| `Duplicate(times=2)` | request sent *n* times, last response returned | same | function runs *n* times |
| `Delay(seconds, then=...)` | sleeps, then applies `then` | same | same |
| `Raise(exc)` / `Return(value)` | raises `exc` / n/a | raises `exc` / n/a | raises / returns |

Helpers: `http_error(status)`, `rate_limited(retry_after=)`,
`service_unavailable(retry_after=)`, `malformed_json()`, `empty_body()`.

**About simulated errors.** `Timeout` and `ConnectionFailure` raise the
client's exception straight away. No socket is opened and no real network
latency happens. `Delay` really waits, using the scenario's `sleep` function,
which you can replace. The package does not throttle bandwidth, drop packets
or slow down TLS. For real network faults, use a proxy such as Toxiproxy.

## Using it

### With pytest

```python
@pytest.mark.fault_scenario(unmatched="error")   # optional: forbid unplanned real calls
def test_rates_fallback(fault_scenario):
    rates = fault_scenario.http(fs.always(fs.Timeout("read")), url="https://shipping.example/v2/rates")
    assert get_rate() == FALLBACK
    rates.assert_called(times=3)
```

`fault_scenario` is active for the test and removed afterwards. Each test
gets a fresh scenario and a stable seed taken from its test id.

| Option | Effect |
|---|---|
| `--fault-seed=N` | Change the base seed for every test (still reproducible). |
| `--fault-report=PATH` | Write a JSON report of every scenario in the run. |
| `--junitxml=...` | Each test gets a `fault_scenario` property with its seed and totals. |

### Without pytest

```python
scenario = fs.Scenario("nightly-smoke", seed=42)
scenario.http(fs.intermittent(fs.service_unavailable(), 0.2), host="api.example.com")
with scenario:                    # patches HTTPX and Requests transports
    run_job()
print(scenario.summary())
```

### Explicit transports (no global patching)

```python
from failure_scenarios.httpx import FaultTransport, AsyncFaultTransport
from failure_scenarios.requests import FaultAdapter

client = httpx.Client(transport=FaultTransport(scenario))                # forwards to the real network
client = httpx.Client(transport=FaultTransport(scenario, httpx.MockTransport(handler)))  # or to a mock
session.mount("https://", FaultAdapter(scenario))
```

Activation patches `httpx.HTTPTransport`, `httpx.AsyncHTTPTransport` and
`requests.adapters.HTTPAdapter.send`. Clients built on custom transports, such
as `MockTransport`, respx routes or FastAPI's `TestClient`, are not
intercepted. Wrap those explicitly. If a request passes through both an
explicit transport and a patched one, the scenario is applied only once.

### Non-HTTP dependencies

```python
@fs.fault_point("email.send")
def send_email(to, body): ...

fault_scenario.point("email.send", fs.fail_times(2, fs.ConnectionFailure()))
```

`fs.inject(name, func, *args)` and `fs.inject_async(...)` do the same without
a decorator. When no scenario is active, fault points just call the function.

### Matching

```python
scenario.http(plan, method="POST", url="https://api.example.com/v1/*")   # glob
scenario.http(plan, host="auth.example.com", path=re.compile(r"^/oauth")) # regex
scenario.http(plan, when=lambda req: req.headers.get("x-tenant") == "b") # predicate
```

An exact URL also matches the same URL with a query string.

### Assertions

`rule.assert_called(times= | at_least= | at_most=)`, `assert_not_called()`,
`assert_retried(n)`, `assert_min_interval(seconds)`,
`assert_backoff(factor=, min_first=)`, `rule.intervals()`, `rule.calls`
(with `.request.headers` and `.request.content`), and
`scenario.assert_all_triggered()`. Each failure message includes the call
table.

`scenario.reset()` clears the history and rewinds every plan.
`scenario.to_dict()` and `fs.to_json(scenario)` export the history.

## Examples

- [`examples/fastapi_checkout`](examples/fastapi_checkout): retries with
  backoff, one idempotency key across attempts, giving up, and malformed
  bodies.
- [`examples/circuit_breaker`](examples/circuit_breaker): a Requests client
  with a breaker, covering open, half-open, reopen and fallback.

Both examples run as part of the test suite. Django needs nothing special.
The fixture intercepts outbound HTTPX and Requests calls from views and
services the same way.

## Development

```bash
uv sync
uv run pytest
uv run ruff check src tests examples && uv run mypy
uv run --group docs mkdocs serve
```

## Status

Version 0.1.0 is feature-complete for its scope. Licensed under Apache-2.0.

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.
