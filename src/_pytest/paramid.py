"""Plugin for exporting stable parameterized test identities.

This plugin adds the ``--export-param-ids=PATH`` command-line option, which
exports a list of stable identities for all collected parameterized test
cases after collection completes. The identities can be used to compare and
select test cases across machines.

The export is read-only: it does not modify the collection order and does not
execute any test bodies.

See ``doc/en/paramid.rst`` for the identity specification.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

from _pytest.config import Config
from _pytest.config.argparsing import Parser
from _pytest.nodes import Item
import pytest


def pytest_addoption(parser: Parser) -> None:
    group = parser.getgroup("debugconfig")
    group.addoption(
        "--export-param-ids",
        action="store",
        dest="export_param_ids",
        metavar="PATH",
        default=None,
        help="Export stable parameterized test identities to PATH "
        "(use '-' for stdout). The export is read-only and does not "
        "execute any tests.",
    )


def _stable_encode(value: object) -> str | None:
    """Try to stably encode a value for use in a test identity.

    Returns a stable string representation of the value, or ``None`` if the
    value cannot be stably encoded (e.g. a custom object whose repr may
    contain memory addresses).

    Only basic types and nested tuples/lists/dicts of basic types are
    supported. Hashes are never used, as they are not reliably identifiable.
    """
    if value is None or isinstance(value, (bool, int, float, str, bytes)):
        return repr(value)
    if isinstance(value, (tuple, list)):
        encoded = [_stable_encode(v) for v in value]
        if all(e is not None for e in encoded):
            return f"[{', '.join(encoded)}]"
        return None
    if isinstance(value, dict):
        encoded_parts = []
        for k, v in value.items():
            k_enc = _stable_encode(k)
            v_enc = _stable_encode(v)
            if k_enc is None or v_enc is None:
                return None
            encoded_parts.append(f"{k_enc}: {v_enc}")
        return "{" + ", ".join(encoded_parts) + "}"
    return None


def _get_relative_path(item: Item, config: Config) -> str:
    """Return the rootpath-relative source path of the item."""
    try:
        return str(item.path.relative_to(config.rootpath))
    except ValueError:
        # item.path is not under rootpath; fall back to os.path.relpath.
        return os.path.relpath(item.path, config.rootpath)


def _extract_identity(item: Item, config: Config) -> tuple[str, bool] | None:
    """Extract the stable identity of a parameterized test item.

    Returns a tuple of (identity, is_stable), or ``None`` if the item is not
    parameterized.

    The identity is composed of:
    - the rootpath-relative source path
    - the original test name (without parametrization decorations)
    - the parameter names and their explicit IDs (or stably encoded values)

    If a parameter has no explicit ID and its value cannot be stably encoded,
    the identity is marked as not stable (``is_stable=False``).
    """
    callspec = getattr(item, "callspec", None)
    if callspec is None:
        return None

    originalname: str = getattr(item, "originalname", item.name)
    rel_path = _get_relative_path(item, config)

    # Collect parameter name -> (id, is_explicit) pairs.
    param_pairs: list[tuple[str, str, bool]] = []
    all_stable = True

    idlist: Sequence[str] = getattr(callspec, "_idlist", ())
    id_explicit: Sequence[bool] = getattr(callspec, "_id_explicit", ())
    argnames_per_id: Sequence[Sequence[str]] = getattr(
        callspec, "_argnames_per_id", ()
    )
    params: dict[str, object] = getattr(callspec, "params", {})

    for i, param_id in enumerate(idlist):
        is_explicit = id_explicit[i] if i < len(id_explicit) else False
        argnames = argnames_per_id[i] if i < len(argnames_per_id) else ()
        for argname in argnames:
            if is_explicit:
                # Explicit ID: stable.
                param_pairs.append((argname, param_id, True))
            else:
                # No explicit ID: try to stably encode the value.
                value = params.get(argname)
                encoded = _stable_encode(value)
                if encoded is not None:
                    param_pairs.append((argname, encoded, True))
                else:
                    param_pairs.append((argname, "", False))
                    all_stable = False

    # Sort by parameter name for a canonical identity.
    param_pairs.sort(key=lambda p: p[0])

    if not param_pairs:
        # Parameterized but no parameters (shouldn't normally happen).
        return None

    parts = [f"{name}={id_}" if id_ else name for name, id_, _ in param_pairs]
    identity = f"{rel_path}::{originalname}[{','.join(parts)}]"

    return identity, all_stable


@pytest.hookimpl(wrapper=True)
def pytest_collection_finish(session: pytest.Session):
    """Export stable parameterized test identities after collection completes.

    This hook is called after collection has been performed and modified. It
    iterates over the collected items, extracts their stable identities,
    detects duplicates, and exports the list to the specified file (or stdout).

    The export is read-only: it does not modify the collection order and does
    not execute any test bodies.
    """
    config = session.config
    export_path = config.option.export_param_ids

    if export_path is None:
        yield
        return

    # Let other plugins' collection_finish hooks run first.
    yield

    # Extract identities from all parameterized items.
    entries: list[tuple[str, str, str]] = []  # (status, identity, nodeid)
    seen: dict[str, str] = {}  # identity -> first nodeid (for stable identities)

    for item in session.items:
        result = _extract_identity(item, config)
        if result is None:
            continue
        identity, is_stable = result
        nodeid = item.nodeid

        if not is_stable:
            entries.append(("UNSTABLE", identity, nodeid))
            continue

        if identity in seen:
            # Duplicate stable identity: report conflict.
            first_nodeid = seen[identity]
            entries.append(("CONFLICT", identity, f"{first_nodeid} <-> {nodeid}"))
        else:
            seen[identity] = nodeid
            entries.append(("STABLE", identity, nodeid))

    # Write the export.
    lines = [
        "# Param ID Export",
        "# Format: <status>\t<identity>\t<nodeid>",
        "# status: STABLE, UNSTABLE, or CONFLICT",
        "#   STABLE   - the identity is stable and can be used across machines",
        "#   UNSTABLE - the identity cannot be stably encoded (no explicit ID)",
        "#   CONFLICT - two items have the same stable identity",
        "#",
        f"# total: {len(entries)}",
        "",
    ]
    for status, identity, nodeid in entries:
        lines.append(f"{status}\t{identity}\t{nodeid}")

    content = "\n".join(lines) + "\n"

    if export_path == "-":
        config.get_terminal_writer().write(content)
    else:
        with open(export_path, "w", encoding="utf-8") as f:
            f.write(content)
