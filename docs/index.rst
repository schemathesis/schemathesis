Schemathesis: Property-based testing for API schemas
====================================================

Schemathesis is a modern API testing tool for web applications built with Open API and GraphQL specifications.

It reads the application schema and generates test cases, which will ensure that your application is compliant with its schema.

The application under test could be written in any language; the only thing you need is a valid API schema in a supported format.

Simple to use and yet powerful to uncover hard-to-find errors thanks to the property-based testing approach backed by state-of-the-art `Hypothesis <http://hypothesis.works/>`_ library.

You can use Schemathesis in the command line:

.. code:: bash

  schemathesis run http://example.com/swagger.json

Or in your Python tests:

.. code-block:: python

    import schemathesis

    schema = schemathesis.from_uri("http://example.com/swagger.json")

    @schema.parametrize()
    def test_api(case):
        case.call_and_validate()

Features
--------

 - Content-Type, schema, and status code conformance checks for Open API;
 - Testing of explicit examples from the input schema;
 - Stateful testing via Open API links;
 - Concurrent test execution;
 - Targeted testing;
 - Storing and replaying network requests;
 - Built-in ASGI / WSGI application support;
 - Ready-to-go Docker image;
 - Configurable with user-defined checks, string formats, hooks, and targets.

User's Guide
------------

.. toctree::
   :maxdepth: 2

   introduction
   cli
   python
   compatibility
   examples
   stateful
   graphql
   targeted
   extending

Additional notes
----------------

.. toctree::
   :maxdepth: 2

   faq
   changelog
