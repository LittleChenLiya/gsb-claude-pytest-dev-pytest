"""Regression tests for natively supported async fixtures."""

from __future__ import annotations

from _pytest.pytester import Pytester


def test_module_scope_async_fixture_setup_once(pytester: Pytester) -> None:
    """A module-scoped async fixture is set up and torn down exactly once,
    no matter how many tests use it."""
    pytester.makepyfile(
        test_a_mod="""
        import pytest

        events = []

        @pytest.fixture(scope="module")
        async def resource():
            events.append("setup")
            yield 42
            events.append("teardown")

        def test_one(resource):
            assert resource == 42

        def test_two(resource):
            assert resource == 42

        def test_three(resource):
            assert resource == 42
        """,
        test_b_check="""
        from test_a_mod import events

        def test_lifecycle():
            # The module fixture of test_a_mod is torn down before this
            # module's tests run.
            assert events == ["setup", "teardown"]
        """,
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=4)


def test_session_scope_async_fixture_setup_once(pytester: Pytester) -> None:
    """A session-scoped async fixture is shared (and set up once) across
    several test modules, and torn down once at the end of the session."""
    pytester.makepyfile(
        conftest="""
        import json
        import pathlib

        import pytest

        events = []

        @pytest.fixture(scope="session")
        async def resource():
            events.append("setup")
            yield events
            events.append("teardown")

        def pytest_sessionfinish():
            pathlib.Path("events.json").write_text(json.dumps(events))
        """,
        test_a="""
        def test_a(resource):
            resource.append("a")
        """,
        test_b="""
        def test_b(resource):
            resource.append("b")
        """,
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=2)
    import json

    events = json.loads(pytester.path.joinpath("events.json").read_text())
    assert events == ["setup", "a", "b", "teardown"]


def test_parametrized_async_fixture(pytester: Pytester) -> None:
    """Each parameter of a parametrized async fixture is set up and torn
    down exactly once."""
    pytester.makepyfile(
        test_params="""
        import pytest

        events = []

        @pytest.fixture(params=[1, 2, 3])
        async def number(request):
            events.append(("setup", request.param))
            yield request.param
            events.append(("teardown", request.param))

        def test_number(number):
            assert number in (1, 2, 3)

        def test_events():
            assert events == [
                ("setup", 1), ("teardown", 1),
                ("setup", 2), ("teardown", 2),
                ("setup", 3), ("teardown", 3),
            ]
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=4)


def test_async_fixture_dependency_failure_reverse_teardown(
    pytester: Pytester,
) -> None:
    """When a fixture in a dependency chain fails to set up, the fixtures
    which were already set up are finalized in reverse topological order."""
    pytester.makepyfile(
        test_dep="""
        import pytest

        events = []

        @pytest.fixture
        async def first():
            events.append("setup-first")
            yield 1
            events.append("teardown-first")

        @pytest.fixture
        async def second(first):
            events.append("setup-second")
            yield 2
            events.append("teardown-second")

        @pytest.fixture
        async def third(second):
            events.append("setup-third")
            raise RuntimeError("boom")

        def test_uses_failing(third):
            pass

        def test_order():
            assert events == [
                "setup-first",
                "setup-second",
                "setup-third",
                "teardown-second",
                "teardown-first",
            ]
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=1, errors=1)


