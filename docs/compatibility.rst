.. _compatibility:

Compatibility
=============

By default, Schemathesis is strict on Open API spec interpretation, but other 3rd-party tools often are more flexible
and not always comply with the spec.

FastAPI
-------

`FastAPI <https://github.com/tiangolo/fastapi>`_ uses `pydantic <https://github.com/samuelcolvin/pydantic>`_ for JSON Schema
generation, and it produces Draft 7 compatible schemas. But Open API 2 / 3.0.x use earlier versions of JSON Schema (Draft 4 and Wright Draft 00 respectively), which leads
to incompatibilities when Schemathesis parses input schema.

It is a `known issue <https://github.com/tiangolo/fastapi/issues/240>`_ on the FastAPI side,
and Schemathesis provides a way to handle such schemas. The idea is to convert Draft 7 keywords syntax to Draft 4.

To use it, you need to add this code before you load your schema with Schemathesis:

.. code:: python

    import schemathesis

    # will install all available compatibility fixups.
    schemathesis.fixups.install()
    # You can provide a list of fixup names as the first argument
    # schemathesis.fixups.install(["fastapi"])

If you use the Command Line Interface, then you can utilize the ``--fixups=all`` option.
