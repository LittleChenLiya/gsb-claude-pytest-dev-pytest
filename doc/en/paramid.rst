Param ID Export
===============

The ``--export-param-ids=PATH`` command-line option exports a list of stable
identities for all collected parameterized test cases after collection
completes. The identities can be used to compare and select test cases across
machines.

The export is read-only: it does not modify the collection order and does not
execute any test bodies.

Identity Format
---------------

A stable identity is composed of the following parts, separated by ``::``:

.. code-block:: text

    <relative_source_path>::<original_test_name>[<param_name>=<explicit_id>,...]

Where:

- ``<relative_source_path>`` is the rootpath-relative filesystem path of the
  test module (e.g. ``test_foo.py``).
- ``<original_test_name>`` is the original test function name, without any
  parametrization decorations (e.g. ``test_func``).
- ``<param_name>`` is the name of a parameter (e.g. ``x``).
- ``<explicit_id>`` is the explicit ID provided by the user (e.g. ``small``).

For example, given the following test:

.. code-block:: python

    import pytest

    @pytest.mark.parametrize("x", [1, 2], ids=["small", "large"])
    def test_func(x):
        assert True

The exported identities would be:

.. code-block:: text

    STABLE	test_foo.py::test_func[x=small]	test_foo.py::test_func[small]
    STABLE	test_foo.py::test_func[x=large]	test_foo.py::test_func[large]

Stability
---------

An identity is **stable** when:

- The parameter has an explicit ID (provided via ``ids=`` or
  ``pytest.param(id=...)``), OR
- The parameter has no explicit ID, but its value can be stably encoded.

A value can be stably encoded if it is one of the following types:

- ``None``, ``bool``, ``int``, ``float``, ``str``, ``bytes``
- ``tuple`` or ``list`` of stably encodable values
- ``dict`` with stably encodable keys and values

A value **cannot** be stably encoded if it is a custom object whose repr may
contain memory addresses. In this case, the identity is marked as **unstable**.

.. warning::

    Hashes are never used to identify parameter values, as they are not
    reliably identifiable. If a value cannot be stably encoded, the identity
    is marked as unstable rather than using a hash.

For example, given the following test:

.. code-block:: python

    import pytest

    class MyObject:
        pass

    @pytest.mark.parametrize("x", [MyObject()])
    def test_func(x):
        assert True

The exported identity would be:

.. code-block:: text

    UNSTABLE	test_foo.py::test_func[x]	test_foo.py::test_func[x0]

Conflicts
---------

If two or more test cases have the same stable identity, the conflict is
reported in the export. The conflicting nodeids are listed in the export.

For example, given the following test:

.. code-block:: python

    import pytest

    @pytest.mark.parametrize("x", [1], ids=["small"])
    def test_func(x):
        assert True

    @pytest.mark.parametrize("y", [2], ids=["small"])
    def test_other(y):
        assert True

The exported identities would be:

.. code-block:: text

    CONFLICT	test_foo.py::test_func[x=small]	test_foo.py::test_func[small] <-> test_foo.py::test_other[small]

Export Format
-------------

The export is a line-based text format. Each line has the following format:

.. code-block:: text

    <status>\t<identity>\t<nodeid>

Where:

- ``<status>`` is one of ``STABLE``, ``UNSTABLE``, or ``CONFLICT``.
- ``<identity>`` is the stable identity of the test case.
- ``<nodeid>`` is the pytest nodeid of the test case. For ``CONFLICT`` lines,
  this contains both conflicting nodeids separated by ``<->``.

Lines starting with ``#`` are comments and describe the format.

Usage
-----

Export to a file:

.. code-block:: bash

    pytest --export-param-ids=param_ids.txt

Export to stdout:

.. code-block:: bash

    pytest --export-param-ids=-

See Also
--------

- :option:`--export-param-ids` command-line option
- :ref:`parametrize` documentation
