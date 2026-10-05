"""Tests for the ``--explain-marks`` collection-time mark explanation report."""

from __future__ import annotations

from _pytest.pytester import Pytester


def extract_report_lines(output: str) -> list[str]:
    """Extract the mark explanation report block from pytest output."""
    lines = output.splitlines()
    try:
        start = next(
            i
            for i, line in enumerate(lines)
            if line.startswith("mark explanation report")
        )
    except StopIteration:
        raise AssertionError("no mark explanation report found in output") from None
    report = [lines[start]]
    for line in lines[start + 1 :]:
        # Report body lines are indented; the first non-indented line ends it.
        if not line.startswith(" "):
            break
        report.append(line)
    return report


def test_explain_marks_module_and_parametrize_sources(pytester: Pytester) -> None:
    """Module-level and parameter-level marks of the same name are both
    reported with their own source, and a parameter set without marks does
    not inherit another parameter set's marks."""
    pytester.makeini(
        """
        [pytest]
        markers =
            slow: mark a test as slow
        """
    )
    pytester.makepyfile(
        """
        import pytest

        pytestmark = pytest.mark.slow

        @pytest.mark.parametrize(
            "value",
            [
                pytest.param(1, marks=pytest.mark.slow, id="marked"),
                pytest.param(2, id="plain"),
            ],
        )
        def test_param(value):
            pass
        """
    )
    result = pytester.runpytest("--explain-marks", "--collect-only")
    assert result.ret == 0
    result.stdout.re_match_lines(
        [
            r"mark explanation report \(read-only.*\):",
            r"  item #0 \S+::test_param\[marked\]",
            r"    mark #0 parametrize\('value', .* \[source=function, owner=\S+\] \(effective, registered\)",
            r"    mark #1 slow \[source=parametrize, owner=\S+::test_param\[marked\]\] \(effective, registered\)",
            r"    mark #2 slow \[source=module, owner=\S+\.py\] \(shadowed, registered\)",
            r"  item #1 \S+::test_param\[plain\]",
            r"    mark #0 parametrize\('value', .* \[source=function, owner=\S+\] \(effective, registered\)",
            r"    mark #1 slow \[source=module, owner=\S+\.py\] \(effective, registered\)",
            r"  2 of 2 collected items carry markers",
        ],
        consecutive=True,
    )
    # The "plain" parameter set must not inherit the "marked" set's mark:
    # exactly one parametrize-sourced slow mark exists in the whole report.
    report = extract_report_lines(result.stdout.str())
    parametrize_slow = [
        line for line in report if "slow" in line and "source=parametrize" in line
    ]
    assert len(parametrize_slow) == 1
    assert "test_param[marked]" in parametrize_slow[0]


def test_explain_marks_keeps_duplicate_marks_in_order(pytester: Pytester) -> None:
    """Same-name markers are not collapsed into a single value: every
    occurrence is listed, in the original application order."""
    pytester.makeini(
        """
        [pytest]
        markers =
            slow: mark a test as slow
        """
    )
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.slow(reason="applied-second")
        @pytest.mark.slow(reason="applied-first")
        def test_dup():
            pass
        """
    )
    result = pytester.runpytest("--explain-marks", "--collect-only")
    assert result.ret == 0
    result.stdout.re_match_lines(
        [
            r"    mark #0 slow\(reason='applied-first'\) \[source=function, .* \(effective, registered\)",
            r"    mark #1 slow\(reason='applied-second'\) \[source=function, .* \(shadowed, registered\)",
        ],
        consecutive=True,
    )


def test_explain_marks_class_source(pytester: Pytester) -> None:
    """Class-level marks are reported with the class as their source."""
    pytester.makeini(
        """
        [pytest]
        markers =
            webtest: mark a test as a web test
        """
    )
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.webtest
        class TestGui:
            def test_method(self):
                pass
        """
    )
    result = pytester.runpytest("--explain-marks", "--collect-only")
    assert result.ret == 0
    result.stdout.re_match_lines(
        [
            r"  item #0 \S+::TestGui::test_method",
            r"    mark #0 webtest \[source=class, owner=\S+::TestGui\] \(effective, registered\)",
        ],
        consecutive=True,
    )


