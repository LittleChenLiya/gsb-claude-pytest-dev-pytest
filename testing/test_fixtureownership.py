"""Tests for the ``--fixture-ownership-report`` option.

The report (see ``src/_pytest/fixtureownership.py``) records the *actual*
fixture lifecycle events of a session -- creation, borrowing, finalizer
registration and release -- without executing fixtures itself and without
storing fixture values.
"""

from __future__ import annotations

from _pytest.pytester import Pytester
from _pytest.pytester import RunResult


OPTION = "--fixture-ownership-report"


def make_lifecycle_project(pytester: Pytester) -> None:
    """A small lifecycle example: one session-scoped fixture shared by two
    items, one function-scoped fixture per item."""
    pytester.makepyfile(
        test_lifecycle="""
        import pytest

        @pytest.fixture(scope="session")
        def db():
            yield "db"

        @pytest.fixture
        def conn(db):
            yield "conn"

        def test_one(conn):
            pass

        def test_two(conn):
            pass
        """
    )


def report_lines(result: RunResult) -> list[str]:
    """Extract the lines of the fixture ownership report section."""
    lines = result.stdout.str().splitlines()
    out: list[str] = []
    in_report = False
    for line in lines:
        if "fixture ownership report" in line:
            in_report = True
            continue
        if in_report:
            if line.startswith("="):
                break
            if line.strip():
                out.append(line)
    return out


def parse_events(result: RunResult) -> list[tuple[str, str, str, str, str]]:
    """Parse the report into (kind, scope, argname, item, detail) tuples."""
    events = []
    for line in report_lines(result):
        parts = line.split()
        if len(parts) >= 5 and parts[0].isdigit() and parts[4].startswith("item="):
            events.append(
                (parts[1], parts[2], parts[3], parts[4][5:], " ".join(parts[5:]))
            )
    return events


def events_named(
    events: list[tuple[str, str, str, str, str]], argname: str, kind: str | None = None
) -> list[tuple[str, str, str, str, str]]:
    return [
        event
        for event in events
        if event[2] == argname and (kind is None or event[0] == kind)
    ]


def test_session_fixture_created_once_function_fixtures_released_separately(
    pytester: Pytester,
) -> None:
    """Lifecycle example: a session fixture shared by two items is created
    exactly once, while the function fixtures are released per item."""
    make_lifecycle_project(pytester)
    result = pytester.runpytest(OPTION)
    assert result.ret == 0

    events = parse_events(result)
    # The session fixture is created exactly once for both items ...
    assert len(events_named(events, "db", "created")) == 1
    # ... borrowed by both items ...
    db_item_borrows = [
        event
        for event in events_named(events, "db", "borrowed")
        if event[4] == "by item"
    ]
    assert {event[3] for event in db_item_borrows} == {
        "test_lifecycle.py::test_one",
        "test_lifecycle.py::test_two",
    }
    # ... and by the "conn" fixture of each item ...
    db_fixture_borrows = [
        event
        for event in events_named(events, "db", "borrowed")
        if event[4] == "by fixture 'conn'"
    ]
    assert {event[3] for event in db_fixture_borrows} == {
        "test_lifecycle.py::test_one",
        "test_lifecycle.py::test_two",
    }
    # ... and released exactly once, at the end of the session.
    assert len(events_named(events, "db", "released")) == 1

    # The function fixture is created and released separately per item.
    conn_creates = events_named(events, "conn", "created")
    assert {event[3] for event in conn_creates} == {
        "test_lifecycle.py::test_one",
        "test_lifecycle.py::test_two",
    }
    conn_releases = events_named(events, "conn", "released")
    assert {event[3] for event in conn_releases} == {
        "test_lifecycle.py::test_one",
        "test_lifecycle.py::test_two",
    }
    # The function fixture of the first item is released before the one of
    # the second item is created.
    kinds = [event[0:3] for event in events]
    first_conn_release = kinds.index(("released", "function", "conn"))
    second_conn_create = (
        len(kinds) - 1 - kinds[::-1].index(("created", "function", "conn"))
    )
    assert first_conn_release < second_conn_create

    result.stdout.fnmatch_lines(["totals: *created=3*released=3*"])


