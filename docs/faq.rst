Frequently Asked Questions
==========================

This page answers some of the often asked questions about Schemathesis.

Usage & Configuration
---------------------

How many tests does Schemathesis execute for an API operation?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The total number of tests Schemathesis executes is influenced by the API schema's complexity, user-defined settings like ``--hypothesis-max-examples`` for the maximum tests generated, and the test generation phases (``explicit``, ``generate``, ``reuse``, and ``shrink``). 
The process is designed to optimize coverage within a reasonable test budget rather than aiming for exhaustive coverage. 
For detailed insights and customization options, refer to our :ref:`data generation docs <data-generation-overview>`.

What parts of the application is Schemathesis targeting during its tests?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

It depends. The test data that Schemathesis generates is random. Input validation is, therefore, more frequently examined than other parts.

Since Schemathesis generates data that fits the application's API schema, it can reach the app's business logic, but it depends on the architecture of each particular application.

Why are no examples generated in Schemathesis when using ``--hypothesis-phase=explicit``?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``--hypothesis-phase=explicit`` option is designed to test only the examples that are explicitly defined in the API schema.
It avoids generating new examples to maintain predictability and adhere strictly to the documented API behavior.

If you need random examples for API operations without explicit examples, consider using the ``--contrib-openapi-fill-missing-examples`` CLI option.

Should I always have my application running before starting the test suite?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For the ``pytest`` integration, there is ``schemathesis.pytest.from_fixture`` loader where you can postpone API schema loading
and start the test application as a part of your test setup. See more information in the :doc:`../python` section.

How can I use database objects IDs in tests?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``case`` object that is injected in each test can be modified, assuming your URL template is ``/api/users/{user_id}`` then in tests, it can be done like this:

.. code:: python

    schema = ...  # Load the API schema here


    @schema.parametrize()
    def test_api(case):
        case.path_parameters["user_id"] = 42

Why Schemathesis generates uniform data for my API schema?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

There might be multiple reasons for that, but usually, this behavior occurs when the API schema is complex or deeply nested.
Please, refer to the ``Data generation`` section in the documentation for more info. If you think that it is not the case, feel
free to `open an issue <https://github.com/schemathesis/schemathesis/issues/new?assignees=Stranger6667&labels=Status%3A+Review+Needed%2C+Type%3A+Bug&template=bug_report.md&title=%5BBUG%5D>`_.

How different is ``--request-timeout`` from ``--hypothesis-deadline``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

These CLI parameters both represent some kind of limit for the duration of a certain part of a single test. However, each of them has a different scope.

``--hypothesis-deadline`` counts parts of a single test case execution, including waiting for the API response, and running all checks and relevant hooks for that single test case.

``--request-timeout`` is only relevant for waiting for the API response. If this duration is exceeded, the test is marked as a "Timeout".

Why Schemathesis reports "Flaky" errors?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When Schemathesis finds a failure, it tries to verify it by re-running the test again.
If the same failure is not reproduced, then Schemathesis concludes the test as "Flaky".

This situation usually happens, when the tested application state is not reset between tests.
Let's imagine that we have an API where the user can create "orders", then the "Flaky" situation might look like this:

1. Create order "A" -> 201 with payload that does not conform to the definition in the API schema;
2. Create order "A" again to verify the failure -> 409 with conformant payload.

With Python tests, you may want to write a context manager that cleans the application state between test runs as
`suggested <https://hypothesis.readthedocs.io/en/latest/healthchecks.html#hypothesis.HealthCheck.function_scoped_fixture>`_ by Hypothesis docs.

CLI reports flaky failures as regular failures with a special note about their flakiness. Cleaning the application state could be done via the :ref:`before_call <hooks_before_call>` hook.

Does Schemathesis support Open API discriminators? Schemathesis raises an "Unsatisfiable" error.
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``discriminator`` field does not affect data generation, and Schemathesis work directly with the underlying schemas.
Usually, the problem comes from using the ``oneOf`` keyword with very permissive sub-schemas.
For example:

.. code:: yaml

    discriminator:
      propertyName: objectType
    oneOf:
      - type: object
        required:
          - objectType
        properties:
          objectType:
            type: string
          foo:
            type: string
      - type: object
        required:
          - objectType
        properties:
          objectType:
            type: string
          bar:
            type: string

