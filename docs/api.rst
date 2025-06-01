Public API reference
====================

Hooks
~~~~~

.. class:: schemathesis.BaseSchema
  :noindex:

  All functions above can be accessed via ``schema.hooks.<function-name>`` on a schema instance. Such calls will affect
  only tests generated from the schema instance. Additionally you can use the following:

  .. method:: schema.hooks.apply

    Register hook to run only on one test function.

    :param hook: A hook function.
    :param Optional[str] name: A hook name.

    .. code-block:: python

        def before_generate_query(context, strategy):
            ...


        @schema.hooks.apply(before_generate_query)
        @schema.parametrize()
        def test_api(case):
            ...

Schema
~~~~~~

.. autoclass:: schemathesis.BaseSchema()

  .. automethod:: parametrize
  .. automethod:: given
  .. automethod:: as_state_machine

.. autoclass:: schemathesis.models.APIOperation()

  :members:

  .. automethod:: validate_response
  .. automethod:: is_response_valid
  .. automethod:: make_case
  .. automethod:: as_strategy

Open API-specific API

.. autoclass:: schemathesis.specs.openapi.schemas.BaseOpenAPISchema()
  :noindex:

  .. automethod:: add_link
    :noindex:
