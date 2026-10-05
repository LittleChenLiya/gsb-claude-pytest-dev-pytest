"""Optional fixture ownership report.

Records the *actual* fixture lifecycle observed during a test session:
which fixture instances were really created (executed), which items or
fixtures borrowed them, which finalizers were registered, and which were
released.

Unlike ``--setup-show`` (which traces setup/teardown as it happens) and
``--setup-plan`` (which only predicts what would run), this report is built
from observed events only. It never executes fixtures a second time and never
stores fixture values.

Enable with ``--fixture-ownership-report``.
"""

from __future__ import annotations

from collections.abc import Generator
import json
from pathlib import Path
import threading
from typing import Any

import pytest
from _pytest.config import Config
from _pytest.config.argparsing import Parser
from _pytest.fixtures import FixtureDef
from _pytest.fixtures import SubRequest
from _pytest.runner import SetupState


# Config attribute used to stash the per-session report state.  Each session
# gets its own Config, so parallel sessions never share state.
_REPORT_ATTR = "_fixture_ownership_report"

# A lock is not strictly needed because each session has its own report, but
# it makes the report safe if two sessions ever overlap in one process.
_LOCK = threading.Lock()


class FixtureOwnershipReport:
    """Per-session fixture ownership report state.

    Only metadata is stored -- never fixture values.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self.events: list[dict[str, Any]] = []
        # Names of fixtures that were actually created (executed) in this
        # session.  Used to avoid reporting a release for a fixture that was
        # never created.
        self._created: set[tuple[str, str]] = set()

    # -- event recording ---------------------------------------------------

    def record_create(
        self,
        name: str,
        scope: str,
        owner_nodeid: str,
        outcome: str,
        error: BaseException | None,
        deps: list[str],
    ) -> None:
        with _LOCK:
            self._created.add((name, scope))
            event: dict[str, Any] = {
                "kind": "create",
                "name": name,
                "scope": scope,
                "owner": owner_nodeid,
                "outcome": outcome,
            }
            if error is not None:
                event["error"] = f"{type(error).__name__}: {error}"
            if deps:
                event["dependencies"] = deps
            self.events.append(event)

    def record_borrow(
        self,
        name: str,
        scope: str,
        borrower_nodeid: str,
        owner_nodeid: str,
    ) -> None:
        with _LOCK:
            self.events.append(
                {
                    "kind": "borrow",
                    "name": name,
                    "scope": scope,
                    "borrower": borrower_nodeid,
                    "owner": owner_nodeid,
                }
            )

    def record_finalizer(self, name: str, scope: str, nodeid: str) -> None:
        with _LOCK:
            self.events.append(
                {
                    "kind": "finalizer",
                    "name": name,
                    "scope": scope,
                    "node": nodeid,
                }
            )

    def record_release(
        self,
        name: str,
        scope: str,
        owner_nodeid: str,
        outcome: str,
        error: BaseException | None,
    ) -> None:
        with _LOCK:
            # Only report a release for a fixture that was actually created.
            # A fixture whose setup failed before it could run (e.g. a
            # dependency failure) is never finalized, so we must not report
            # a successful release for it.
            if (name, scope) not in self._created:
                return
            event: dict[str, Any] = {
                "kind": "release",
                "name": name,
                "scope": scope,
                "owner": owner_nodeid,
                "outcome": outcome,
            }
            if error is not None:
                event["error"] = f"{type(error).__name__}: {error}"
            self.events.append(event)

    # -- output ------------------------------------------------------------

    def render_terminal(self) -> list[str]:
        lines: list[str] = []
        lines.append("fixture ownership report:")
        for event in self.events:
            kind = event["kind"]
            if kind == "create":
                line = (
                    f"  created: {event['scope']} {event['name']} "
                    f"owner={event['owner']} outcome={event['outcome']}"
                )
                if "error" in event:
                    line += f" error={event['error']}"
                lines.append(line)
            elif kind == "borrow":
                lines.append(
                    f"  borrowed: {event['scope']} {event['name']} "
                    f"borrower={event['borrower']} owner={event['owner']}"
                )
            elif kind == "finalizer":
                lines.append(
                    f"  finalizer: {event['scope']} {event['name']} "
                    f"node={event['node']}"
                )
            elif kind == "release":
                line = (
                    f"  released: {event['scope']} {event['name']} "
                    f"owner={event['owner']} outcome={event['outcome']}"
                )
                if "error" in event:
                    line += f" error={event['error']}"
                lines.append(line)
        if not self.events:
            lines.append("  (no fixture events recorded)")
        return lines

    def render_json(self) -> str:
        return json.dumps(self.events, indent=2, default=str)


# -- wrappers (installed once at import time; no-op when disabled) ---------

_original_execute = FixtureDef.execute
_original_finish = FixtureDef.finish
_original_addfinalizer = SetupState.addfinalizer


def _format_nodeid(nodeid: str) -> str:
    """Format a nodeid for display.  The session node has an empty nodeid."""
    return nodeid if nodeid else "<session>"


def _execute_wrapper(self: FixtureDef[Any], request: SubRequest) -> Any:
    report: FixtureOwnershipReport | None = getattr(
        request.config, _REPORT_ATTR, None
    )
    if report is None or self.argname == "request":
        return _original_execute(self, request)

    try:
        result = _original_execute(self, request)
    except BaseException:
        # Creation failed (or a cached exception was re-raised).  The
        # pytest_fixture_setup hook records the create failure.
        raise
    else:
        # Record a borrow for every use of the fixture, including the first
        # use by the item that created it.  This shows which items share the
        # fixture instance.  The borrower is the test item that requested the
        # fixture; the owner is the scope node that created it.
        borrower = _format_nodeid(request._pyfuncitem.nodeid)
        owner = _format_nodeid(request.node.nodeid)
        report.record_borrow(self.argname, self.scope, borrower, owner)
        return result


def _finish_wrapper(self: FixtureDef[Any], request: SubRequest) -> None:
    report: FixtureOwnershipReport | None = getattr(
        request.config, _REPORT_ATTR, None
    )
    if report is None or self.argname == "request":
        _original_finish(self, request)
        return

    if self.cached_result is None:
        _original_finish(self, request)
        return

    owner = _format_nodeid(request.node.nodeid)
    try:
        _original_finish(self, request)
    except BaseException as e:
        # A finalizer raised.  The release event is still recorded -- the
        # exception must not erase it.
        report.record_release(self.argname, self.scope, owner, "failure", e)
        raise
    else:
        report.record_release(self.argname, self.scope, owner, "success", None)


def _addfinalizer_wrapper(
    self: SetupState, finalizer: Any, node: Any
) -> None:
    report: FixtureOwnershipReport | None = getattr(
        node.config, _REPORT_ATTR, None
    )
    if report is None:
        _original_addfinalizer(self, finalizer, node)
        return

    report.record_finalizer("item", "function", _format_nodeid(node.nodeid))
    _original_addfinalizer(self, finalizer, node)


FixtureDef.execute = _execute_wrapper  # type: ignore[assignment]
FixtureDef.finish = _finish_wrapper  # type: ignore[assignment]
SetupState.addfinalizer = _addfinalizer_wrapper  # type: ignore[assignment]


# -- hooks -----------------------------------------------------------------


def pytest_addoption(parser: Parser) -> None:
    group = parser.getgroup("debugconfig")
    group.addoption(
        "--fixture-ownership-report",
        action="store_true",
        help="Record an optional fixture ownership report (created, borrowed, "
        "finalizers registered, released) without executing fixtures a "
        "second time",
    )
    group.addoption(
        "--fixture-ownership-report-file",
        action="store",
        default=None,
        metavar="PATH",
        help="Write the fixture ownership report as JSON to PATH",
    )


def pytest_configure(config: Config) -> None:
    if config.option.fixture_ownership_report:
        setattr(config, _REPORT_ATTR, FixtureOwnershipReport(config))


@pytest.hookimpl(wrapper=True)
def pytest_fixture_setup(
    fixturedef: FixtureDef[Any], request: SubRequest
) -> Generator[None, Any, Any]:
    report: FixtureOwnershipReport | None = getattr(
        request.config, _REPORT_ATTR, None
    )
    if report is None:
        return (yield)

    if fixturedef.argname == "request":
        return (yield)

    deps = [arg for arg in fixturedef.argnames if arg != "request"]
    owner = _format_nodeid(request.node.nodeid)

    try:
        result = yield
    except BaseException as e:
        # The fixture's own setup failed.  This is a creation failure, not a
        # dependency failure (a dependency failure would have prevented this
        # hook from being called at all).
        report.record_create(
            fixturedef.argname, fixturedef.scope, owner, "failure", e, deps
        )
        raise
    else:
        report.record_create(
            fixturedef.argname, fixturedef.scope, owner, "success", None, deps
        )
        return result


def pytest_sessionfinish(session: Any) -> None:
    config = session.config
    report: FixtureOwnershipReport | None = getattr(config, _REPORT_ATTR, None)
    if report is None:
        return

    # Print the terminal report.
    capman = config.pluginmanager.getplugin("capturemanager")
    if capman:
        capman.suspend_global_capture()
    tw = config.get_terminal_writer()
    tw.line()
    tw.sep("=", "fixture ownership report")
    for line in report.render_terminal():
        tw.line(line)
    tw.sep("=", "end fixture ownership report")
    tw.flush()
    if capman:
        capman.resume_global_capture()

    # Write the JSON report if requested.
    report_path = config.option.fixture_ownership_report_file
    if report_path is not None:
        try:
            Path(report_path).write_text(report.render_json(), encoding="utf-8")
        except OSError as e:
            # Don't let a report-writing failure break the session.
            tw = config.get_terminal_writer()
            tw.line(
                f"failed to write fixture ownership report to {report_path}: {e}"
            )