.. _changelog:

Changelog
=========

`Unreleased`_
-------------

`0.3.0`_ - 2019-09-06
---------------------

Added
~~~~~

- ``Parametrizer.from_uri`` method to construct parametrizer instances from URIs. `#24`_

Removed
~~~~~~~

- Possibility to use ``Parametrizer.parametrize`` and custom ``Parametrizer`` kwargs for passing config options
  to ``hypothesis.settings``. Use ``hypothesis.settings`` decorators on tests instead.

`0.2.0`_ - 2019-09-05
---------------------

Added
~~~~~

- Open API 3.0 support. `#10`_
- "header" parameters. `#7`_

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

.. _Unreleased: https://github.com/kiwicom/schemathesis/compare/v0.3.0...HEAD
.. _0.3.0: https://github.com/kiwicom/schemathesis/compare/v0.2.0...v0.3.0
.. _0.2.0: https://github.com/kiwicom/schemathesis/compare/v0.1.0...v0.2.0

.. _#24: https://github.com/kiwicom/schemathesis/issues/24
.. _#18: https://github.com/kiwicom/schemathesis/issues/18
.. _#16: https://github.com/kiwicom/schemathesis/issues/16
.. _#10: https://github.com/kiwicom/schemathesis/issues/10
.. _#7: https://github.com/kiwicom/schemathesis/issues/7
