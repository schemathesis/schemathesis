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

Both examples above will run hundreds of requests against the API under test and report all found failures and inconsistencies along with instructions to reproduce them.

Features
--------

 - Content-Type, schema, headers, and status code conformance checks for Open API;
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
   stateful
   how
   compatibility
   examples
   graphql
   targeted
   extending

Commercial support
------------------

If you are interested in the effective integration of Schemathesis to your private project, you can `schedule an appointment <https://app.acuityscheduling.com/schedule.php?owner=21203869&appointmentType=18278663>`_ and I will help you do that.

Resources
---------

- `An article <https://dygalo.dev/blog/schemathesis-property-based-testing-for-api-schemas/>`_ about Schemathesis by **@Stranger6667**
- `A video <https://www.youtube.com/watch?v=9FHRwrv-xuQ>`_ from EuroPython 2020 by **@hultner**
- `Schemathesis tutorial <https://appdev.consulting.redhat.com/tracks/contract-first/automated-testing-with-schemathesis.html>`_  with an accompanying `video <https://www.youtube.com/watch?v=4r7OC-lBKMg>`_ by Red Hat
- `Using Hypothesis and Schemathesis to Test FastAPI <https://testdriven.io/blog/fastapi-hypothesis/>`_ by **@amalshaji**

Additional notes
----------------

.. toctree::
   :maxdepth: 2

   faq
   changelog
