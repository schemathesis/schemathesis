.. _changelog:

Changelog
=========

`Unreleased`_
-------------

Added
~~~~~

- Open API 3.0 support. `#10`_

Changed
~~~~~~~

- Handle errors during collection / executions as failures.
- Use ``re.search`` for pattern matching in ``filter_method``/``filter_endpoint`` instead of ``fnmatch``. `#18`_
- ``Case.body`` contains properties from the target schema, without extra level of nesting.

Fixed
~~~~~

- ``KeyError`` on collection when "basePath" is absent. `#16`_

0.1.0 - 2019-06-28
------------------

- Initial public release

.. _Unreleased: https://github.com/kiwicom/schemathesis/compare/v0.1.0...HEAD

.. _#18: https://github.com/kiwicom/schemathesis/issues/18
.. _#16: https://github.com/kiwicom/schemathesis/issues/16
.. _#10: https://github.com/kiwicom/schemathesis/issues/10
