"""Export a stable identity manifest for parametrized tests.

This plugin implements ``--stable-manifest=PATH``.  When the option is
given, pytest stops after collection (test bodies are never executed,
exactly like ``--collect-only``) and writes a JSON manifest describing a
*stable identity* for every collected parametrized test.

The manifest is meant for cross-machine comparison of test selection:
two checkouts of the same test suite on different machines must produce
byte-identical manifests.  To guarantee this, identities are built only
from

* the test file path, relative to the rootdir (POSIX separators),
* the original (unparametrized) test name,
* the parameter names, and
* explicit parameter IDs (``ids=`` or ``pytest.param(..., id=...)``).

When a parameter has no explicit ID, its value is used only if it can be
stably encoded (``None``, ``bool``, ``int``, ``float``, ``complex``,
``str``, ``bytes``, enums, and containers thereof).  Anything else --
most notably arbitrary objects, whose ``repr()`` may embed a memory
address -- is reported as ``unstable`` in the manifest.  The plugin
never falls back to hashing a ``repr()``: an identity that cannot be
determined reliably is reported as such instead of being faked.

If two collected items end up with the same stable identity (for
example because of duplicate explicit IDs), the identity is reported
once in the ``conflicts`` list together with *both* nodeids; entries
are never silently overwritten.

The manifest preserves the original collection order and never changes
it.
"""

from __future__ import annotations

import dataclasses
import enum
import json
import os
from pathlib import Path
from typing import Any

from _pytest.config import Config
from _pytest.config import ExitCode
from _pytest.config.argparsing import Parser
from _pytest.main import Session
from _pytest.mark.structures import HIDDEN_PARAM
from _pytest.mark.structures import ParameterSet
from _pytest.nodes import Item
from _pytest.stash import StashKey
from _pytest.terminal import TerminalReporter
import pytest


manifest_result_key: StashKey[ManifestResult] = StashKey()


def pytest_addoption(parser: Parser) -> None:
    group = parser.getgroup("stablemanifest", "stable identity manifest")
    group.addoption(
        "--stable-manifest",
        action="store",
        dest="stable_manifest_path",
        metavar="PATH",
        default=None,
        help=(
            "Write a JSON manifest with a stable identity for every "
            "parametrized test to PATH after collection, then exit without "
            "running any test body (like --collect-only).  Useful for "
            "cross-machine comparison of test selection."
        ),
    )


@pytest.hookimpl(tryfirst=True)
def pytest_runtestloop(session: Session) -> bool | None:
    if session.config.option.stable_manifest_path is not None:
        # Exporting the manifest must never execute test bodies.
        return True
    return None


def pytest_collection_finish(session: Session) -> None:
    option = session.config.option.stable_manifest_path
    if option is None:
        return
    path = Path(option)
    if not path.is_absolute():
        path = Path(session.config.invocation_params.dir) / path
    result = _build_manifest(session)
    text = json.dumps(result.manifest, indent=2, ensure_ascii=False) + "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    except OSError as e:
        raise pytest.UsageError(
            f"could not write stable identity manifest to {path}: {e}"
        ) from e
    result.path = path
    session.config.stash[manifest_result_key] = result


def pytest_terminal_summary(
    terminalreporter: TerminalReporter,
    exitstatus: ExitCode,
    config: Config,
) -> None:
    result = config.stash.get(manifest_result_key, None)
    if result is None:
        return
    terminalreporter.section("stable identity manifest")
    terminalreporter.write_line(f"manifest written to: {result.path}")
    terminalreporter.write_line(
        f"{result.total} parametrized item(s): "
        f"{result.n_stable} stable, {result.n_unstable} unstable, "
        f"{len(result.conflicts)} conflicting identitie(s)"
    )
    for conflict in result.conflicts:
        terminalreporter.write_line(
            "CONFLICT: identity "
            f"{conflict['identity']!r} is shared by nodeids: "
            + ", ".join(conflict["nodeids"]),
            red=True,
        )
    for entry in result.entries:
        if entry["stable"]:
            continue
        reasons = "; ".join(p["reason"] for p in entry["params"] if p.get("reason"))
        terminalreporter.write_line(
            f"unstable identity: {entry['nodeid']} ({reasons})", yellow=True
        )


