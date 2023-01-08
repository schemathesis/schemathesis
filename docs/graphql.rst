GraphQL
=======

Schemathesis provides capabilities for testing GraphQL-based applications:

- Generating conforming queries & mutations
- Using default values in generated queries
- Customizing scalars generation

Usage
~~~~~

This test will load GraphQL schema from ``https://bahnql.herokuapp.com/graphql``, generate queries and mutations for it,
send them to the server, and verify that responses are not 5xx.

.. code:: text

    st run --hypothesis-deadline=None https://bahnql.herokuapp.com/graphql

Or in Python tests:

.. code:: python

    import schemathesis
    from hypothesis import settings

    schema = schemathesis.graphql.from_url("https://bahnql.herokuapp.com/graphql")


    @schema.parametrize()
    @settings(deadline=None)
    def test(case):
        case.call_and_validate()

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

At the moment it is not possible to select API operations to test.