def test_setup_failure_midway_keeps_registered_finalizer_events(
    pytester: Pytester,
) -> None:
    """When the setup of an item fails midway, the finalizer registrations
    and releases of the fixtures which were already created are still
    reported, and creation failures are distinguished from dependency
    failures."""
    pytester.makepyfile(
        test_midway="""
        import pytest

        @pytest.fixture
        def good():
            yield "good"

        @pytest.fixture
        def bad(good):
            raise RuntimeError("boom")

        @pytest.fixture
        def top(bad):
            return "top"

        def test_midway(top):
            pass
        """
    )
    result = pytester.runpytest(OPTION)
    result.assert_outcomes(errors=1)

    events = parse_events(result)
    # "good" was fully set up before the failure: registration, creation
    # and release are all visible.
    assert len(events_named(events, "good", "finalizer-registered")) == 1
    assert len(events_named(events, "good", "created")) == 1
    assert len(events_named(events, "good", "released")) == 1
    # "bad" failed in its own fixture function: create-failed, and its
    # finalizer registration is still visible.
    (bad_create,) = events_named(events, "bad", "create-failed")
    assert "fixture function raised RuntimeError" in bad_create[4]
    assert len(events_named(events, "bad", "finalizer-registered")) == 1
    # "top" never ran its own fixture function because its dependency
    # failed: dependency-failed, not create-failed.
    (top_dep,) = events_named(events, "top", "dependency-failed")
    assert "dependency 'bad' raised RuntimeError" in top_dep[4]
    assert events_named(events, "top", "create-failed") == []
    assert events_named(events, "top", "created") == []


def test_never_created_fixtures_are_not_reported_released(
    pytester: Pytester,
) -> None:
    """A fixture whose setup failed (or which was never executed at all)
    must not show up as successfully released."""
    pytester.makepyfile(
        test_never="""
        import pytest

        @pytest.fixture
        def never_used():
            raise AssertionError("must not be executed")

        @pytest.fixture
        def broken():
            raise RuntimeError("boom")

        def test_broken(broken):
            pass
        """
    )
    result = pytester.runpytest(OPTION)
    result.assert_outcomes(errors=1)

    events = parse_events(result)
    # Declared but never requested: no events at all (nothing is inferred
    # from declarations), and the report did not execute it.
    assert events_named(events, "never_used") == []
    # Failed creation: no successful release, but an explicit
    # release-skipped event instead.
    assert events_named(events, "broken", "released") == []
    (skipped,) = events_named(events, "broken", "release-skipped")
    assert "never created" in skipped[4]


def test_finalizer_exception_does_not_hide_other_release_events(
    pytester: Pytester,
) -> None:
    """A finalizer which raises does not erase the release events of the
    other fixtures."""
    pytester.makepyfile(
        test_finalizers="""
        import pytest

        @pytest.fixture
        def flaky():
            yield "flaky"
            raise RuntimeError("teardown boom")

        @pytest.fixture
        def other():
            yield "other"

        def test_finalizers(flaky, other):
            pass
        """
    )
    result = pytester.runpytest(OPTION)
    result.assert_outcomes(passed=1, errors=1)

    events = parse_events(result)
    assert len(events_named(events, "flaky", "released")) == 1
    assert len(events_named(events, "other", "released")) == 1


def test_report_does_not_execute_fixtures_or_store_values(
    pytester: Pytester,
) -> None:
    """The report is observational only: it neither executes fixtures
    itself nor stores (and prints) fixture values."""
    pytester.makepyfile(
        test_observe="""
        import pytest

        calls = []

        @pytest.fixture(scope="session")
        def counted():
            calls.append(1)
            yield "SENTINEL-VALUE-12345"

        @pytest.fixture
        def unused():
            raise AssertionError("must not be executed by the report")

        def test_one(counted):
            pass

        def test_two(counted):
            pass

        def test_created_only_once():
            assert len(calls) == 1
        """
    )
    result = pytester.runpytest(OPTION)
    assert result.ret == 0

    events = parse_events(result)
    # The session fixture was executed exactly once in total.
    assert len(events_named(events, "counted", "created")) == 1
    # The unused fixture was not executed just because of the report.
    assert events_named(events, "unused") == []
    # Fixture values are never stored, so they never show up in the report.
    for line in report_lines(result):
        assert "SENTINEL-VALUE-12345" not in line


def test_parallel_sessions_have_independent_state(pytester: Pytester) -> None:
    """Repeated in-process sessions each get their own report state."""
    pytester.makepyfile(
        test_alpha="""
        import pytest

        @pytest.fixture
        def fixture_alpha():
            yield 1

        def test_alpha(fixture_alpha):
            pass
        """,
        test_beta="""
        import pytest

        @pytest.fixture
        def fixture_beta():
            yield 1

        def test_beta(fixture_beta):
            pass
        """,
    )
    result_alpha = pytester.runpytest_inprocess(OPTION, "test_alpha.py")
    assert result_alpha.ret == 0
    result_beta = pytester.runpytest_inprocess(OPTION, "test_beta.py")
    assert result_beta.ret == 0

    events_alpha = parse_events(result_alpha)
    assert events_named(events_alpha, "fixture_alpha", "created")
    assert events_named(events_alpha, "fixture_beta") == []

    events_beta = parse_events(result_beta)
    assert events_named(events_beta, "fixture_beta", "created")
    assert events_named(events_beta, "fixture_alpha") == []


def test_report_disabled_by_default(pytester: Pytester) -> None:
    """Without the option no report is shown and no state is kept."""
    make_lifecycle_project(pytester)
    result = pytester.runpytest()
    assert result.ret == 0
    assert report_lines(result) == []
    result.stdout.no_fnmatch_line("*fixture ownership report*")