Here both schemas do not restrict their additional properties, and for this reason, any object that is valid for the first sub-schema is also valid for the second one, which
contradicts the definition of the ``oneOf`` keyword behavior, where the value should be valid against **exactly one** sub-schema.

To solve this problem, you can use ``anyOf`` or make your sub-schemas less permissive.

Schemathesis reports conformance issue for schemas with the ``oneOf`` keyword. Why?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``oneOf`` keyword is a tricky one and the validation results might look counterintuitive at first glance.
Let's take a look at an example:

.. code:: yaml

    paths:
      /pets:
        patch:
          requestBody:
            content:
              application/json:
                schema:
                  oneOf:
                    - $ref: '#/components/schemas/Cat'
                    - $ref: '#/components/schemas/Dog'
          responses:
            '200':
              description: Updated
    components:
      schemas:
        Dog:
          type: object
          properties:
            bark:
              type: boolean
            breed:
              type: string
              enum: [Dingo, Husky, Retriever, Shepherd]
        Cat:
          type: object
          properties:
            hunts:
              type: boolean
            age:
              type: integer

Here we have two possible payload options - ``Dog`` and ``Cat``. The following JSON object is valid against the ``Dog`` schema:

.. code:: json

    {
      "bark": true,
      "breed": "Dingo"
    }

Though, ``oneOf`` requires that the input should be valid against **exactly one** sub-schema!
At first glance it looks like the case, but it is **actually not**. It happens because the ``Cat`` schema does not restrict what properties should always be present and what should not.
If the input object does not have the ``hunts`` or ``age`` properties, then it will be validated as a ``Cat`` instance.
To prevent this situation you might use ``required`` and ``additionalProperties`` keywords:

.. code:: yaml

    components:
      schemas:
        Dog:
          type: object
          properties:
            bark:
              type: boolean
            breed:
              type: string
              enum: [Dingo, Husky, Retriever, Shepherd]
          required: [bark, breed]      # List all the required properties
          additionalProperties: false  # And forbid any others
        Cat:
          type: object
          properties:
            hunts:
              type: boolean
            age:
              type: integer
          required: [hunts, age]       # List all the required properties
          additionalProperties: false  # And forbid any others

By adding these keywords, any ``Cat`` instance will always require the ``hunts`` and ``age`` properties to be present.

As an alternative, you could use the ``anyOf`` keyword instead.

Why is Schemathesis slower on Windows when using ``localhost``?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When Schemathesis sends a request to ``http://localhost/``, it first attempts to use IPv6. This can cause delays if your server only supports IPv4.
This is especially problematic on Windows due to an unavoidable 1-second timeout for refused TCP connections, which the OS may retry up to three times.
On Linux, the connection fails immediately if refused, allowing a quick switch to IPv4.

**Solution**: To avoid this delay, simply use http://127.0.0.1/ instead of http://localhost/. This ensures that Schemathesis will use IPv4 directly.

Why can’t Schemathesis connect to my locally running application when run via Docker on MacOS?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The host has a changing IP address, or none if you have no network access. As a result, the Docker container cannot use ``localhost`` to reach the host machine.

**Solution**: Instead, use ``host.docker.internal`` as the hostname to allow Schemathesis to connect to services running on the host.

How to prevent Schemathesis from generating NULL bytes in strings?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

By default, Schemathesis generates ``NULL`` bytes for all strings in order to cover more possible edge cases.

**Solution**: To prevent Schemathesis from generating ``NULL`` bytes in strings, you need to set the ``allow_x00`` configuration to ``False``.

CLI:

.. code:: text

    $ st run --generation-allow-x00=false ...

Python:

.. code:: python

    import schemathesis
    from schemathesis import GenerationConfig

    schema = schemathesis.openapi.from_url(
        "https://example.schemathesis.io/openapi.json",
        generation_config=GenerationConfig(allow_x00=False),
    )

This adjustment ensures that Schemathesis does not include NULL bytes in strings for all your tests, making them compatible with systems that reject such inputs.

Working with API schemas
------------------------

How to disallow random field names in my schema?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

You need to add ``additionalProperties: false`` to the relevant object definition. But there is a caveat with emulating
inheritance with Open API via ``allOf``.

In this case, it is better to use YAML anchors to share schema parts; otherwise it will prevent valid data from passing the validation.
