Compatibility
=============

This section aims to help you resolve issues you may encounter due to the strict Open API specification adherence in Schemathesis and its interaction with other tools that might not be as strict.

.. _compatibility-fastapi:

Using FastAPI
-------------

`FastAPI <https://github.com/tiangolo/fastapi>`_ uses `pydantic <https://github.com/samuelcolvin/pydantic>`_ for JSON Schema generation.
Depending on your FastAPI version, you may need to adjust your Schemathesis setup:

For Recent FastAPI Versions
~~~~~~~~~~~~~~~~~~~~~~~~~~~

If you're using FastAPI versions that generate OpenAPI 3.1 schemas, activate Schemathesis' experimental support for OpenAPI 3.1.

**CLI**

.. code:: bash

    st run https://example.schemathesis.io/openapi.json --experimental=openapi-3.1

**Python**

.. code:: python

    import schemathesis

    # Globally enable OpenAPI 3.1 experimental feature
    schemathesis.experimental.OPEN_API_3_1.enable()

UTF-8 BOM
---------

Some web servers prefix JSON with a UTF-8 Byte Order Mark (BOM), which is not compliant with the JSON specification.
If your server does this, you can enable a fixup to remove it.

**CLI**

.. code:: bash

    st run https://example.schemathesis.io/openapi.json --fixups=utf8_bom

**Python**

.. code:: python

    import schemathesis

    schemathesis.fixups.utf8_bom.install()
