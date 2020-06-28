.. _graphql:

GraphQL
=======

Schemathesis provides basic capabilities for testing GraphQL-based applications.
The current support is limited to creating Hypothesis strategies for tests and crafting appropriate network requests.

**NOTE**: This area is in active development - more features will be added soon.

Usage
~~~~~

At the moment there is no direct integration with pytest and in order to generate GraphQL queries you need to manually
pass strategies to Hypothesis's ``given`` decorator.

.. code:: python

    import schemathesis

    schema = schemathesis.graphql.from_url(
        "https://bahnql.herokuapp.com/graphql"
    )

    @given(case=schema.query.as_strategy())
    @settings(deadline=None)
    def test(case):
        response = case.call()
        assert response.status_code < 500, response.content

This test will load GraphQL schema from ``https://bahnql.herokuapp.com/graphql`` and will generate queries for it.
In the test body, ``case`` instance provides only one method - ``call`` that will run a proper network request to the
application under test.

Limitations
~~~~~~~~~~~

Current GraphQL support does **NOT** include the following:

- Direct pytest integration;
- CLI integration;
- Custom scalar types support (it will produce an error);
- Mutations;
- Filters for fields under test;
- Hooks;
