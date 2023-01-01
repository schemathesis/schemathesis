Schemathesis: Property-based testing for API schemas
====================================================

Schemathesis is a specification-centric API testing tool for Open API and GraphQL-based applications.

It reads the application schema and generates test cases, which will ensure that your application is compliant with its schema and never crashes.

The application under test could be written in any language; the only thing you need is a valid API schema in a supported format.

Simple to use and yet powerful to uncover hard-to-find errors thanks to the property-based testing approach backed by state-of-the-art `Hypothesis <http://hypothesis.works/>`_ library.

You can use Schemathesis in the command line directly:

.. code:: bash

  st run https://example.schemathesis.io/openapi.json

Or via Docker:

.. code:: bash

  docker run schemathesis/schemathesis:stable run https://example.schemathesis.io/openapi.json

Or in your Python tests:

.. code-block:: python

    import schemathesis

    schema = schemathesis.from_uri("https://example.schemathesis.io/openapi.json")


    @schema.parametrize()
    def test_api(case):
        case.call_and_validate()

Both examples above will run hundreds of requests against the API under test and report all found failures and inconsistencies along with instructions to reproduce them.

.. note::

  You can also use our `SaaS <https://app.schemathesis.io/auth/sign-up/?utm_source=oss_docs&utm_content=index_note>`_ to run more comprehensive tests and visualise the outcomes!

Features
--------

- Open API: Schema conformance, explicit examples, stateful testing;
- GraphQL: queries generation;
- Multi-worker test execution;
- Storing and replaying tests;
- ASGI / WSGI support;
- Generated code samples (cURL, Python);
- Docker image;
- Customizable checks & test generation

.. note::

    🎉 Join our `Discord <https://discord.gg/R9ASRAmHnA>`_, we'd love to hear your feedback 🎉

User's Guide
------------

.. toctree::
   :maxdepth: 2

   introduction
   cli
   python
   continuous_integration
   service
   auth
   contrib
   stateful
   how
   compatibility
   examples
   graphql
   targeted
   extending

Support
-------

If you want to integrate Schemathesis into your company workflows or improve its effectiveness, feel free to reach out to `support@schemathesis.io`.

Schemathesis.io also runs workshops about effective API testing. `Signup here <https://forms.gle/epkovRdQNMCYh2Ax8>`_

Resources
---------

- `Deriving Semantics-Aware Fuzzers from Web API Schemas <https://arxiv.org/abs/2112.10328>`_ by **Zac-HD** and **@Stranger6667**
- `An article <https://dygalo.dev/blog/schemathesis-property-based-testing-for-api-schemas/>`_ about Schemathesis by **@Stranger6667**
- `Effective API schemas testing <https://youtu.be/VVLZ25JgjD4>`_ from DevConf.cz by **@Stranger6667**
- `How to use Schemathesis to test Flask API in GitHub Actions <https://notes.lina-is-here.com/2022/08/04/schemathesis-docker-compose.html>`_ by **@lina-is-here**
- `A video <https://www.youtube.com/watch?v=9FHRwrv-xuQ>`_ from EuroPython 2020 by **@hultner**
- `Schemathesis tutorial <https://appdev.consulting.redhat.com/tracks/contract-first/automated-testing-with-schemathesis.html>`_  with an accompanying `video <https://www.youtube.com/watch?v=4r7OC-lBKMg>`_ by Red Hat
- `Using Hypothesis and Schemathesis to Test FastAPI <https://testdriven.io/blog/fastapi-hypothesis/>`_ by **@amalshaji**

Additional notes
----------------

.. toctree::
   :maxdepth: 2

   recipes
   api
   faq
   changelog