def test_explain_marks_unregistered_diagnostics(pytester: Pytester) -> None:
    """Markers which are not registered are flagged per occurrence and
    listed again in a dedicated diagnostics section."""
    pytester.makeini(
        """
        [pytest]
        markers =
            registered_mark: a registered marker
        """
    )
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.registered_mark
        @pytest.mark.mystery_mark
        def test_x():
            pass
        """
    )
    result = pytester.runpytest("--explain-marks", "--collect-only")
    assert result.ret == 0
    result.stdout.re_match_lines(
        [
            r"    mark #0 mystery_mark \[source=function, .* \(effective, UNREGISTERED\)",
            r"    mark #1 registered_mark \[source=function, .* \(effective, registered\)",
            r"  1 of 1 collected items carry markers",
            r"  unregistered markers \(not builtin, plugin-provided or listed in the `markers` ini\):",
            r"    mystery_mark \[item=\S+::test_x, source=function\]",
        ],
        consecutive=True,
    )
    # The registered marker must not appear in the diagnostics section.
    result.stdout.no_fnmatch_line("*registered_mark*item=*")


def test_explain_marks_triggers_no_fixtures(pytester: Pytester) -> None:
    """The report is produced at collection time and must not trigger any
    fixture setup (fixture count stays 0)."""
    pytester.makeconftest(
        """
        def pytest_fixture_setup(fixturedef, request):
            with open("fixture-setups.txt", "a", encoding="utf-8") as f:
                print(fixturedef.argname, file=f)
        """
    )
    pytester.makeini(
        """
        [pytest]
        markers =
            slow: mark a test as slow
        """
    )
    pytester.makepyfile(
        """
        import pytest

        @pytest.fixture
        def spying_fixture():
            return 42

        @pytest.mark.slow
        def test_needs_fixture(spying_fixture):
            assert spying_fixture == 42
        """
    )
    result = pytester.runpytest("--explain-marks", "--collect-only")
    assert result.ret == 0
    # The report was still produced in full...
    result.stdout.re_match_lines(
        [
            r"mark explanation report \(read-only.*\):",
            r"  item #0 \S+::test_needs_fixture",
            r"    mark #0 slow \[source=function, .* \(effective, registered\)",
        ],
        consecutive=True,
    )
    # ...without setting up a single fixture.
    assert not (pytester.path / "fixture-setups.txt").exists()


def test_explain_marks_normal_run_is_read_only(pytester: Pytester) -> None:
    """With a normal run the report is shown, all items still run and
    nothing is deselected or modified."""
    pytester.makeini(
        """
        [pytest]
        markers =
            slow: mark a test as slow
        """
    )
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.slow
        def test_marked():
            pass

        def test_unmarked():
            pass
        """
    )
    result = pytester.runpytest("--explain-marks")
    result.assert_outcomes(passed=2)
    result.stdout.re_match_lines(
        [
            r"mark explanation report \(read-only.*\):",
            r"  item #0 \S+::test_marked",
            r"    mark #0 slow \[source=function, .* \(effective, registered\)",
            r"  1 of 2 collected items carry markers",
        ],
        consecutive=True,
    )


def test_explain_marks_report_is_stable(pytester: Pytester) -> None:
    """Repeated runs over the same collected tree produce identical reports
    (stable, comparable numbering)."""
    pytester.makeini(
        """
        [pytest]
        markers =
            slow: mark a test as slow
        """
    )
    pytester.makepyfile(
        """
        import pytest

        pytestmark = pytest.mark.slow

        @pytest.mark.parametrize("value", [1, 2, 3])
        def test_param(value):
            pass

        def test_other():
            pass
        """
    )
    first = pytester.runpytest("--explain-marks", "--collect-only")
    second = pytester.runpytest("--explain-marks", "--collect-only")
    assert first.ret == 0
    assert second.ret == 0
    first_report = extract_report_lines(first.stdout.str())
    second_report = extract_report_lines(second.stdout.str())
    assert first_report == second_report
    # Sanity check: the report actually contains numbered entries.
    assert any(line.startswith("  item #0 ") for line in first_report)
    assert any(line.startswith("    mark #0 ") for line in first_report)


def test_explain_marks_disabled_by_default(pytester: Pytester) -> None:
    """No report is shown unless --explain-marks is passed."""
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.slow
        def test_x():
            pass
        """
    )
    result = pytester.runpytest("--collect-only")
    assert result.ret == 0
    result.stdout.no_fnmatch_line("mark explanation report*")
