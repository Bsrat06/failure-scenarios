from __future__ import annotations

import json

import pytest

pytest_plugins = ["pytester"]


def test_fixture_isolation_report_and_junit(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        """
        import pytest
        import failure_scenarios as fs

        def call():
            return fs.inject("dep", lambda: "real")

        def test_first(fault_scenario):
            fault_scenario.point("dep", fs.Return("fake"))
            assert call() == "fake"

        def test_isolated(fault_scenario):
            assert fault_scenario.rules == []
            assert call() == "real"

        @pytest.mark.fault_scenario(seed=99, unmatched="error")
        def test_marker(fault_scenario):
            assert fault_scenario.seed == 99
            with pytest.raises(fs.UnmatchedCallError):
                call()

        def test_fails(fault_scenario):
            fault_scenario.point("dep", fs.fail_times(1, fs.Timeout()))
            try:
                call()
            except TimeoutError:
                pass
            assert call() == "never"
        """
    )
    result = pytester.runpytest(
        "-p", "no:cacheprovider", "--fault-report=out/report.json", "--junitxml=junit.xml"
    )
    result.assert_outcomes(passed=3, failed=1)
    result.stdout.fnmatch_lines(
        [
            "*failure scenario*",
            "*dep?0?*read timeout (simulated)*raised TimeoutError*",
            "*reproduce: pytest*test_fails*",
            "failure-scenarios: 4 test?s?, 5 intercepted call?s?, 2 simulated outcome?s?;"
            " report: out/report.json",
        ]
    )
    report = json.loads((pytester.path / "out" / "report.json").read_text())
    assert [t["nodeid"].split("::")[1] for t in report["tests"]] == [
        "test_first",
        "test_isolated",
        "test_marker",
        "test_fails",
    ]
    assert 'name="fault_scenario"' in (pytester.path / "junit.xml").read_text()


def test_seed_option_changes_and_reproduces_seeds(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        """
        def test_seed(fault_scenario):
            print("SEED", fault_scenario.seed)
        """
    )

    def seed(*args: str) -> str:
        out = pytester.runpytest("-s", *args).stdout.str()
        return next(line.split("SEED")[1] for line in out.splitlines() if "SEED" in line)

    assert seed() == seed()
    assert seed("--fault-seed=1") == seed("--fault-seed=1") != seed()