@dataclasses.dataclass
class _ParamIdentity:
    """Identity contribution of a single parameter of one item."""

    name: str
    # The stable token for this parameter, or None when unstable.
    token: str | None
    # "explicit-id" | "value" | "unstable".
    source: str
    # Why the parameter is unstable (only for source == "unstable").
    reason: str | None = None


@dataclasses.dataclass
class ManifestResult:
    """Outcome of a manifest export, for terminal reporting and tests."""

    manifest: dict[str, Any]
    entries: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    total: int
    n_stable: int
    n_unstable: int
    path: Path | None = None


def _build_manifest(session: Session) -> ManifestResult:
    entries: list[dict[str, Any]] = []
    # Stable identity -> nodeids, in collection order.  Used to detect
    # duplicate identities without ever overwriting an entry.
    identities: dict[str, list[str]] = {}
    n_stable = 0
    n_unstable = 0

    for item in session.items:
        params = _item_param_identities(item)
        if params is None:
            # Not a parametrized item.
            continue
        relpath = _relative_path(item, session.config)
        originalname = _original_name(item)
        stable = all(p.token is not None for p in params)
        identity = _identity_string(relpath, originalname, params) if stable else None
        if stable:
            n_stable += 1
            assert identity is not None
            identities.setdefault(identity, []).append(item.nodeid)
        else:
            n_unstable += 1
        entries.append(
            {
                "nodeid": item.nodeid,
                "path": relpath,
                "name": originalname,
                "params": [
                    {
                        "name": p.name,
                        "source": p.source,
                        "token": p.token,
                        **({"reason": p.reason} if p.reason else {}),
                    }
                    for p in params
                ],
                "stable": stable,
                "identity": identity,
            }
        )

    conflicts = [
        {"identity": identity, "nodeids": nodeids}
        for identity, nodeids in identities.items()
        if len(nodeids) > 1
    ]
    manifest: dict[str, Any] = {
        "version": 1,
        "entries": entries,
        "conflicts": conflicts,
    }
    return ManifestResult(
        manifest=manifest,
        entries=entries,
        conflicts=conflicts,
        total=len(entries),
        n_stable=n_stable,
        n_unstable=n_unstable,
    )


def _item_param_identities(item: Item) -> list[_ParamIdentity] | None:
    """Compute the per-parameter identities of a parametrized item.

    Returns None for items which are not parametrized at all.
    """
    callspec = getattr(item, "callspec", None)
    if callspec is None:
        return None
    explicit_ids = _explicit_param_ids(item, callspec)
    params: list[_ParamIdentity] = []
    for argname, value in callspec.params.items():
        if argname in explicit_ids:
            params.append(_ParamIdentity(argname, explicit_ids[argname], "explicit-id"))
            continue
        token = _stable_token(value)
        if token is not None:
            params.append(_ParamIdentity(argname, token, "value"))
        else:
            params.append(
                _ParamIdentity(
                    argname,
                    None,
                    "unstable",
                    reason=(
                        "no explicit id and value of type "
                        f"{type(value).__name__!r} cannot be stably encoded; "
                        "give the parameter an explicit id via ids= or "
                        "pytest.param(..., id=...)"
                    ),
                )
            )
    return params


