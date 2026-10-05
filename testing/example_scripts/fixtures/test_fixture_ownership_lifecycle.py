"""Lifecycle example for the fixture ownership report.

Demonstrates the *actual* fixture lifecycle observed by
``--fixture-ownership-report``:

* A session-scoped fixture is created once and borrowed by every item.
* A function-scoped fixture is created and released per item.
* A fixture that registers a finalizer and then fails setup still shows the
  registered finalizer event.
* A fixture whose dependency fails is never created and never released.

Run with::

    pytest --fixture-ownership-report test_fixture_ownership_lifecycle.py

The report is built from observed events only -- it never executes fixtures a
second time and never stores fixture values.
"""

from __future__ import annotations

import pytest


# -- session-scoped fixture: created once, shared by all items -------------


@pytest.fixture(scope="session")
def session_resource():
    """A session-scoped resource created once and borrowed by all items."""
    resource = {"id": "session-resource", "created": True}
    yield resource
    resource["created"] = False


# -- function-scoped fixture: created and released per item ----------------


@pytest.fixture(scope="function")
def function_resource():
    """A function-scoped resource created and released for each item."""
    resource = {"id": "function-resource"}
    yield resource


# -- fixture with a finalizer that fails setup -----------------------------


@pytest.fixture
def fixture_with_finalizer(request):
    """Registers a finalizer and then fails setup.

    The registered finalizer event is still recorded in the ownership report
    even though setup fails.
    """

    def finalizer():
        pass

    request.addfinalizer(finalizer)
    raise RuntimeError("setup failed after registering finalizer")


# -- fixture with a failing dependency --------------------------------------


@pytest.fixture
def failing_dependency():
    """A fixture that fails during setup."""
    raise RuntimeError("dependency setup failed")


@pytest.fixture
def dependent_fixture(failing_dependency):
    """Depends on a failing fixture -- never created, never released."""
    return "dependent value"


# -- tests ------------------------------------------------------------------


def test_first_item(session_resource, function_resource):
    assert session_resource["created"] is True
    assert function_resource["id"] == "function-resource"


def test_second_item(session_resource, function_resource):
    assert session_resource["created"] is True
    assert function_resource["id"] == "function-resource"


def test_setup_failure_shows_finalizer(fixture_with_finalizer):
    """This test fails during fixture setup; the registered finalizer event
    is still recorded in the ownership report."""


def test_dependency_failure(dependent_fixture):
    """This test fails because its dependency fails; the dependent fixture is
    never created and never released."""
