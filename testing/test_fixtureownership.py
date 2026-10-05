"""Tests for the optional fixture ownership report (--fixture-ownership-report).

The report records the *actual* fixture lifecycle: created, borrowed,
finalizers registered, and released.  It never executes fixtures a second time
and never stores fixture values.
"""

from __future__ import annotations

import json

from _pytest.pytester import Pytester


# -- helpers ---------------------------------------------------------------


def _report_lines(result) -> list[str]:
    """Extract the fixture ownership report lines from the pytest output."""
    lines = result.stdout.lines
    start = None
    end = None
    for i, line in enumerate(lines):
        # The header separator contains "fixture ownership report" but not
        # "end fixture ownership report".
        if (
            "fixture ownership report" in line
            and "==" in line
            and "end fixture ownership report" not in line
        ):
            start = i
        if start is not None and "end fixture ownership report" in line:
            end = i
            break
    if start is None or end is None:
        return []
    return lines[start + 1 : end]


def _events(lines: list[str]) -> list[dict[str, str]]:
    """Parse report lines into event dicts.

    Each event line has the form::

        <kind>: <scope> <name> <key>=<value> ...
    """
    events: list[dict[str, str]] = []
    for line in lines:
        line = line.strip()
        if not line or line == "fixture ownership report:":
            continue
        parts = line.split()
        if not parts:
            continue
        kind = parts[0].rstrip(":")
        event: dict[str, str] = {"kind": kind}
        # parts[1] = scope, parts[2] = name (positional)
        if len(parts) >= 2:
            event["scope"] = parts[1]
        if len(parts) >= 3:
            event["name"] = parts[2]
        for part in parts[3:]:
            if "=" in part:
                key, _, value = part.partition("=")
                event[key] = value
        events.append(event)
    return events


# -- tests -----------------------------------------------------------------


def test_session_fixture_created_once_borrowed_by_two_items(
    pytester: Pytester,
) -> None:
    """A session-scoped fixture shared by two items is created only once.

    Both items borrow the same fixture instance; function-scoped fixtures are
    released separately.
    """
    pytester.makepyfile(
        """
        import pytest

        @pytest.fixture(scope="session")
        def session_fix():
            yield "session_value"

        @pytest.fixture(scope="function")
        def func_fix():
            yield "func_value"

        def test_one(session_fix, func_fix):
            assert session_fix == "session_value"
            assert func_fix == "func_value"

        def test_two(session_fix, func_fix):
            assert session_fix == "session_value"
            assert func_fix == "func_value"
        """
    )

    result = pytester.runpytest("--fixture-ownership-report", "-p", "no:cacheprovider")
    assert result.ret == 0

    lines = _report_lines(result)
    events = _events(lines)

    # The session fixture is created exactly once.
    session_creates = [
        e for e in events if e["kind"] == "created" and e["name"] == "session_fix"
    ]
    assert len(session_creates) == 1
    assert session_creates[0]["scope"] == "session"
    assert session_creates[0]["outcome"] == "success"

    # Both items borrow the session fixture.
    session_borrows = [
        e for e in events if e["kind"] == "borrowed" and e["name"] == "session_fix"
    ]
    assert len(session_borrows) == 2
    borrowers = {e["borrower"] for e in session_borrows}
    assert borrowers == {
        "test_session_fixture_created_once_borrowed_by_two_items.py::test_one",
        "test_session_fixture_created_once_borrowed_by_two_items.py::test_two",
    }

    # The function fixture is created and released separately for each item.
    func_creates = [
        e for e in events if e["kind"] == "created" and e["name"] == "func_fix"
    ]
    assert len(func_creates) == 2
    func_releases = [
        e for e in events if e["kind"] == "released" and e["name"] == "func_fix"
    ]
    assert len(func_releases) == 2
    for release in func_releases:
        assert release["outcome"] == "success"


