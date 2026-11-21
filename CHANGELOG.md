# Changelog

All notable changes are documented here. The project follows [Semantic Versioning](https://semver.org/).

## 0.1.0 (unreleased)

- Fault outcomes: `Respond`, `Timeout`, `ConnectionFailure`, `LostResponse`, `Duplicate`, `Delay`, `Raise`, `Return`, `PASS`, with HTTP helpers.
- Plans: `always`, `sequence`, `fail_times`, `fail_after`, `cycle`, and seeded `intermittent`.
- `Scenario` with ordered rules, thread-safe call history, reset, nesting and strict unmatched mode.
- Assertions: call counts, retries, minimum intervals, backoff, untriggered rules.
- HTTPX sync and async transports, a Requests adapter, and process-wide transport patching.
- Generic fault points (`fault_point`, `inject`, `inject_async`).
- pytest plugin: `fault_scenario` fixture, marker, `--fault-seed`, `--fault-report`, JUnit properties, and a failure section with reproduction command.
