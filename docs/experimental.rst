Experimental features
=====================

Introduction
------------

This section provides an overview of experimental features in Schemathesis - features that are under development and available for testing, but not yet considered stable.

This section provides an overview of experimental features in Schemathesis.
These are features that are under development, available for user testing but not yet stable.
Experimental features offer a glimpse into upcoming functionalities and enhancements, providing users an opportunity to try out and contribute feedback to shape their final form.

.. note::

   Experimental features can change or be removed in any minor version release.

Enabling Experimental Features
------------------------------

Schemathesis provides a few ways to enable experimental features: via the CLI, Python tests, and environment variables.

.. _experimental-cli:

In CLI
~~~~~~

To enable an experimental feature via the CLI, use the ``--experimental`` option.

For example, to enable experimental support for OpenAPI 3.1:

.. code-block:: bash

   st run https://example.schemathesis.io/openapi.json --experimental=openapi-3.1

.. _experimental-python:

In Python Tests
~~~~~~~~~~~~~~~

To enable experimental features for your Schemathesis tests in Python, use the ``enable`` method on the desired experimental feature. It's a good idea to put the code to enable the feature in a place that runs before your tests start. For those using ``pytest``, putting this code in a ``conftest.py`` file at the root of your test directory would work well.

.. code-block:: python

    import schemathesis

    # Globally enable OpenAPI 3.1 experimental feature
    schemathesis.experimental.OPEN_API_3_1.enable()

Executing the above code will ensure that Schemathesis will utilize the enabled experimental features in all tests run afterwards.

.. note::

    This action will apply globally, affecting all your tests. Use with caution.

Using Environment Variables
~~~~~~~~~~~~~~~~~~~~~~~~~~~

You can also enable experimental features through environment variables. This is particularly useful for CI/CD pipelines or when you don't have direct control over CLI arguments or test code.

For example, to enable experimental support for OpenAPI 3.1:

.. code-block:: bash

    export SCHEMATHESIS_EXPERIMENTAL_OPENAPI_3_1=true

This will enable the OpenAPI 3.1 experimental feature for any Schemathesis runs in the same environment.

Current Experimental Features
-----------------------------

.. _experimental-openapi-31:

Open API 3.1
~~~~~~~~~~~~

Provides partial support for OpenAPI 3.1. This includes compatible JSON Schema validation for API responses.
Note that data generation is still compatible only with OpenAPI 3.0.

Enabling this feature also automatically enables UUID format support.

.. _openapi-31-cli:

In CLI
~~~~~~

.. code-block:: bash

   st run https://example.schemathesis.io/openapi.json --experimental=openapi-3.1

.. _openapi-31-python:

In Python Tests
~~~~~~~~~~~~~~~

.. code-block:: python

    import schemathesis

    # Globally enable OpenAPI 3.1 experimental feature
    schemathesis.experimental.OPEN_API_3_1.enable()

.. _openapi-31-env-vars:

Using Environment Variables
~~~~~~~~~~~~~~~~~~~~~~~~~~~

To enable OpenAPI 3.1 support via environment variables, you can set:

.. code-block:: bash

    export SCHEMATHESIS_EXPERIMENTAL_OPENAPI_3_1=true

For more details, join the `GitHub Discussion #1822 <https://github.com/schemathesis/schemathesis/discussions/1822>`_.

.. _coverage-phase:

Coverage phase
~~~~~~~~~~~~~~

Schemathesis primarily generates randomized tests; therefore, the outcomes can vary between test runs.

During the coverage phase, Schemathesis deterministically generates test cases to cover common "positive" and "negative" testing scenarios depending on the configuration. 
It covers common testing patterns such as:

- Boundary values: Minimum, maximum, and near-boundary values for numeric types and string lengths.
- Data types: Generating values for different data types specified in the schema.
- Constraints: Testing against schema constraints like `required` fields, `pattern`, `format`, and `uniqueItems`.

Additionally, it uses examples in the API schema as the source of "positive" values.

In CLI
~~~~~~

.. code-block:: bash

    st run https://example.schemathesis.io/openapi.json --experimental=coverage-phase

Using Environment Variables
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

    export SCHEMATHESIS_EXPERIMENTAL_COVERAGE_PHASE=true

For more details, join the `GitHub Discussion #2418 <https://github.com/schemathesis/schemathesis/discussions/2418>`_.

.. _stateful-test-runner:

New Stateful Test Runner
~~~~~~~~~~~~~~~~~~~~~~~~

This experimental feature enables a more effective way to run stateful tests in the CLI, along with a new display style. 
It leverages state machines, which were previously available in the ``pytest`` integration, to generate more diverse test scenarios. 
By default, state machines are executed until no new failures are found in a single iteration.

.. important::

   The new test runner does not yet support all configuration options available in the old one.

In CLI
~~~~~~

.. code-block:: bash

    st run https://example.schemathesis.io/openapi.json --experimental=stateful-test-runner

Using Environment Variables
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

    export SCHEMATHESIS_EXPERIMENTAL_STATEFUL_TEST_RUNNER=true

For more details, join the `GitHub Discussion #2262 <https://github.com/schemathesis/schemathesis/discussions/2262>`_.

Schema Analysis
~~~~~~~~~~~~~~~

Enables integration with Schemathesis.io to optimize test case generation for better bug detection.
This feature infers appropriate data generation strategies, supports uncommon media types, adjusts schemas for faster data generation, and detects the web server to generate more targeted test data. 

.. _schema-analysis-cli:

In CLI
~~~~~~

.. code-block:: bash

  st run https://example.schemathesis.io/openapi.json --experimental=schema-analysis

.. _schema-analysis-env-vars:

Using Environment Variables
~~~~~~~~~~~~~~~~~~~~~~~~~~~

To enable schema analysis support via environment variables, you can set:

.. code-block:: bash

    export SCHEMATHESIS_EXPERIMENTAL_SCHEMA_ANALYSIS=true

For more details, join the `GitHub Discussion #2056 <https://github.com/schemathesis/schemathesis/discussions/2056>`_.

Stabilization of Experimental Features
--------------------------------------

Criteria for moving a feature from experimental to stable status include:

- Full coverage of planned functionality
- API design stability, assessed through user feedback and internal review

Providing Feedback
------------------

Feedback is crucial for the development and stabilization of experimental features. We encourage you to share your thoughts via `GitHub Discussions <https://github.com/schemathesis/schemathesis/discussions>`_

.. note::

   When you use an experimental feature, a notice will appear in your test output, providing a link to the corresponding GitHub discussion where you can leave feedback.
