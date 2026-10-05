.. _mark:

How to mark test functions with attributes
===========================================

By using the ``pytest.mark`` helper you can easily set
metadata on your test functions. You can find the full list of builtin markers
in the :ref:`API Reference<marks ref>`. Or you can list all the markers, including
builtin and custom, using the CLI - :code:`pytest --markers`.

Here are some of the builtin markers:

* :ref:`usefixtures <usefixtures>` - use fixtures on a test function or class
* :ref:`filterwarnings <filterwarnings>` - filter certain warnings of a test function
* :ref:`skip <skip>` - always skip a test function
* :ref:`skipif <skipif>` - skip a test function if a certain condition is met
* :ref:`xfail <xfail>` - produce an "expected failure" outcome if a certain
  condition is met
* :ref:`parametrize <parametrizemark>` - perform multiple calls
  to the same test function.

It's easy to create custom markers or to apply markers
to whole test classes or modules. Those markers can be used by plugins, and also
are commonly used to :ref:`select tests <mark run>` on the command-line with the :option:`-m` option.

See :ref:`mark examples` for examples which also serve as documentation.

.. note::

    Marks can only be applied to tests, having no effect on
    :ref:`fixtures <fixtures>`.


Registering marks
-----------------

You can register custom marks in your configuration file like this:

.. tab:: toml

    .. code-block:: toml

        [pytest]
        markers = [
            "slow: marks tests as slow (deselect with '-m \"not slow\"')",
            "serial",
        ]

.. tab:: ini

    .. code-block:: ini

        [pytest]
        markers =
            slow: marks tests as slow (deselect with '-m "not slow"')
            serial

Note that everything past the ``:`` after the mark name is an optional description.

Alternatively, you can register new markers programmatically in a
:ref:`pytest_configure <initialization-hooks>` hook:

.. code-block:: python

    def pytest_configure(config):
        config.addinivalue_line(
            "markers", "env(name): mark test to run only on named environment"
        )


Registered marks appear in pytest's help text and do not emit warnings (see the next section). It
is recommended that third-party plugins always :ref:`register their markers <registering-markers>`.

.. _unknown-marks:

Raising errors on unknown marks
-------------------------------

Unregistered marks applied with the ``@pytest.mark.name_of_the_mark`` decorator
will always emit a warning in order to avoid silently doing something
surprising due to mistyped names. As described in the previous section, you can disable
the warning for custom marks by registering them in your configuration file or
using a custom ``pytest_configure`` hook.

When the :confval:`strict_markers` configuration option is set, any unknown marks applied
with the ``@pytest.mark.name_of_the_mark`` decorator will trigger an error. You can
enforce this validation in your project by setting :confval:`strict_markers` in your configuration:

.. tab:: toml

    .. code-block:: toml

        [pytest]
        addopts = ["--strict-markers"]
        markers = [
            "slow: marks tests as slow (deselect with '-m \"not slow\"')",
            "serial",
        ]

.. tab:: ini

    .. code-block:: ini

        [pytest]
        strict_markers = true
        markers =
            slow: marks tests as slow (deselect with '-m "not slow"')
            serial

.. _explain-marks:

Explaining effective marks
--------------------------

Marks can be applied at many levels (module, class, parameter set, test
function) and the same mark name may be applied more than once, which can make
it hard to tell which occurrence actually takes effect. The
:option:`--explain-marks` command-line option prints a read-only report right
after collection which explains, for every collected item, each effective
marker occurrence with its origin and its real priority:

.. code-block:: bash

    pytest --explain-marks --collect-only

For example, given:

.. code-block:: python

    import pytest

    pytestmark = pytest.mark.slow


    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(1, marks=pytest.mark.slow, id="marked"),
            pytest.param(2, id="plain"),
        ],
    )
    def test_param(value):
        pass

the report looks like::

    mark explanation report (read-only: items are not modified, no fixtures are triggered):
      item #0 test_module.py::test_param[marked]
        mark #0 parametrize('value', [...]) [source=function, owner=test_module.py::test_param[marked]] (effective, registered)
        mark #1 slow [source=parametrize, owner=test_module.py::test_param[marked]] (effective, registered)
        mark #2 slow [source=module, owner=test_module.py] (shadowed, registered)
      item #1 test_module.py::test_param[plain]
        mark #0 parametrize('value', [...]) [source=function, owner=test_module.py::test_param[plain]] (effective, registered)
        mark #1 slow [source=module, owner=test_module.py] (effective, registered)
      2 of 2 collected items carry markers

How to read the report:

* ``item #N``/``mark #N`` are stable sequence numbers (collection order and
  closest-first priority order), so reports of repeated runs are directly
  comparable.
* ``source`` tells where the mark comes from: ``module``, ``class``,
  ``parametrize`` or ``function``. Each parametrized item keeps its own
  parameter-level marks -- marks are never inherited from other parameter
  sets (``test_param[plain]`` above has no ``parametrize``-sourced ``slow``).
* Every occurrence is listed individually: same-name marks are never
  collapsed into a single value, and duplicates keep their original order.
  The first occurrence of a name is the ``effective`` one (the mark returned
  by :meth:`Node.get_closest_marker <_pytest.nodes.Node.get_closest_marker>`);
  later duplicates are ``shadowed``.
* Marks which are not registered (builtin, plugin-provided or listed in the
  :confval:`markers` configuration) are flagged as ``UNREGISTERED`` and are
  also listed in a dedicated diagnostics section at the end of the report.

The report is strictly observational: it never modifies items or keywords and
never triggers fixtures, so it is safe to combine with
:option:`--collect-only`.
