.. _`stable-manifest`:
.. _`stable identity manifest`:


How to export stable identities of parametrized tests
=====================================================

.. versionadded:: 9.0

The builtin ``stablemanifest`` plugin adds the
``--stable-manifest=PATH`` command line option.  When given, pytest
collects the suite, writes a JSON manifest with a *stable identity* for
every parametrized test to ``PATH``, and exits **without running any
test body** (like ``--collect-only``).  Exporting the manifest never
changes the collection order.

The manifest is meant for **cross-machine comparison of test
selection**: two checkouts of the same test suite on different machines
must produce byte-identical manifests, so a diff of two manifests shows
exactly which parametrized cases were added, removed, or renamed.


Usage
-----

.. code-block:: bash

    pytest --stable-manifest=manifest.json

The manifest looks like this:

.. code-block:: json

    {
      "version": 1,
      "entries": [
        {
          "nodeid": "test_sizes.py::test_sizes[small]",
          "path": "test_sizes.py",
          "name": "test_sizes",
          "params": [
            {"name": "size", "source": "explicit-id", "token": "small"}
          ],
          "stable": true,
          "identity": "test_sizes.py::test_sizes[size=small]"
        }
      ],
      "conflicts": []
    }

``entries`` preserves the original collection order.  Only parametrized
items are listed.


Identity specification
----------------------

The identity of a parametrized test is built **only** from:

1. the test file path, relative to the rootdir, using POSIX (``/``)
   separators on every platform;
2. the original (unparametrized) test name;
3. the parameter names; and
4. for each parameter, either its **explicit ID** or a **stably
   encodable value**.

It has the form::

    <relative/path.py>::<test_name>[<param>=<token>,...]

Parameter tokens are resolved as follows, per parameter:

* **Explicit ID** (``source: "explicit-id"``): an ID given via
  ``pytest.param(..., id=...)``, an ``ids=[...]`` sequence, or an
  ``ids=callable`` that returned a non-``None`` value.  Explicit IDs
  always win over value encoding.
* **Stable value** (``source: "value"``): if there is no explicit ID,
  the value itself is used, but only when it can be encoded without
  machine-dependent data.  Supported values are ``None``, ``bool``,
  ``int``, ``float``, ``complex``, ``str``, ``bytes``, ``enum.Enum``
  members, and ``tuple``/``list``/``dict``/``set``/``frozenset``
  containers thereof.  The token is a canonical JSON encoding of the
  value (container keys are sorted, bytes are hex-encoded, enums are
  encoded as ``module.QualName.member``).
* **Unstable** (``source: "unstable"``): anything else -- most notably
  arbitrary objects, whose ``repr()`` may embed a memory address such as
  ``<object object at 0x7f...>``.  The entry gets ``"stable": false``,
  ``"identity": null`` and a ``reason`` explaining how to fix it (give
  the parameter an explicit ID).  The plugin **never** falls back to
  hashing a ``repr()``: an identity that cannot be determined reliably
  is reported as unstable instead of being faked.

Duplicate identities
--------------------

If two collected items end up with the same stable identity -- for
example because of duplicate explicit IDs -- the identity is reported
once in the top-level ``conflicts`` list together with **both**
nodeids::

    "conflicts": [
      {
        "identity": "test_dup.py::test_dup[x=dup]",
        "nodeids": ["test_dup.py::test_dup[dup0]", "test_dup.py::test_dup[dup1]"]
      }
    ]

Entries are never silently overwritten or dropped, and a ``CONFLICT``
line is also printed in the terminal summary.

.. note::

    This plugin is enabled by default, but can be disabled if needed:
    see :ref:`cmdunregister` (the internal name for this plugin is
    ``stablemanifest``).


Development
-----------

The plugin's own tests live in ``testing/test_stablemanifest.py`` and
use the :fixture:`pytester` fixture.  To run only the plugin tests:

.. code-block:: bash

    python -m pytest testing/test_stablemanifest.py
