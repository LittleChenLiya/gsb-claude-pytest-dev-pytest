"""Read-only mark explanation report, emitted after collection.

This plugin implements the ``--explain-marks`` command-line option.  When
enabled, pytest prints a report right after collection which explains, for
every collected item, each *effective* marker occurrence together with its
origin (module, class, parametrize or function) and its *real* priority
(its position in the closest-first iteration order used by
:meth:`Node.get_closest_marker <_pytest.nodes.Node.get_closest_marker>`).

The report is strictly observational:

* every occurrence of a marker is listed individually -- same-name markers
  are never collapsed into a single value, and duplicates keep their
  original order;
* each parametrized item keeps its own parameter-level marks, marks are
  never "inherited" across parameter sets;
* markers which are not registered (via the ``markers`` ini option, builtin
  markers or plugin-registered ones) are listed in a separate diagnostics
  section;
* nothing is written back to the items (``own_markers``/``keywords`` are
  only read) and no fixture is ever triggered, so the report also works
  together with ``--collect-only``;
* items and occurrences are numbered with stable, deterministic indices so
  reports of repeated runs are directly comparable.
"""

from __future__ import annotations

from collections.abc import Iterator
from collections.abc import Sequence
import dataclasses
from typing import TYPE_CHECKING

from _pytest.config import Config
from _pytest.config.argparsing import Parser
from _pytest.mark.structures import Mark
from _pytest.nodes import Item
from _pytest.nodes import Node


if TYPE_CHECKING:
    from pathlib import Path


def pytest_addoption(parser: Parser) -> None:
    group = parser.getgroup("general")
    group.addoption(
        "--explain-marks",
        action="store_true",
        default=False,
        dest="explain_marks",
        help=(
            "Show a read-only report after collection explaining, for each "
            "collected item, every effective marker occurrence with its origin "
            "(module, class, parametrize or function) and its real priority. "
            "The report never modifies items and never triggers fixtures."
        ),
    )


@dataclasses.dataclass(frozen=True)
class MarkOccurrence:
    """A single occurrence of a marker on a collected item.

    Occurrences are never merged: if the same marker name is applied several
    times, each application gets its own occurrence, in the original order.
    """

    #: Position in closest-first iteration order; 0 is the highest priority,
    #: i.e. the occurrence returned first by ``Node.get_closest_marker``.
    priority: int
    #: The marker itself.
    mark: Mark
    #: Origin kind: "function", "parametrize", "class", "module", "package",
    #: "session" or the lower-cased node class name for anything else.
    source: str
    #: Nodeid of the node which owns the marker.
    owner: str


def _source_kind(node: Node) -> str:
    """Classify *node* into a human readable marker origin kind."""
    # Imported locally to avoid an import cycle at module import time.
    from _pytest.main import Session
    from _pytest.python import Class
    from _pytest.python import Function
    from _pytest.python import Module
    from _pytest.python import Package

    if isinstance(node, Function):
        return "function"
    if isinstance(node, Class):
        return "class"
    if isinstance(node, Package):
        return "package"
    if isinstance(node, Module):
        return "module"
    if isinstance(node, Session):
        return "session"
    return type(node).__name__.lower()


def iter_mark_occurrences(item: Item) -> Iterator[MarkOccurrence]:
    """Yield every marker occurrence of *item*, closest scope first.

    The iteration order matches ``Node.iter_markers_with_node``, so the first
    occurrence of a given marker name is exactly the one returned by
    ``Node.get_closest_marker`` -- this is the *real* priority of the
    occurrence.

    This is strictly read-only: it only reads the ``own_markers`` and
    ``callspec`` attributes and never touches ``keywords`` or fixtures.
    """
    priority = 0
    for node in item.iter_parents():
        own_markers: Sequence[Mark] = node.own_markers
        param_marks: Sequence[Mark] = ()
        if node is item:
            # Function.__init__ extends own_markers with the function's own
            # marks first and with callspec.marks (parametrization) last, so
            # the tail of own_markers originates from parametrization.
            callspec = getattr(item, "callspec", None)
            if callspec is not None and callspec.marks:
                split = len(own_markers) - len(callspec.marks)
                if split >= 0:
                    param_marks = own_markers[split:]
                    own_markers = own_markers[:split]
        source = _source_kind(node)
        for mark in own_markers:
            yield MarkOccurrence(priority, mark, source, node.nodeid)
            priority += 1
        for mark in param_marks:
            yield MarkOccurrence(priority, mark, "parametrize", node.nodeid)
            priority += 1


def _format_mark(mark: Mark) -> str:
    """Render a mark as ``name`` or ``name(args..., kwargs...)``."""
    parts = [repr(arg) for arg in mark.args]
    parts.extend(f"{key}={value!r}" for key, value in mark.kwargs.items())
    if not parts:
        return mark.name
    return f"{mark.name}({', '.join(parts)})"


def build_explanation_lines(config: Config, items: Sequence[Item]) -> list[str]:
    """Build the mark explanation report lines for the collected *items*.

    The output is deterministic: items are numbered in collection order and
    occurrences in real priority order, so two runs over the same collected
    tree produce byte-identical reports.
    """
    registered = {marker.name for marker in config._iter_registered_markers()}
    lines = [
        "mark explanation report "
        "(read-only: items are not modified, no fixtures are triggered):",
    ]
    # Unregistered marker diagnostics: (name, item nodeid, source), kept in
    # first-seen order for stable, comparable output.
    unregistered: list[tuple[str, str, str]] = []
    items_with_marks = 0
    for item_index, item in enumerate(items):
        occurrences = list(iter_mark_occurrences(item))
        if not occurrences:
            continue
        items_with_marks += 1
        lines.append(f"  item #{item_index} {item.nodeid}")
        seen_names: set[str] = set()
        for occurrence in occurrences:
            name = occurrence.mark.name
            # The first occurrence of a name is the effective one (the one
            # get_closest_marker returns); later duplicates are shadowed but
            # still reported, in their original order.
            effectiveness = "effective" if name not in seen_names else "shadowed"
            seen_names.add(name)
            registration = "registered" if name in registered else "UNREGISTERED"
            lines.append(
                f"    mark #{occurrence.priority} {_format_mark(occurrence.mark)}"
                f" [source={occurrence.source}, owner={occurrence.owner}]"
                f" ({effectiveness}, {registration})"
            )
            if name not in registered:
                unregistered.append((name, item.nodeid, occurrence.source))
    lines.append(f"  {items_with_marks} of {len(items)} collected items carry markers")
    if unregistered:
        lines.append(
            "  unregistered markers "
            "(not builtin, plugin-provided or listed in the `markers` ini):"
        )
        for name, nodeid, source in unregistered:
            lines.append(f"    {name} [item={nodeid}, source={source}]")
    return lines


def pytest_report_collectionfinish(
    config: Config,
    start_path: Path,
    items: Sequence[Item],
) -> list[str]:
    """Emit the mark explanation report after collection, when requested."""
    if not config.option.explain_marks:
        return []
    return build_explanation_lines(config, items)
