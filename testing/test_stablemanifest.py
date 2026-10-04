"""Tests for the ``stablemanifest`` plugin (``--stable-manifest``).

Run only these tests with::

    python -m pytest testing/test_stablemanifest.py
"""

from __future__ import annotations

import json
from typing import Any

from _pytest.pytester import Pytester
import pytest


@pytest.fixture
def read_manifest(pytester: Pytester):
    """Run pytest with --stable-manifest and return the parsed manifest.

    The manifest is written to ``manifest.json`` inside the pytester
    directory; the RunResult is available as ``read_manifest.result``.
    """

    def run(*args: str) -> dict[str, Any]:
        result = pytester.runpytest("--stable-manifest=manifest.json", *args)
        run.result = result
        assert result.ret == 0
        text = pytester.path.joinpath("manifest.json").read_text(encoding="utf-8")
        return json.loads(text)

    run.result = None
    return run


def identities(manifest: dict[str, Any]) -> list:
    return [entry["identity"] for entry in manifest["entries"]]


def test_explicit_ids_stable_across_collections(
    pytester: Pytester, read_manifest
) -> None:
    """Two collections of the same suite with explicit ids ``small`` and
    ``large`` produce byte-identical manifests."""
    pytester.makepyfile(
        test_sizes="""
        import pytest

        @pytest.mark.parametrize("size", [1, 1000000], ids=["small", "large"])
        def test_sizes(size):
            pass
        """
    )
    first = read_manifest()
    first_text = pytester.path.joinpath("manifest.json").read_text(encoding="utf-8")
    second = read_manifest()
    second_text = pytester.path.joinpath("manifest.json").read_text(encoding="utf-8")

    assert first_text == second_text
    assert first == second
    assert identities(first) == [
        "test_sizes.py::test_sizes[size=small]",
        "test_sizes.py::test_sizes[size=large]",
    ]
    for entry in first["entries"]:
        assert entry["stable"] is True
        assert entry["params"][0]["source"] == "explicit-id"
    assert first["conflicts"] == []


def test_object_parameter_without_id_is_unstable(
    pytester: Pytester, read_manifest
) -> None:
    """A parameter object without an explicit id is reported as unstable;
    the plugin must not fake stability by hashing a repr() (which may
    contain a memory address)."""
    pytester.makepyfile(
        test_obj="""
        import pytest

        @pytest.mark.parametrize("obj", [object()])
        def test_obj(obj):
            pass
        """
    )
    manifest = read_manifest()
    (entry,) = manifest["entries"]
    assert entry["stable"] is False
    assert entry["identity"] is None
    (param,) = entry["params"]
    assert param["name"] == "obj"
    assert param["source"] == "unstable"
    assert param["token"] is None
    assert "'object'" in param["reason"]

    # No memory addresses (0x...) leak into the manifest.
    raw = pytester.path.joinpath("manifest.json").read_text(encoding="utf-8")
    assert "0x" not in raw
    read_manifest.result.stdout.fnmatch_lines(["*unstable identity: *test_obj*"])


def test_duplicate_ids_reported_as_conflict_not_overwritten(
    pytester: Pytester, read_manifest
) -> None:
    """Two parameters sharing one explicit id produce a conflict entry
    naming both nodeids; both entries stay in the manifest."""
    pytester.makepyfile(
        test_dup="""
        import pytest

        @pytest.mark.parametrize(
            "x", [pytest.param(1, id="dup"), pytest.param(2, id="dup")]
        )
        def test_dup(x):
            pass
        """
    )
    manifest = read_manifest()
    assert len(manifest["entries"]) == 2
    assert identities(manifest) == [
        "test_dup.py::test_dup[x=dup]",
        "test_dup.py::test_dup[x=dup]",
    ]
    (conflict,) = manifest["conflicts"]
    assert conflict["identity"] == "test_dup.py::test_dup[x=dup]"
    assert conflict["nodeids"] == [
        "test_dup.py::test_dup[dup0]",
        "test_dup.py::test_dup[dup1]",
    ]
    read_manifest.result.stdout.fnmatch_lines(["*CONFLICT*test_dup*dup0*dup1*"])


