GraphQL
=======

Schemathesis provides capabilities for testing GraphQL-based applications.
The current support is limited to Python tests - CLI support is in the works.

Usage
~~~~~

.. code:: python

    import schemathesis

    schema = schemathesis.graphql.from_url(
        "https://bahnql.herokuapp.com/graphql"
    )

    @schema.parametrize()
    @settings(deadline=None)
    def test(case):
        response = case.call()
        case.validate_response(response)

This test will load GraphQL schema from ``https://bahnql.herokuapp.com/graphql``, generate queries for it, send them to the server, and verify responses.

Limitations
~~~~~~~~~~~

Current GraphQL support does **NOT** include the following:

- CLI integration;
- Custom scalar types as required arguments (it will produce an error);
- Mutations;
- Filters;
