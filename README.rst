Schemathesis
============

|Build| |Coverage| |Version| |Python versions| |License|

Schemathesis is a tool for testing your web applications built with Open API / Swagger specifications.

It reads the application schema and generates test cases which will ensure that your application is compliant with its schema.

The application under test could be written in any language, the only thing you need is a valid API schema in a supported format.

**Supported specification versions**:

- Swagger 2.0
- Open API 3.0.x

More API specifications will be added in the future.

Built with:

- `hypothesis`_

- `hypothesis_jsonschema`_

- `pytest`_

Inspired by wonderful `swagger-conformance <https://github.com/olipratt/swagger-conformance>`_ project.

Installation
------------

To install Schemathesis via ``pip`` run the following command:

.. code:: bash

    pip install schemathesis

Optionally you could install ``requests`` for convenient HTTP calls.

Gitter: https://gitter.im/kiwicom/schemathesis

Usage
-----

To examine your application with Schemathesis you need to:

- Setup & run your application, so it is accessible via the network;
- Write a couple of tests in Python;
- Run the tests via ``pytest``.

Suppose you have your application running on ``http://0.0.0.0:8080`` and its
schema is available at ``http://0.0.0.0:8080/swagger.json``.

A basic test, that will verify that any data, that fit into the schema will not cause any internal server error could
look like this:

.. code:: python

    # test_api.py
    import requests
    import schemathesis

    BASE_URL = "http://0.0.0.0:8080"
    schema = schemathesis.from_uri(f"{BASE_URL}/swagger.json")

    @schema.parametrize()
    def test_no_server_errors(case):
        response = requests.request(
            case.method,
            f"{BASE_URL}{case.formatted_path}",
            headers=case.headers,
            params=case.query,
            json=case.body
        )
        assert response.status_code < 500


It consists of four main parts:

1. Schema preparation; ``schemathesis`` package provides multiple ways to initialize the schema - ``from_path``, ``from_dict``, ``from_uri``, ``from_file``.

2. Test parametrization; ``@schema.parametrize()`` generates separate tests for all endpoint/method combination available in the schema.

3. A network call to the running application; ``requests`` will do the job, for example.

4. Verifying a property you'd like to test; In the example, we verify that any app response will not indicate a server-side error (HTTP codes 5xx).

Run the tests:

.. code:: bash

    pytest test_api.py

**Other properties that could be tested**:

- Any call will be processed in <50 ms - you can verify the app performance;
- Any unauthorized access will end with 401 HTTP response code;

Each test function should have the ``case`` fixture, that represents a single test case.

Important ``Case`` attributes:

- ``method`` - HTTP method
- ``formatted_path`` - full endpoint path
- ``headers`` - HTTP headers
- ``query`` - query parameters
- ``body`` - request body

For each test, Schemathesis will generate a bunch of random inputs acceptable by the schema.
This data could be used to verify that your application works in the way as described in the schema or that schema describes expected behavior.

By default, there will be 100 test cases per endpoint/method combination.
To limit the number of examples you could use ``hypothesis.settings`` decorator on your test functions:

.. code:: python

    from hypothesis import settings

    @settings(max_examples=5)
    def test_something(client, case):
        ...

To narrow down the scope of the schemathesis tests it is possible to filter by method or endpoint:

.. code:: python

    @schema.parametrize(method="GET", endpoint="/pet")
    def test_no_server_errors(case):
        ...

The acceptable values are regexps or list of regexps (matched with ``re.search``).

Explicit examples
~~~~~~~~~~~~~~~~~

If the schema contains parameters examples, then they will be additionally included in the generated cases.

.. code:: yaml

    paths:
      get:
        parameters:
        - in: body
          name: body
          required: true
          schema: '#/definitions/Pet'

    definitions:
      Pet:
        additionalProperties: false
        example:
          name: Doggo
        properties:
          name:
            type: string
        required:
        - name
        type: object


With this Swagger schema example, there will be a case with body ``{"name": "Doggo"}``.  Examples handled with
``example`` decorator from Hypothesis, more info about its behavior is `here`_.

NOTE. Schemathesis supports only examples in ``parameters`` at the moment, examples of individual properties are not supported.

Lazy loading
~~~~~~~~~~~~

If you have a schema that is not available when the tests are collected, for example it is build with tools
like ``apispec`` and requires an application instance available, then you can parametrize the tests from a pytest fixture.

.. code:: python

    # test_api.py
    import schemathesis

    schema = schemathesis.from_pytest_fixture("fixture_name")

    @schema.parametrize()
    def test_api(case):
        ...

In this case the test body will be used as a sub-test via ``pytest-subtests`` library.

Documentation
-------------

For the full documentation, please see https://schemathesis.readthedocs.io/en/latest/ (WIP)

Or you can look at the ``docs/`` directory in the repository.

Contributing
------------

Any contribution in development, testing or any other area is highly appreciated and useful to the project.

Please, see the `CONTRIBUTING.rst`_ file for more details.

Python support
--------------

Schemathesis supports Python 3.6, 3.7 and 3.8.

License
-------

The code in this project is licensed under `MIT license`_.
By contributing to ``schemathesis``, you agree that your contributions
will be licensed under its MIT license.

.. |Build| image:: https://github.com/kiwicom/schemathesis/workflows/build/badge.svg
   :target: https://github.com/kiwicom/schemathesis/actions
.. |Coverage| image:: https://codecov.io/gh/kiwicom/schemathesis/branch/master/graph/badge.svg
   :target: https://codecov.io/gh/kiwicom/schemathesis
   :alt: codecov.io status for master branch
.. |Version| image:: https://img.shields.io/pypi/v/schemathesis.svg
   :target: https://pypi.org/project/schemathesis/
.. |Python versions| image:: https://img.shields.io/pypi/pyversions/schemathesis.svg
   :target: https://pypi.org/project/schemathesis/
.. |License| image:: https://img.shields.io/pypi/l/schemathesis.svg
   :target: https://opensource.org/licenses/MIT

.. _hypothesis: https://hypothesis.works/
.. _hypothesis_jsonschema: https://github.com/Zac-HD/hypothesis-jsonschema
.. _pytest: http://pytest.org/en/latest/
.. _here: https://hypothesis.readthedocs.io/en/latest/reproducing.html#providing-explicit-examples
.. _CONTRIBUTING.rst: https://github.com/kiwicom/schemathesis/blob/contributing/CONTRIBUTING.rst
.. _MIT license: https://opensource.org/licenses/MIT