def test_async_yield_fixture_and_addfinalizer_order(pytester: Pytester) -> None:
    """request.addfinalizer callbacks registered by an async yield fixture
    compose with the yield teardown in stable LIFO order, matching the
    semantics of synchronous yield fixtures."""
    pytester.makepyfile(
        test_order="""
        import pytest

        events = []

        @pytest.fixture
        async def resource(request):
            events.append("setup")
            request.addfinalizer(lambda: events.append("finalizer-1"))

            async def async_fin():
                events.append("finalizer-2")

            request.addfinalizer(async_fin)
            yield 1
            events.append("yield-teardown")

        def test_one(resource):
            assert resource == 1

        def test_order():
            assert events == [
                "setup",
                "yield-teardown",
                "finalizer-2",
                "finalizer-1",
            ]
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=2)


def test_async_fixture_teardown_errors_aggregated(pytester: Pytester) -> None:
    """If both an async yield teardown and an addfinalizer callback raise,
    the errors are aggregated instead of discarding all but the first."""
    pytester.makepyfile(
        test_agg="""
        import pytest

        @pytest.fixture
        async def resource(request):
            def fin():
                raise RuntimeError("finalizer-error")

            request.addfinalizer(fin)
            yield 1
            raise ValueError("yield-teardown-error")

        def test_one(resource):
            pass
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(
        ["*errors while tearing down fixture*resource*", "*finalizer-error*"]
    )
    result.stdout.fnmatch_lines(["*yield-teardown-error*"])


def test_async_fixture_setup_failure_recovers(pytester: Pytester) -> None:
    """After an async fixture fails to set up, later tests can still use
    async fixtures (the event loop is recreated as needed)."""
    pytester.makepyfile(
        test_recover="""
        import pytest

        @pytest.fixture
        async def failing():
            raise RuntimeError("boom")

        @pytest.fixture
        async def working():
            return 42

        def test_broken(failing):
            pass

        def test_still_works(working):
            assert working == 42
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=1, errors=1)


def test_async_fixture_nested_event_loop_in_test(pytester: Pytester) -> None:
    """A test may run its own (nested) event loop while an async fixture of
    any scope is active."""
    pytester.makepyfile(
        test_nested="""
        import asyncio

        import pytest

        @pytest.fixture
        async def resource():
            return 42

        async def compute():
            await asyncio.sleep(0)
            return 1

        def test_nested_loop(resource):
            assert resource == 42
            assert asyncio.run(compute()) == 1
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)


def test_async_fixture_keyboard_interrupt_no_leak(pytester: Pytester) -> None:
    """A KeyboardInterrupt during the run must not leak event loops, pending
    tasks or other resources."""
    pytester.makepyfile(
        test_interrupt="""
        import asyncio

        import pytest

        @pytest.fixture(scope="session")
        async def resource():
            async def background():
                await asyncio.sleep(3600)

            task = asyncio.get_running_loop().create_task(background())
            yield task

        def test_interrupted(resource):
            raise KeyboardInterrupt
        """
    )
    result = pytester.runpytest_subprocess()
    assert result.ret == 2  # ExitCode.INTERRUPTED
    result.stderr.no_fnmatch_line("*Task was destroyed but it is pending*")
    result.stderr.no_fnmatch_line("*Event loop is closed*")
    result.stderr.no_fnmatch_line("*unclosed*")
    result.stderr.no_fnmatch_line("*Exception ignored*")


def test_async_fixture_no_loop_leak_after_session(pytester: Pytester) -> None:
    """After the session finishes, no async fixture event loop is left
    open or running."""
    pytester.makepyfile(
        test_a_leak="""
        import asyncio

        import pytest

        loops = []

        @pytest.fixture(scope="module")
        async def resource():
            loops.append(asyncio.get_running_loop())
            yield 1

        def test_one(resource):
            pass

        def test_two(resource):
            pass

        def test_loop_recorded():
            assert len(loops) == 1
        """,
        test_b_check="""
        import asyncio
        import gc

        from test_a_leak import loops

        def test_loops_closed():
            assert len(loops) == 1
            assert loops[0].is_closed()
            gc.collect()
            leftover = [
                loop
                for loop in gc.get_objects()
                if isinstance(loop, asyncio.AbstractEventLoop)
                and not loop.is_closed()
            ]
            assert leftover == []
        """,
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=4)


def test_async_fixture_multiple_modules_shared_scope_loop(
    pytester: Pytester,
) -> None:
    """Concurrent collection of several modules using async fixtures of the
    same scope: all share a single event loop which is closed afterwards."""
    pytester.makepyfile(
        conftest="""
        import asyncio

        import pytest

        seen_loops = []

        @pytest.fixture(scope="session")
        async def session_resource():
            seen_loops.append(asyncio.get_running_loop())
            yield 1
        """,
        **{
            f"test_mod{i}": """
        import asyncio

        import pytest

        from conftest import seen_loops

        @pytest.fixture(scope="module")
        async def module_resource(session_resource):
            seen_loops.append(asyncio.get_running_loop())
            return session_resource

        def test_value(module_resource):
            assert module_resource == 1
        """
            for i in range(3)
        },
    )
    pytester.makepyfile(
        test_zz_check="""
        from conftest import seen_loops

        def test_shared_loops():
            # one session loop + one module loop per module
            assert len(seen_loops) == 4
            assert len(set(seen_loops)) == 4
            # The module loops are closed once their scope ends; the session
            # loop is still active and is closed at session teardown.
            assert sum(loop.is_closed() for loop in seen_loops) == 3
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=4)


def test_class_scope_async_fixture(pytester: Pytester) -> None:
    """Class-scoped async fixtures are set up once per class."""
    pytester.makepyfile(
        test_class="""
        import pytest

        events = []

        class TestGroup:
            @pytest.fixture(scope="class")
            async def resource(self):
                events.append("setup")
                yield 42
                events.append("teardown")

            def test_one(self, resource):
                assert resource == 42

            def test_two(self, resource):
                assert resource == 42

        def test_lifecycle():
            assert events == ["setup", "teardown"]
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=3)


def test_sync_fixture_with_async_finalizer(pytester: Pytester) -> None:
    """A synchronous fixture may register an async def finalizer; it is
    awaited during teardown."""
    pytester.makepyfile(
        test_fin="""
        import asyncio

        import pytest

        events = []

        @pytest.fixture
        def resource(request):
            async def fin():
                await asyncio.sleep(0)
                events.append("async-finalizer")

            request.addfinalizer(fin)
            return 1

        def test_one(resource):
            assert resource == 1

        def test_finalized():
            assert events == ["async-finalizer"]
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=2)


def test_async_fixture_keyboard_interrupt_during_setup(
    pytester: Pytester,
) -> None:
    """A KeyboardInterrupt raised while an async fixture is being set up
    aborts the session without leaking loops or tasks."""
    pytester.makepyfile(
        test_interrupt="""
        import asyncio

        import pytest

        @pytest.fixture
        async def broken():
            await asyncio.sleep(0)
            raise KeyboardInterrupt

        def test_one(broken):
            pass

        def test_two():
            pass
        """
    )
    result = pytester.runpytest_subprocess()
    assert result.ret == 2  # ExitCode.INTERRUPTED
    result.stderr.no_fnmatch_line("*Task was destroyed but it is pending*")
    result.stderr.no_fnmatch_line("*Event loop is closed*")
    result.stderr.no_fnmatch_line("*Exception ignored*")


def test_async_fixture_tmp_path_not_leaked(pytester: Pytester) -> None:
    """Async fixtures combined with tmp_path clean up temporary directories
    as usual."""
    pytester.makepyfile(
        test_tmp="""
        import pytest

        paths = []

        @pytest.fixture
        async def resource(tmp_path):
            paths.append(tmp_path)
            (tmp_path / "data.txt").write_text("hello")
            return tmp_path

        def test_one(resource):
            assert (resource / "data.txt").read_text() == "hello"

        def test_two():
            assert len(paths) == 1
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=2)
