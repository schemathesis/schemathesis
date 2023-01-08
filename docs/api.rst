Public API reference
====================

Checks
~~~~~~

.. autofunction:: schemathesis.checks.register

Fixups
~~~~~~

**Available fixups**:

- fast_api
- utf8_bom

.. autofunction:: schemathesis.fixups.install
.. autofunction:: schemathesis.fixups.uninstall

Authentication
~~~~~~~~~~~~~~

.. automodule:: schemathesis.auth

.. autofunction:: schemathesis.auth.register

.. autoclass:: schemathesis.auth.AuthProvider
   :members:

.. autoclass:: schemathesis.auth.AuthContext
   :members:

Hooks
~~~~~

.. autoclass:: schemathesis.hooks.HookContext
   :members:

These functions affect Schemathesis behavior globally:

.. autofunction:: schemathesis.hooks.register
.. autofunction:: schemathesis.hooks.unregister
.. autofunction:: schemathesis.hooks.unregister_all

.. class:: schemathesis.schemas.BaseSchema
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

Serializers
~~~~~~~~~~~

.. autoclass:: schemathesis.serializers.SerializerContext
   :members:
.. autofunction:: schemathesis.serializers.register
.. autofunction:: schemathesis.serializers.unregister

Targeted testing
~~~~~~~~~~~~~~~~

.. autoclass:: schemathesis.targets.TargetContext
   :members:
.. autofunction:: schemathesis.targets.register

Custom strategies for Open API "format" keyword
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. autofunction:: schemathesis.openapi.format


Custom scalars for GraphQL
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. autofunction:: schemathesis.graphql.register_scalar

Loaders
~~~~~~~

.. autofunction:: schemathesis.from_aiohttp
.. autofunction:: schemathesis.from_asgi
.. autofunction:: schemathesis.from_dict
.. autofunction:: schemathesis.from_file
.. autofunction:: schemathesis.from_path
.. autofunction:: schemathesis.from_pytest_fixture
.. autofunction:: schemathesis.from_uri
.. autofunction:: schemathesis.from_wsgi
.. autofunction:: schemathesis.graphql.from_dict
.. autofunction:: schemathesis.graphql.from_url
.. autofunction:: schemathesis.graphql.from_wsgi

Schema
~~~~~~

.. autoclass:: schemathesis.schemas.BaseSchema()

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
