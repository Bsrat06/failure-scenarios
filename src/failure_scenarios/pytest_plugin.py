"""pytest plugin: the ``fault_scenario`` fixture, reproducible seeds and reports.

Registered automatically through the ``pytest11`` entry point.
"""

from __future__ import annotations

import json
import zlib
from collections.abc import Generator
from typing import Any

import pytest

from ._report import write_json_report
from ._scenario import Scenario

_STASH_SCENARIO = pytest.StashKey[Scenario]()
_STASH_ENTRIES = pytest.StashKey[list[dict[str, Any]]]()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("failure-scenarios")
    group.addoption(
        "--fault-seed",
        default=None,
        help="Base seed for randomized fault plans (default: derived from each test id).",
    )
    group.addoption(
        "--fault-report",
        default=None,
        metavar="PATH",
        help="Write a JSON report of every fault_scenario used in the run.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "fault_scenario(seed=None, unmatched='pass'): configure the fault_scenario fixture.",
    )
    config.stash[_STASH_ENTRIES] = []


def _seed_for(request: pytest.FixtureRequest, marker_seed: Any) -> int | str:
    if marker_seed is not None:
        return marker_seed  # type: ignore[no-any-return]
    base = request.config.getoption("--fault-seed")
    key = request.node.nodeid if base is None else f"{base}:{request.node.nodeid}"
    return zlib.crc32(key.encode())


@pytest.fixture
def fault_scenario(request: pytest.FixtureRequest) -> Generator[Scenario, None, None]:
    """An active :class:`Scenario` for this test, isolated and cleaned up afterwards."""
    marker = request.node.get_closest_marker("fault_scenario")
    options = dict(marker.kwargs) if marker else {}
    seed = _seed_for(request, options.pop("seed", None))
    scenario = Scenario(request.node.nodeid, seed=seed, **options)
    request.node.stash[_STASH_SCENARIO] = scenario
    scenario.activate()
    try:
        yield scenario
    finally:
        scenario.deactivate()
        data = scenario.to_dict()
        totals = data["totals"]
        request.node.user_properties.append(
            ("fault_scenario", json.dumps({"seed": scenario.seed, **totals}))
        )
        request.config.stash[_STASH_ENTRIES].append({"nodeid": request.node.nodeid, **data})


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    scenario = item.stash.get(_STASH_SCENARIO, None)
    if scenario is not None and report.when == "call" and report.failed:
        reproduce = f"pytest {item.nodeid!r}"
        seed = item.config.getoption("--fault-seed")
        if seed is not None:
            reproduce += f" --fault-seed={seed}"
        report.sections.append(
            ("failure scenario", f"{scenario.summary()}\n\nreproduce: {reproduce}")
        )
    return report


def pytest_sessionfinish(session: pytest.Session) -> None:
    path = session.config.getoption("--fault-report")
    entries = session.config.stash.get(_STASH_ENTRIES, [])
    if path:
        write_json_report(
            path,
            entries,
            meta={"fault_seed": session.config.getoption("--fault-seed")},
        )


def pytest_terminal_summary(terminalreporter: Any, config: pytest.Config) -> None:
    entries = config.stash.get(_STASH_ENTRIES, [])
    if not entries:
        return
    calls = sum(e["totals"]["calls"] for e in entries)
    faults = sum(e["totals"]["simulated"] for e in entries)
    line = (
        f"failure-scenarios: {len(entries)} test(s), {calls} intercepted call(s), "
        f"{faults} simulated outcome(s)"
    )
    path = config.getoption("--fault-report")
    if path:
        line += f"; report: {path}"
    terminalreporter.write_line(line)