def test_test_bodies_are_not_executed_and_order_is_preserved(
    pytester: Pytester,
) -> None:
    """Exporting the manifest must not execute test bodies and must keep
    the original collection order."""
    pytester.makepyfile(
        test_order="""
        import pathlib
        import pytest

        @pytest.mark.parametrize("n", [3, 1, 2], ids=["c", "a", "b"])
        def test_first(n):
            pathlib.Path("executed.txt").write_text("boom")

        @pytest.mark.parametrize("n", [10, 20])
        def test_second(n):
            pathlib.Path("executed.txt").write_text("boom")
        """
    )
    result = pytester.runpytest("--stable-manifest=manifest.json")
    assert result.ret == 0
    result.stdout.fnmatch_lines(["*no tests ran*"])
    assert not pytester.path.joinpath("executed.txt").exists()

    manifest = json.loads(
        pytester.path.joinpath("manifest.json").read_text(encoding="utf-8")
    )
    assert [e["nodeid"] for e in manifest["entries"]] == [
        "test_order.py::test_first[c]",
        "test_order.py::test_first[a]",
        "test_order.py::test_first[b]",
        "test_order.py::test_second[10]",
        "test_order.py::test_second[20]",
    ]


def test_relative_paths_use_posix_separators(pytester: Pytester, read_manifest) -> None:
    """Paths in the manifest are relative to the rootdir and use forward
    slashes, so manifests compare equal across machines and OSes."""
    sub = pytester.path / "sub" / "pkg"
    sub.mkdir(parents=True)
    sub.joinpath("test_in_sub.py").write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('x', [1], ids=['one'])\n"
        "def test_x(x):\n"
        "    pass\n",
        encoding="utf-8",
    )
    manifest = read_manifest()
    (entry,) = manifest["entries"]
    assert entry["path"] == "sub/pkg/test_in_sub.py"
    assert "\\" not in entry["path"]
    assert entry["identity"] == "sub/pkg/test_in_sub.py::test_x[x=one]"


def test_plain_values_use_stable_value_encoding(
    pytester: Pytester, read_manifest
) -> None:
    """Parameters without explicit ids but with stably encodable values
    (int, str, tuple, ...) get a deterministic value token."""
    pytester.makepyfile(
        test_values="""
        import pytest

        @pytest.mark.parametrize("n", [1, 2])
        @pytest.mark.parametrize("pair", [("a", 1), ("b", 2)])
        def test_values(n, pair):
            pass
        """
    )
    manifest = read_manifest()
    assert manifest["conflicts"] == []
    assert identities(manifest) == [
        'test_values.py::test_values[pair={"__tuple__":["a",1]},n=1]',
        'test_values.py::test_values[pair={"__tuple__":["a",1]},n=2]',
        'test_values.py::test_values[pair={"__tuple__":["b",2]},n=1]',
        'test_values.py::test_values[pair={"__tuple__":["b",2]},n=2]',
    ]
    for entry in manifest["entries"]:
        assert entry["stable"] is True
        assert all(p["source"] == "value" for p in entry["params"])
    # Deterministic across collections.
    assert read_manifest() == manifest


def test_ids_callable_and_pytest_param_id_are_explicit(
    pytester: Pytester, read_manifest
) -> None:
    pytester.makepyfile(
        test_ids="""
        import pytest

        @pytest.mark.parametrize("item", [object()], ids=lambda v: "callable-id")
        def test_callable_ids(item):
            pass

        @pytest.mark.parametrize(
            "item", [pytest.param(object(), id="param-id")]
        )
        def test_param_id(item):
            pass
        """
    )
    manifest = read_manifest()
    assert identities(manifest) == [
        "test_ids.py::test_callable_ids[item=callable-id]",
        "test_ids.py::test_param_id[item=param-id]",
    ]
    assert all(e["stable"] for e in manifest["entries"])
    assert all(
        p["source"] == "explicit-id" for e in manifest["entries"] for p in e["params"]
    )


def test_non_parametrized_tests_are_omitted(pytester: Pytester, read_manifest) -> None:
    pytester.makepyfile(
        test_mix="""
        import pytest

        def test_plain():
            pass

        @pytest.mark.parametrize("x", [1], ids=["one"])
        def test_param(x):
            pass
        """
    )
    manifest = read_manifest()
    assert [e["nodeid"] for e in manifest["entries"]] == [
        "test_mix.py::test_param[one]"
    ]


def test_without_option_tests_run_normally(pytester: Pytester) -> None:
    """Without --stable-manifest nothing is exported and tests execute."""
    pytester.makepyfile(
        test_normal="""
        import pytest

        @pytest.mark.parametrize("x", [1, 2])
        def test_x(x):
            pass
        """
    )
    result = pytester.runpytest()
    assert result.ret == 0
    result.stdout.fnmatch_lines(["*2 passed*"])
    assert not pytester.path.joinpath("manifest.json").exists()


def test_plugin_can_be_disabled(pytester: Pytester) -> None:
    pytester.makepyfile("def test(): pass")
    result = pytester.runpytest("-p", "no:stablemanifest")
    assert result.ret == 0
    result.stdout.fnmatch_lines(["*1 passed*"])
