"""Optional fixture ownership report.

This plugin implements the ``--fixture-ownership-report`` option, which
prints, at the end of the test session, a chronological report of the
*actual* fixture lifecycle events observed during the run:

* ``finalizer-registered`` -- teardown finalizers were registered for the
  fixture (this happens right before the fixture is executed, and stays
  visible even if the setup of the item fails midway).
* ``created`` -- the fixture was really executed and produced a value.
* ``create-failed`` -- the fixture function itself raised, so no value was
  ever created.
* ``dependency-failed`` -- a dependency of the fixture failed before the
  fixture function itself ran, so no value was ever created.
* ``borrowed`` -- an item (or another fixture) obtained an already created
  fixture.
* ``released`` -- the finalizers of a created fixture ran.
* ``release-skipped`` -- finalization ran for a fixture which was never
  created (its setup had failed), so there was nothing to release.  Such
  fixtures are never reported as successfully released.

The report is purely observational:

* it never calls ``request.getfixturevalue()`` or otherwise triggers
  fixture execution, so fixtures are never executed a second time (nor
  executed only because of the report);
* it never stores fixture values, only names, scopes and node ids;
* events are derived only from actual runtime state (hook invocations and
  cached results), never inferred from static declarations, so fixtures
  which were declared but never created do not show up as created or
  released;
* the state is kept per test session (in ``config.stash``), so concurrent
  or repeated sessions in the same process each get their own report.

Lifecycle example::

    # test_lifecycle.py
    import pytest


    @pytest.fixture(scope="session")
    def db():
        yield "db"


    @pytest.fixture
    def conn(db):
        yield "conn"


    def test_one(conn): ...
    def test_two(conn): ...

Running ``pytest --fixture-ownership-report test_lifecycle.py`` shows how
the session-scoped ``db`` fixture is created only once while the
function-scoped ``conn`` fixture is created and released separately for
each item (see testing/test_fixtureownership.py for the full output).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Generator
import dataclasses

from _pytest.config import Config
from _pytest.config.argparsing import Parser
from _pytest.fixtures import FixtureDef
from _pytest.fixtures import FixtureRequest
from _pytest.fixtures import SubRequest
from _pytest.nodes import Item
from _pytest.stash import StashKey
from _pytest.terminal import TerminalReporter
import pytest


fixture_ownership_key = StashKey["FixtureOwnershipRecorder"]()


@dataclasses.dataclass(frozen=True)
class FixtureEvent:
    """A single observed fixture lifecycle event.

    Only identifies the fixture and the involved item -- never carries the
    fixture value.
    """

    #: The kind of event, e.g. "created", "borrowed", "released".
    kind: str
    #: The name of the fixture.
    argname: str
    #: The scope of the fixture, e.g. "function", "session".
    scope: str
    #: The nodeid of the item which was active when the event happened.
    item: str
    #: Optional extra information, e.g. who borrowed the fixture.
    detail: str = ""


class FixtureOwnershipRecorder:
    """Records actual fixture lifecycle events for a single test session.

    A fresh recorder is created per session (stored in ``config.stash``),
    so parallel or repeated sessions in the same process do not share
    state.
    """

    def __init__(self) -> None:
        self.events: list[FixtureEvent] = []

    def record(
        self,
        kind: str,
        fixturedef: FixtureDef[object],
        *,
        item: str,
        detail: str = "",
    ) -> None:
        self.events.append(
            FixtureEvent(
                kind=kind,
                argname=fixturedef.argname,
                scope=fixturedef.scope,
                item=item,
                detail=detail,
            )
        )


def pytest_addoption(parser: Parser) -> None:
    group = parser.getgroup("debugconfig")
    group.addoption(
        "--fixture-ownership-report",
        action="store_true",
        default=False,
        help="Show a report of the actual fixture lifecycle events "
        "(creation, borrowing, finalizer registration and release) "
        "observed during the session.",
    )


def pytest_configure(config: Config) -> None:
    if config.option.fixture_ownership_report:
        # Per-session state: each Config (hence each session, including
        # in-process sessions started by pytester) gets its own recorder.
        config.stash[fixture_ownership_key] = FixtureOwnershipRecorder()


def _recorder(config: Config) -> FixtureOwnershipRecorder | None:
    return config.stash.get(fixture_ownership_key, None)


def _item_nodeid(request: FixtureRequest) -> str:
    pyfuncitem = getattr(request, "_pyfuncitem", None)
    if pyfuncitem is None:
        return "-"
    return str(pyfuncitem.nodeid)


@pytest.hookimpl(wrapper=True)
def pytest_fixture_setup(
    fixturedef: FixtureDef[object], request: SubRequest
) -> Generator[None, object, object]:
    recorder = _recorder(request.config)
    if recorder is None:
        return (yield)
    item = _item_nodeid(request)
    # FixtureDef.execute() resolves the statically declared dependencies of
    # the fixture before invoking this hook, so each dependency which
    # actually holds a value by now was genuinely borrowed by this fixture.
    fixture_defs = getattr(request, "_fixture_defs", None) or {}
    for depname in fixturedef.argnames:
        depdef = fixture_defs.get(depname)
        if depdef is None:
            continue
        dep_cached = depdef.cached_result
        if dep_cached is None or dep_cached[2] is not None:
            continue
        recorder.record(
            "borrowed",
            depdef,
            item=item,
            detail=f"by fixture {fixturedef.argname!r}",
        )
    # FixtureDef.execute() registers the teardown finalizers right before
    # invoking this hook, so reaching this point proves the registration
    # happened.  The event is recorded even if the setup goes on to fail.
    recorder.record("finalizer-registered", fixturedef, item=item)
    try:
        result = yield
    except BaseException as exc:
        cached = fixturedef.cached_result
        if cached is not None and cached[2] is not None:
            # The fixture function itself raised; the failure is cached.
            recorder.record(
                "create-failed",
                fixturedef,
                item=item,
                detail=f"fixture function raised {type(exc).__name__}",
            )
        else:
            # The hook raised before the fixture function ran, i.e. a
            # dynamically requested dependency (or the fixture resolution
            # itself) failed.
            recorder.record(
                "dependency-failed",
                fixturedef,
                item=item,
                detail=f"setup raised {type(exc).__name__} before the "
                "fixture function ran",
            )
        # The fixtures which were waiting on this failed fixture (their
        # execute() was suspended resolving it) never ran their own setup:
        # report them as dependency-failed rather than create-failed.
        for ancestor in request._iter_chain():
            if ancestor is request:
                continue
            recorder.record(
                "dependency-failed",
                ancestor._fixturedef,
                item=item,
                detail=f"dependency {fixturedef.argname!r} raised {type(exc).__name__}",
            )
        raise
    recorder.record("created", fixturedef, item=item)
    return result


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item: Item) -> Generator[None, None, None]:
    recorder = _recorder(item.config)
    if recorder is None:
        return (yield)
    try:
        return (yield)
    finally:
        # Record borrows even when the setup failed midway: the fixtures
        # which were actually created for the item are observable state.
        _record_item_borrows(recorder, item)


def _record_item_borrows(recorder: FixtureOwnershipRecorder, item: Item) -> None:
    request = getattr(item, "_request", None)
    fixture_defs = getattr(request, "_fixture_defs", None)
    if not fixture_defs:
        return
    for argname, fixturedef in fixture_defs.items():
        if argname == "request":
            continue
        cached = fixturedef.cached_result
        # Only record fixtures which actually hold a value right now --
        # never infer creation from declarations.
        if cached is None or cached[2] is not None:
            continue
        recorder.record("borrowed", fixturedef, item=str(item.nodeid), detail="by item")


def pytest_fixture_post_finalizer(
    fixturedef: FixtureDef[object], request: SubRequest
) -> None:
    recorder = _recorder(request.config)
    if recorder is None:
        return
    cached = fixturedef.cached_result
    if cached is None:
        # Nothing was ever set up for this fixturedef; finish() returned
        # early without running any finalizer.
        return
    item = _item_nodeid(request)
    if cached[2] is not None:
        # The setup failed, so the fixture never held a value: do not
        # report this as a successful release.
        recorder.record(
            "release-skipped",
            fixturedef,
            item=item,
            detail="fixture was never created; nothing to release",
        )
    else:
        recorder.record("released", fixturedef, item=item)


def pytest_terminal_summary(terminalreporter: TerminalReporter, config: Config) -> None:
    recorder = _recorder(config)
    if recorder is None:
        return
    terminalreporter.section("fixture ownership report")
    if not recorder.events:
        terminalreporter.write_line("no fixture lifecycle events were recorded")
        return
    for n, event in enumerate(recorder.events, 1):
        terminalreporter.write_line(
            f"{n:>4} {event.kind:<20} {event.scope:<9} {event.argname:<20} "
            f"item={event.item} {event.detail}".rstrip()
        )
    counts = Counter(event.kind for event in recorder.events)
    totals = ", ".join(f"{kind}={counts[kind]}" for kind in sorted(counts))
    terminalreporter.write_line(f"totals: {totals}")
