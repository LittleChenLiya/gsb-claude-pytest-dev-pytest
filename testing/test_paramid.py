"""Tests for the --export-param-ids plugin."""

from __future__ import annotations

from pathlib import Path

from _pytest.pytester import Pytester


def test_export_param_ids_basic(pytester: Pytester) -> None:
    """Test that explicit IDs produce a stable identity list."""
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.parametrize("x", [1, 2], ids=["small", "large"])
        def test_func(x):
            assert True
        """
    )

    result = pytester.runpytest("--export-param-ids=-")
    assert result.ret == 0

    result.stdout.fnmatch_lines(
        [
            "*STABLE*test_func?x=small?*test_func?small?",
            "*STABLE*test_func?x=large?*test_func?large?",
        ]
    )


def test_export_param_ids_stable_across_collections(pytester: Pytester) -> None:
    """Test that two collections produce the same stable identity list."""
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.parametrize("x", [1, 2], ids=["small", "large"])
        def test_func(x):
            assert True
        """
    )

    result1 = pytester.runpytest("--export-param-ids=-")
    result2 = pytester.runpytest("--export-param-ids=-")

    assert result1.ret == 0
    assert result2.ret == 0

    lines1 = [line for line in result1.stdout.lines if line.startswith("STABLE")]
    lines2 = [line for line in result2.stdout.lines if line.startswith("STABLE")]

    assert lines1 == lines2


def test_export_param_ids_unstable_object(pytester: Pytester) -> None:
    """Test that an object parameter without an ID is marked as UNSTABLE."""
    pytester.makepyfile(
        """
        import pytest

        class MyObject:
            pass

        @pytest.mark.parametrize("x", [MyObject()])
        def test_func(x):
            assert True
        """
    )

    result = pytester.runpytest("--export-param-ids=-")
    assert result.ret == 0

    result.stdout.fnmatch_lines(
        [
            "*UNSTABLE*test_func?x?*test_func?x0?",
        ]
    )


def test_export_param_ids_conflict(pytester: Pytester) -> None:
    """Test that duplicate stable identities are reported as CONFLICT."""
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.parametrize("x", [1], ids=["small"])
        def test_func(x):
            assert True

        @pytest.mark.parametrize("y", [2], ids=["small"])
        def test_other(y):
            assert True
        """
    )

    result = pytester.runpytest("--export-param-ids=-")
    assert result.ret == 0

    result.stdout.fnmatch_lines(
        [
            "*CONFLICT*small*",
        ]
    )


def test_export_param_ids_does_not_change_collection_order(pytester: Pytester) -> None:
    """Test that the export does not change the collection order."""
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.parametrize("x", [1, 2, 3], ids=["a", "b", "c"])
        def test_func(x):
            assert True
        """
    )

    result = pytester.runpytest("--export-param-ids=-")
    assert result.ret == 0

    result.stdout.fnmatch_lines(
        [
            "*STABLE*test_func?x=a?*test_func?a?",
            "*STABLE*test_func?x=b?*test_func?b?",
            "*STABLE*test_func?x=c?*test_func?c?",
        ]
    )


def test_export_param_ids_to_file(pytester: Pytester, tmp_path: Path) -> None:
    """Test that the export can be written to a file."""
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.parametrize("x", [1, 2], ids=["small", "large"])
        def test_func(x):
            assert True
        """
    )

    export_file = tmp_path / "param_ids.txt"
    result = pytester.runpytest(f"--export-param-ids={export_file}")
    assert result.ret == 0

    content = export_file.read_text(encoding="utf-8")
    assert "STABLE" in content
    assert "test_func[x=small]" in content
    assert "test_func[x=large]" in content


def test_export_param_ids_relative_path(pytester: Pytester) -> None:
    """Test that the identity uses a relative source path."""
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.parametrize("x", [1, 2], ids=["small", "large"])
        def test_func(x):
            assert True
        """
    )

    result = pytester.runpytest("--export-param-ids=-")
    assert result.ret == 0

    result.stdout.fnmatch_lines(
        [
            "*STABLE*test_func?x=small?*",
        ]
    )

    for line in result.stdout.lines:
        if line.startswith("STABLE"):
            identity = line.split("\t")[1]
            assert not identity.startswith("/")
            assert not identity.startswith("D:\\")
            assert not identity.startswith("C:\\")


def test_export_param_ids_multiple_params(pytester: Pytester) -> None:
    """Test that multiple parameters are handled correctly."""
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.parametrize("x", [1], ids=["small"])
        @pytest.mark.parametrize("y", [2], ids=["large"])
        def test_func(x, y):
            assert True
        """
    )

    result = pytester.runpytest("--export-param-ids=-")
    assert result.ret == 0

    result.stdout.fnmatch_lines(
        [
            "*STABLE*test_func?x=small,y=large?*test_func?large-small?",
        ]
    )


def test_export_param_ids_auto_encoded_value(pytester: Pytester) -> None:
    """Test that values without explicit IDs are stably encoded."""
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.parametrize("x", [1, "hello", None])
        def test_func(x):
            assert True
        """
    )

    result = pytester.runpytest("--export-param-ids=-")
    assert result.ret == 0

    result.stdout.fnmatch_lines(
        [
            "*STABLE*test_func?x=1?*",
            "*STABLE*test_func?x='hello'?*",
            "*STABLE*test_func?x=None?*",
        ]
    )


def test_export_param_ids_no_option(pytester: Pytester) -> None:
    """Test that the plugin does nothing when the option is not provided."""
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.parametrize("x", [1, 2], ids=["small", "large"])
        def test_func(x):
            assert True
        """
    )

    result = pytester.runpytest()
    assert result.ret == 0

    for line in result.stdout.lines:
        assert "STABLE" not in line
        assert "UNSTABLE" not in line
        assert "CONFLICT" not in line
