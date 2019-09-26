.. _changelog:

Changelog
=========

`Unreleased`_
-------------

Added
~~~~~

- Support for ``x-nullable`` extension `#45`_

`0.7.0`_ - 2019-09-26
---------------------

Added
~~~~~

- Support for ``cookie`` parameter in OpenAPI 3.0 schemas. `#21`_
- Support for ``formData`` parameter in Swagger 2.0 schemas. `#6`_
- Test executor. `#28`_

Fixed
~~~~~

- Using ``hypothesis.settings`` decorator with test functions created from ``from_pytest_fixture`` loader. `#69`_

`0.6.0`_ - 2019-09-24
---------------------

Added
~~~~~

- Parametrizing tests from a pytest fixture via ``pytest-subtests``. `#58`_

Changed
~~~~~~~

- Rename module ``readers`` to ``loaders``.
- Rename ``parametrize`` parameters. ``filter_endpoint`` to ``endpoint`` and ``filter_method`` to ``method``.

Removed
~~~~~~~

- Substring match for method / endpoint filters. To avoid clashing with escaped chars in endpoints keys in schemas.

`0.5.0`_ - 2019-09-16
---------------------

Added
~~~~~

- Generating explicit examples from schema. `#17`_

Changed
~~~~~~~

- Schemas are loaded eagerly from now on. Using ``schemathesis.from_uri`` implies network calls.

Deprecated
~~~~~~~~~~

- Using ``Parametrizer.from_{path,uri}`` is deprecated, use ``schemathesis.from_{path,uri}`` instead

Fixed
~~~~~

- Body resolving during test collection. `#55`_

`0.4.1`_ - 2019-09-11
---------------------

Fixed
~~~~~

- Possibly unhandled exception during ``hasattr`` check in ``is_schemathesis_test``.

`0.4.0`_ - 2019-09-10
---------------------

Fixed
~~~~~

- Resolving all inner references in objects. `#34`_

Changed
~~~~~~~

- ``jsonschema.RefResolver`` is now used for reference resolving. `#35`_

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

.. _Unreleased: https://github.com/kiwicom/schemathesis/compare/v0.7.0...HEAD
.. _0.7.0: https://github.com/kiwicom/schemathesis/compare/v0.6.0...v0.7.0
.. _0.6.0: https://github.com/kiwicom/schemathesis/compare/v0.5.0...v0.6.0
.. _0.5.0: https://github.com/kiwicom/schemathesis/compare/v0.4.1...v0.5.0
.. _0.4.1: https://github.com/kiwicom/schemathesis/compare/v0.4.0...v0.4.1
.. _0.4.0: https://github.com/kiwicom/schemathesis/compare/v0.3.0...v0.4.0
.. _0.3.0: https://github.com/kiwicom/schemathesis/compare/v0.2.0...v0.3.0
.. _0.2.0: https://github.com/kiwicom/schemathesis/compare/v0.1.0...v0.2.0

.. _#69: https://github.com/kiwicom/schemathesis/issues/69
.. _#58: https://github.com/kiwicom/schemathesis/issues/58
.. _#55: https://github.com/kiwicom/schemathesis/issues/55
.. _#45: https://github.com/kiwicom/schemathesis/issues/45
.. _#35: https://github.com/kiwicom/schemathesis/issues/35
.. _#34: https://github.com/kiwicom/schemathesis/issues/34
.. _#28: https://github.com/kiwicom/schemathesis/issues/28
.. _#24: https://github.com/kiwicom/schemathesis/issues/24
.. _#21: https://github.com/kiwicom/schemathesis/issues/21
.. _#18: https://github.com/kiwicom/schemathesis/issues/18
.. _#17: https://github.com/kiwicom/schemathesis/issues/17
.. _#16: https://github.com/kiwicom/schemathesis/issues/16
.. _#10: https://github.com/kiwicom/schemathesis/issues/10
.. _#7: https://github.com/kiwicom/schemathesis/issues/7
.. _#6: https://github.com/kiwicom/schemathesis/issues/6