def test_setup_failure_shows_registered_finalizer(pytester: Pytester) -> None:
    """A fixture that registers a finalizer and then fails setup still shows
    the registered finalizer event."""
    pytester.makepyfile(
        """
        import pytest

        @pytest.fixture
        def fixture_with_finalizer(request):
            def fin():
                pass
            request.addfinalizer(fin)
            raise RuntimeError("setup failed after registering finalizer")

        def test_fail(fixture_with_finalizer):
            pass
        """
    )

    result = pytester.runpytest("--fixture-ownership-report", "-p", "no:cacheprovider")
    assert result.ret != 0

    lines = _report_lines(result)
    events = _events(lines)

    # The fixture was created (setup ran) but failed.
    creates = [
        e
        for e in events
        if e["kind"] == "created" and e["name"] == "fixture_with_finalizer"
    ]
    assert len(creates) == 1
    assert creates[0]["outcome"] == "failure"
    assert "RuntimeError" in creates[0].get("error", "")

    # The finalizer registered before the failure is recorded.
    finalizers = [
        e
        for e in events
        if e["kind"] == "finalizer"
    ]
    assert len(finalizers) >= 1

    # The fixture is released (its finalizer ran) despite the setup failure.
    releases = [
        e
        for e in events
        if e["kind"] == "released" and e["name"] == "fixture_with_finalizer"
    ]
    assert len(releases) == 1


def test_not_created_fixture_not_released(pytester: Pytester) -> None:
    """A fixture that was never created (because its dependency failed) must
    not be reported as released."""
    pytester.makepyfile(
        """
        import pytest

        @pytest.fixture
        def failing_dep():
            raise RuntimeError("dependency failed")

        @pytest.fixture
        def dependent(failing_dep):
            return "dependent_value"

        def test_dep(dependent):
            pass
        """
    )

    result = pytester.runpytest("--fixture-ownership-report", "-p", "no:cacheprovider")
    assert result.ret != 0

    lines = _report_lines(result)
    events = _events(lines)

    # The failing dependency is created (setup ran) and failed.
    dep_creates = [
        e for e in events if e["kind"] == "created" and e["name"] == "failing_dep"
    ]
    assert len(dep_creates) == 1
    assert dep_creates[0]["outcome"] == "failure"

    # The dependent fixture is never created.
    dependent_creates = [
        e for e in events if e["kind"] == "created" and e["name"] == "dependent"
    ]
    assert len(dependent_creates) == 0

    # The dependent fixture is never released (no successful release reported
    # for a fixture that was never created).
    dependent_releases = [
        e for e in events if e["kind"] == "released" and e["name"] == "dependent"
    ]
    assert len(dependent_releases) == 0

    # The failing dependency is released (its finalizer ran) despite the failure.
    dep_releases = [
        e for e in events if e["kind"] == "released" and e["name"] == "failing_dep"
    ]
    assert len(dep_releases) == 1


def test_finalizer_exception_does_not_erase_other_releases(
    pytester: Pytester,
) -> None:
    """A finalizer that raises must not erase other fixtures' release events."""
    pytester.makepyfile(
        """
        import pytest

        @pytest.fixture
        def fixture_with_bad_finalizer(request):
            def bad_fin():
                raise RuntimeError("finalizer failed")
            request.addfinalizer(bad_fin)
            yield "value"

        @pytest.fixture
        def fixture_after(request):
            yield "after_value"

        def test_finexcept(fixture_with_bad_finalizer, fixture_after):
            pass
        """
    )

    result = pytester.runpytest("--fixture-ownership-report", "-p", "no:cacheprovider")
    assert result.ret != 0

    lines = _report_lines(result)
    events = _events(lines)

    # Both fixtures are created.
    for name in ("fixture_with_bad_finalizer", "fixture_after"):
        creates = [e for e in events if e["kind"] == "created" and e["name"] == name]
        assert len(creates) == 1

    # Both fixtures are released despite the finalizer exception.
    for name in ("fixture_with_bad_finalizer", "fixture_after"):
        releases = [e for e in events if e["kind"] == "released" and e["name"] == name]
        assert len(releases) == 1, f"release event missing for {name}"

    # The bad finalizer's release is recorded as a failure, not erased.
    bad_releases = [
        e
        for e in events
        if e["kind"] == "released" and e["name"] == "fixture_with_bad_finalizer"
    ]
    assert len(bad_releases) == 1
    assert bad_releases[0]["outcome"] == "failure"
    assert "RuntimeError" in bad_releases[0].get("error", "")


