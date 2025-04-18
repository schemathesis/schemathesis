Public API reference
====================

Checks
~~~~~~

.. autofunction:: schemathesis.check

Data Generation
~~~~~~~~~~~~~~~

.. autoclass:: schemathesis.GenerationConfig
   :members:


Authentication
~~~~~~~~~~~~~~

.. automodule:: schemathesis.auths

.. autofunction:: schemathesis.auth

.. autoclass:: schemathesis.auths.AuthProvider
   :members:

.. autoclass:: schemathesis.auths.AuthContext
   :members:

Hooks
~~~~~

.. autoclass:: schemathesis.HookContext
   :members:

These functions affect Schemathesis behavior globally:

.. autofunction:: schemathesis.hook
.. autofunction:: schemathesis.hooks.unregister
.. autofunction:: schemathesis.hooks.unregister_all

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

Serializers
~~~~~~~~~~~

.. autoclass:: schemathesis.serializers.SerializerContext
   :members:
.. autofunction:: schemathesis.serializer
.. autofunction:: schemathesis.serializers.unregister

Targeted testing
~~~~~~~~~~~~~~~~

.. autoclass:: schemathesis.targets.TargetContext
   :members:
.. autofunction:: schemathesis.target

Custom strategies for Open API "format" keyword
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. autofunction:: schemathesis.openapi.format

Custom strategies for Open API media types
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. autofunction:: schemathesis.openapi.media_type

Custom scalars for GraphQL
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. autofunction:: schemathesis.graphql.scalar

Loaders
~~~~~~~

.. autofunction:: schemathesis.openapi.from_asgi
.. autofunction:: schemathesis.openapi.from_dict
.. autofunction:: schemathesis.openapi.from_file
.. autofunction:: schemathesis.openapi.from_path
.. autofunction:: schemathesis.openapi.from_url
.. autofunction:: schemathesis.openapi.from_wsgi
.. autofunction:: schemathesis.graphql.from_path
.. autofunction:: schemathesis.graphql.from_dict
.. autofunction:: schemathesis.graphql.from_file
.. autofunction:: schemathesis.graphql.from_url
.. autofunction:: schemathesis.graphql.from_asgi
.. autofunction:: schemathesis.graphql.from_wsgi
.. autofunction:: schemathesis.pytest.from_fixture

Sanitizing Output
~~~~~~~~~~~~~~~~~

.. autoclass:: schemathesis.sanitization.Config()

  .. automethod:: with_keys_to_sanitize
  .. automethod:: without_keys_to_sanitize
  .. automethod:: with_sensitive_markers
  .. automethod:: without_sensitive_markers

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
