GraphQL
=======

Schemathesis provides capabilities for testing GraphQL-based applications:

- Generating conforming queries & mutations
- Using default values in generated queries
- Customizing scalars generation
- Validating responses based on the presence of the ``errors`` field

Usage
~~~~~

This test will load GraphQL schema from ``https://bahnql.herokuapp.com/graphql``, generate queries and mutations for it,
send them to the server, and verify that responses are not 5xx.

.. code:: text

    st run https://bahnql.herokuapp.com/graphql

Or in Python tests:

.. code:: python

    import schemathesis

    schema = schemathesis.graphql.from_url("https://bahnql.herokuapp.com/graphql")


    @schema.parametrize()
    def test(case):
        case.call_and_validate()

If you want to narrow the testing scope, you can use ``--include-name`` and ``--exclude-name`` options in CLI and the ``name`` argument for ``include`` and ``exclude`` methods in Python tests:

.. code:: text

    st run --include-name Query.getBookings https://bahnql.herokuapp.com/graphql

.. code:: python

    import schemathesis

    schema = schemathesis.graphql.from_url("https://bahnql.herokuapp.com/graphql")


    @schema.include(name="Query.getBookings").parametrize()
    def test(case):
        case.call_and_validate()

For GraphQL, the ``name`` attribute is a combination of the type and the field name, for example, ``Query.getBookings`` or ``Mutation.updateUser``.

Additionally, you can disable using ``null`` values for optional arguments via the ``--generation-graphql-allow-null=false`` CLI option.

Custom scalars
~~~~~~~~~~~~~~

Standard scalars work out of the box, for custom ones you need to pass custom strategies that generate proper AST nodes:

.. code:: python

    from hypothesis import strategies as st
    import schemathesis
    from schemathesis.graphql import nodes

    schemathesis.graphql.scalar("Date", st.dates().map(nodes.String))

Such a strategy generates valid dates as strings, for example:

.. code::

   { getByDate(created: "2000-01-01") }

To simplify AST node generation, use ``schemathesis.graphql.nodes`` to generate AST nodes of the desired type.
There are ready-to-use factories for common node types. They correspond to the following nodes in the ``graphql`` library:

- ``String`` -> ``graphql.StringValueNode``
- ``Float`` -> ``graphql.FloatValueNode``
- ``Int`` -> ``graphql.IntValueNode``
- ``Object`` -> ``graphql.ObjectValueNode``
- ``List`` -> ``graphql.ListValueNode``
- ``Boolean`` -> ``graphql.BooleanValueNode``
- ``Enum`` -> ``graphql.EnumValueNode``
- ``Null`` -> ``graphql.NullValueNode`` (a constant, not a function)

They exist because classes like ``graphql.StringValueNode`` can't be directly used in Hypothesis' ``map`` calls due to kwarg-only arguments.

Limitations
~~~~~~~~~~~

Schemathesis does not generate negative data for GraphQL schemas.
