"""Explanation report for markers collected on test items.

This module provides a read-only introspection of the markers that pytest
collects on each test item during the collection phase.  Markers are listed
in the order pytest resolves them (closest first, i.e. highest priority),
annotated with their source (module, class, parametrize or function) and a
stable sequence number.  Same-name markers are kept as separate entries so
that no information is lost.

The report never modifies item keywords and never triggers fixture setup.
"""

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Iterator
import dataclasses
from typing import Literal
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from _pytest.config import Config
    from _pytest.main import Session
    from _pytest.mark.structures import Mark
    from _pytest.nodes import Item
    from _pytest.nodes import Node


__all__ = ["MarkExplanation", "MarkSource", "iter_mark_explanations"]


#: The origin of a marker occurrence on a test item.
#:
#: ``module``      -- a ``pytestmark`` applied at module/package level.
#: ``class``       -- a ``pytestmark`` applied to the test class.
#: ``parametrize``  -- a marker attached to a single parameter set via
#:                     :func:`pytest.param(marks=...) <pytest.param>`.
#: ``function``     -- a marker applied directly to the test function.
MarkSource = Literal["module", "class", "parametrize", "function"]


@dataclasses.dataclass(frozen=True)
class MarkExplanation:
    """A single marker occurrence on a test item, with its source.

    :param seq:
        Stable, globally comparable sequence number.  Markers are numbered
        in collection order, and within an item in priority order (closest
        first).
    :param name: Name of the marker.
    :param source: Where the marker came from (see :data:`MarkSource`).
    :param nodeid: Nodeid of the test item the marker belongs to.
    :param mark: The actual :class:`~_pytest.mark.structures.Mark` object.
    :param registered:
        Whether the marker name is registered in the ``markers`` ini option.
    """

    seq: int
    name: str
    source: MarkSource
    nodeid: str
    mark: Mark
    registered: bool


def _classify_source(node: Node, mark: Mark) -> MarkSource:
    """Determine the source of *mark* which lives on *node*.

    For :class:`~_pytest.python.Function` items the function-level marks and
    the parametrize-level marks share the item's ``own_markers`` list, so we
    tell them apart by object identity against ``callspec.marks``.
    """
    # Imported lazily to avoid a circular import (python imports mark, and
    # nodes imports mark.structures while mark is being initialised).
    from _pytest.nodes import Item
    from _pytest.python import Class
    from _pytest.python import Function
    from _pytest.python import Module

    if isinstance(node, Function):
        callspec = getattr(node, "callspec", None)
        if callspec is not None and any(mark is m for m in callspec.marks):
            return "parametrize"
        return "function"
    if isinstance(node, Item):
        # Custom item types have no callspec; every mark is function-level.
        return "function"
    if isinstance(node, Class):
        return "class"
    if isinstance(node, Module):
        return "module"
    # Package / Directory / Session / custom collectors: treat as
    # module-level since they are collection scopes above the class.
    return "module"


def iter_mark_explanations(
    items: Iterable[Item],
    registered_names: Iterable[str],
) -> Iterator[MarkExplanation]:
    """Yield :class:`MarkExplanation` objects for every marker on every item.

    :param items:
        The collected test items (e.g. ``session.items``).
    :param registered_names:
        Names of markers registered in the ``markers`` ini option.
    :returns:
        An iterator of explanations in stable collection order.

    The ``parametrize`` directive mark is skipped: it is a collection
    mechanism whose args hold the raw parametrization data, and the
    parameter-level marks it produces are reported separately.
    """
    registered = set(registered_names)
    seq = 0
    for item in items:
        for node, mark in item.iter_markers_with_node():
            if mark.name == "parametrize":
                continue
            source = _classify_source(node, mark)
            yield MarkExplanation(
                seq=seq,
                name=mark.name,
                source=source,
                nodeid=item.nodeid,
                mark=mark,
                registered=mark.name in registered,
            )
            seq += 1


def _format_mark_args(mark: Mark) -> str:
    """Return a compact ``args=..., kwargs=...`` representation for *mark*."""
    parts: list[str] = []
    if mark.args:
        parts.append(f"args={mark.args!r}")
    if mark.kwargs:
        kw = ", ".join(f"{k}={v!r}" for k, v in mark.kwargs.items())
        parts.append(f"kwargs={kw}")
    return f"({', '.join(parts)})" if parts else ""


def write_markers_explain(config: Config, session: Session) -> None:
    """Write the mark explanation report to the terminal writer.

    This is a read-only operation: it only walks ``own_markers`` and the
    collection tree; it never modifies item keywords and never triggers
    fixture setup.
    """
    import _pytest.config

    session.perform_collect()
    tw = _pytest.config.create_terminal_writer(config)
    registered = {m.name for m in config._iter_registered_markers()}

    tw.sep("=", "Mark explanation report")
    tw.line("Markers are listed in priority order (closest first).")
    tw.line("A '*' marks the marker that wins for its name on that item.")
    tw.line()

    explanations = list(iter_mark_explanations(session.items, registered))

    if not explanations:
        tw.line("No markers were collected on any test item.")
        tw.line()
    else:
        current_nodeid: str | None = None
        seen_names: set[str] = set()
        for expl in explanations:
            if expl.nodeid != current_nodeid:
                if current_nodeid is not None:
                    tw.line()
                current_nodeid = expl.nodeid
                seen_names = set()
                tw.write(f"{expl.nodeid}", bold=True)
                tw.line(":")
            reg = "registered" if expl.registered else "UNREGISTERED"
            priority = "*" if expl.name not in seen_names else " "
            seen_names.add(expl.name)
            tw.line(
                f"  [{expl.seq}]{priority} {expl.name} ({expl.source}, {reg})"
                f"{_format_mark_args(expl.mark)}"
            )
        tw.line()

    # Diagnose markers that are used but not registered, separately.
    unknown: dict[str, list[str]] = {}
    for expl in explanations:
        if not expl.registered:
            unknown.setdefault(expl.name, []).append(expl.nodeid)

    if unknown:
        tw.sep("-", "Unknown markers (used but not registered)")
        for name in sorted(unknown):
            tw.write(f"  {name}", red=True)
            tw.line(f" (used by {len(unknown[name])} item(s))")
            for nodeid in unknown[name]:
                tw.line(f"      - {nodeid}")
        tw.line()