def test_json_report_contains_no_fixture_values(pytester: Pytester) -> None:
    """The JSON report contains only metadata, never fixture values."""
    pytester.makepyfile(
        """
        import pytest

        @pytest.fixture(scope="session")
        def session_fix():
            yield {"secret": "value", "number": 42}

        def test_one(session_fix):
            assert session_fix["secret"] == "value"
        """
    )

    report_path = pytester.path / "report.json"
    result = pytester.runpytest(
        "--fixture-ownership-report",
        f"--fixture-ownership-report-file={report_path}",
        "-p",
        "no:cacheprovider",
    )
    assert result.ret == 0

    assert report_path.exists()
    events = json.loads(report_path.read_text(encoding="utf-8"))

    # The report is a list of event dicts.
    assert isinstance(events, list)
    assert len(events) > 0

    # No fixture values are stored -- the fixture's return value must not
    # appear in the report.
    report_text = json.dumps(events)
    assert "secret" not in report_text
    assert "42" not in report_text

    # Each event has the required metadata fields.
    for event in events:
        assert "kind" in event
        assert "name" in event
        assert "scope" in event


def test_report_not_executed_second_time(pytester: Pytester) -> None:
    """The report observes fixtures without executing them a second time."""
    pytester.makepyfile(
        """
        import pytest

        setup_count = 0

        @pytest.fixture(scope="session")
        def counting_fixture():
            global setup_count
            setup_count += 1
            yield setup_count

        def test_one(counting_fixture):
            assert counting_fixture == 1

        def test_two(counting_fixture):
            assert counting_fixture == 1
        """
    )

    result = pytester.runpytest("--fixture-ownership-report", "-p", "no:cacheprovider")
    assert result.ret == 0

    lines = _report_lines(result)
    events = _events(lines)

    # The fixture is created exactly once (not re-executed by the report).
    creates = [
        e for e in events if e["kind"] == "created" and e["name"] == "counting_fixture"
    ]
    assert len(creates) == 1


def test_no_report_without_flag(pytester: Pytester) -> None:
    """Without the flag, no fixture ownership report is printed."""
    pytester.makepyfile(
        """
        import pytest

        @pytest.fixture
        def func_fix():
            yield "value"

        def test_one(func_fix):
            assert func_fix == "value"
        """
    )

    result = pytester.runpytest("-p", "no:cacheprovider")
    assert result.ret == 0

    lines = _report_lines(result)
    assert lines == []


def test_parallel_sessions_use_separate_state(pytester: Pytester) -> None:
    """Each session uses its own report state -- sessions in the same process
    do not share events."""
    import pytest

    pytester.makepyfile(
        test_a="""
        import pytest

        @pytest.fixture
        def fixture_a():
            yield "a"

        def test_a(fixture_a):
            assert fixture_a == "a"
        """,
        test_b="""
        import pytest

        @pytest.fixture
        def fixture_b():
            yield "b"

        def test_b(fixture_b):
            assert fixture_b == "b"
        """,
    )

    # Run two sessions in the same process.  Each session must have its own
    # report state.
    report_path_a = pytester.path / "report_a.json"
    report_path_b = pytester.path / "report_b.json"

    ret_a = pytest.main(
        [
            str(pytester.path / "test_a.py"),
            "--fixture-ownership-report",
            f"--fixture-ownership-report-file={report_path_a}",
            "-p",
            "no:cacheprovider",
        ]
    )
    assert ret_a == 0

    ret_b = pytest.main(
        [
            str(pytester.path / "test_b.py"),
            "--fixture-ownership-report",
            f"--fixture-ownership-report-file={report_path_b}",
            "-p",
            "no:cacheprovider",
        ]
    )
    assert ret_b == 0

    # Each report contains only its own session's events.
    events_a = json.loads(report_path_a.read_text(encoding="utf-8"))
    events_b = json.loads(report_path_b.read_text(encoding="utf-8"))

    names_a = {e["name"] for e in events_a}
    names_b = {e["name"] for e in events_b}

    assert "fixture_a" in names_a
    assert "fixture_b" not in names_a
    assert "fixture_b" in names_b
    assert "fixture_a" not in names_b