def _explicit_param_ids(item: Item, callspec: Any) -> dict[str, str]:
    """Resolve explicit user-provided IDs for the item's parameters.

    Looks at the ``parametrize`` marks (in the same order pytest applies
    them) and extracts IDs given via ``pytest.param(..., id=...)``, an
    ``ids=[...]`` sequence, or an ``ids=callable``.  Returns a mapping of
    argument name to explicit ID.
    """
    result: dict[str, str] = {}
    for mark in item.iter_markers(name="parametrize"):
        if len(mark.args) < 2:
            continue
        try:
            argnames, _ = ParameterSet._parse_parametrize_args(
                mark.args[0], mark.args[1]
            )
        except Exception:  # noqa: S112 -- malformed mark: skip id resolution
            continue
        if not argnames:
            continue
        # All argnames of one parametrize call share the parameter set index.
        index = callspec.indices.get(argnames[0])
        if index is None:
            continue

        set_id = _parameterset_explicit_id(mark.args[1], index)
        if set_id is not None:
            for name in argnames:
                result.setdefault(name, set_id)
            continue

        ids = mark.kwargs.get("ids")
        if ids is None:
            continue
        if callable(ids):
            for name in argnames:
                if name not in callspec.params:
                    continue
                try:
                    token = ids(callspec.params[name])
                except Exception:
                    token = None
                if token is not None:
                    result.setdefault(name, str(token))
        else:
            try:
                token = ids[index]
            except (TypeError, KeyError, IndexError):
                token = None
            if token is not None:
                for name in argnames:
                    result.setdefault(name, str(token))
    return result


def _parameterset_explicit_id(argvalues: Any, index: int) -> str | None:
    """Return the ``pytest.param(..., id=...)`` ID of the parameter set at
    ``index``, if any."""
    try:
        candidate = argvalues[index]
    except (TypeError, KeyError, IndexError):
        return None
    if (
        isinstance(candidate, ParameterSet)
        and candidate.id is not None
        and candidate.id is not HIDDEN_PARAM
    ):
        return str(candidate.id)
    return None


class _UnstableValue(Exception):
    """Raised when a value cannot be encoded stably."""


def _encode_stable(value: object) -> Any:
    """Encode ``value`` into a JSON-serializable, machine-independent form.

    Raises _UnstableValue for values which have no stable encoding.  In
    particular, arbitrary objects are rejected: their ``repr()`` may
    contain a memory address, and hashing it would only pretend the
    identity is reliable.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float, str)):
        return value
    if isinstance(value, bytes):
        return {"__bytes__": value.hex()}
    if isinstance(value, complex):
        return {"__complex__": [value.real, value.imag]}
    if isinstance(value, enum.Enum):
        cls = type(value)
        return {"__enum__": [f"{cls.__module__}.{cls.__qualname__}", value.name]}
    if isinstance(value, tuple):
        return {"__tuple__": [_encode_stable(v) for v in value]}
    if isinstance(value, list):
        return [_encode_stable(v) for v in value]
    if isinstance(value, (set, frozenset)):
        encoded = [_encode_stable(v) for v in value]
        return {"__set__": sorted(encoded, key=lambda v: json.dumps(v, sort_keys=True))}
    if isinstance(value, dict):
        pairs = [(_encode_stable(k), _encode_stable(v)) for k, v in value.items()]
        pairs.sort(key=lambda kv: json.dumps(kv[0], sort_keys=True))
        return {"__dict__": [[k, v] for k, v in pairs]}
    raise _UnstableValue(
        f"value of type {type(value).__name__!r} has no stable encoding"
    )


def _stable_token(value: object) -> str | None:
    """Return a canonical string token for ``value``, or None if the value
    cannot be stably encoded."""
    try:
        encoded = _encode_stable(value)
        return json.dumps(
            encoded, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
    except (_UnstableValue, TypeError, ValueError):
        return None


def _relative_path(item: Item, config: Config) -> str:
    """Return the item's path relative to the rootdir, with POSIX
    separators, so manifests are comparable across machines."""
    path = Path(str(item.path))
    try:
        return path.relative_to(config.rootpath).as_posix()
    except ValueError:
        pass
    try:
        return Path(os.path.relpath(path, config.rootpath)).as_posix()
    except ValueError:
        # Different drives on Windows: fall back to the absolute path.
        return path.as_posix()


def _original_name(item: Item) -> str:
    """Return the test name without the parametrization suffix."""
    originalname = getattr(item, "originalname", None)
    if isinstance(originalname, str) and originalname:
        return originalname
    return str(item.name).split("[", 1)[0]


def _identity_string(
    relpath: str, originalname: str, params: list[_ParamIdentity]
) -> str:
    inner = ",".join(f"{p.name}={p.token}" for p in params)
    return f"{relpath}::{originalname}[{inner}]"
