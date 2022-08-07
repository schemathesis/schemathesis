Schemathesis
============

|Build| |Coverage| |Version| |Python versions| |Docs| |Chat| |License|

Schemathesis is a specification-centric API testing tool for Open API and GraphQL-based applications.

Why use Schemathesis?
---------------------

- **Application crashes**. Learn what payloads make your API crash, corrupt the database or hang forever.
- **Up-to-date API documentation**. Never worry that your API consumers will use an incorrect specification or outdated payload example.
- **Instant debugging**. Get a detailed failure report with a single cURL command to reproduce the problem immediately.

How does it work?
-----------------

Schemathesis reads the application schema and generates test cases, which will ensure that your application is compliant with its schema and never crashes.

The application under test could be written in any language; the only thing you need is a valid API schema in a supported format.

Read more about how it works in `our research paper <https://arxiv.org/abs/2112.10328>`_.

How do I start?
---------------

Schemathesis is available as a `service <https://schemathesis.io/?utm_source=github>`_, standalone CLI, or a Python library.

The service enables you to verify your API schema in a few clicks, CLI gives more control.
Schemathesis.io has a free tier, so you can combine the CLI flexibility with rich visuals by uploading your test results there.

If you use GitHub Actions, there is a native `GitHub app <https://github.com/apps/schemathesis>`_ that reports test results directly to your pull requests.

Features
--------

- Open API: Schema conformance, explicit examples, stateful testing;
- GraphQL: queries generation;
- Multi-worker test execution;
- Storing and replaying tests;
- ASGI / WSGI support;
- Generated code samples (cURL, Python);
- Docker image;
- Customizable checks & test generation;

CLI installation
----------------

To install Schemathesis via ``pip`` run the following command:

.. code:: bash

    pip install schemathesis

This command installs the ``st`` entrypoint.

You can also use our Docker image without installing Schemathesis as a Python package.

Usage
-----

You can use Schemathesis in the command line directly:

.. code:: bash

  st run --checks all https://example.schemathesis.io/openapi.json

Or via Docker:

.. code:: bash

  docker run schemathesis/schemathesis:stable \
      run --checks all https://example.schemathesis.io/openapi.json

.. image:: https://raw.githubusercontent.com/schemathesis/schemathesis/master/img/schemathesis.gif

Or in your Python tests:

.. code:: python

    import schemathesis

    schema = schemathesis.from_uri("https://example.schemathesis.io/openapi.json")


    @schema.parametrize()
    def test_api(case):
        case.call_and_validate()

CLI is simple to use and requires no coding; the in-code approach gives more flexibility.

Both examples above will run hundreds of requests against the API under test and report all found failures and inconsistencies along with instructions to reproduce them.

💡 See a complete working example project in the ``/example`` directory. 💡

Support
-------

If you want to integrate Schemathesis into your company workflows or improve its effectiveness, feel free to reach out to `support@schemathesis.io`.

Schemathesis.io also runs workshops about effective API testing. `Signup here <https://forms.gle/epkovRdQNMCYh2Ax8>`_

Contributing
------------

Any contribution to development, testing, or any other area is highly appreciated and useful to the project.
For guidance on how to contribute to Schemathesis, see the `contributing guidelines <https://github.com/schemathesis/schemathesis/blob/master/CONTRIBUTING.rst>`_.

Links
-----

- **Documentation**: https://schemathesis.readthedocs.io/en/stable/
- **Releases**: https://pypi.org/project/schemathesis/
- **Code**: https://github.com/schemathesis/schemathesis
- **Issue tracker**: https://github.com/schemathesis/schemathesis/issues
- **Chat**: https://discord.gg/R9ASRAmHnA

Additional content:

- Research paper: `Deriving Semantics-Aware Fuzzers from Web API Schemas <https://arxiv.org/abs/2112.10328>`_ by **@Zac-HD** and **@Stranger6667**
- `An article <https://dygalo.dev/blog/schemathesis-property-based-testing-for-api-schemas/>`_ about Schemathesis by **@Stranger6667**
- `Effective API schemas testing <https://youtu.be/VVLZ25JgjD4>`_ from DevConf.cz by **@Stranger6667**
- `How to use Schemathesis to test Flask API in GitHub Actions <https://notes.lina-is-here.com/2022/08/04/schemathesis-docker-compose.html>`_ by **lina-is-here**
- `A video <https://www.youtube.com/watch?v=9FHRwrv-xuQ>`_ from EuroPython 2020 by **@hultner**
- `Schemathesis tutorial <https://appdev.consulting.redhat.com/tracks/contract-first/automated-testing-with-schemathesis.html>`_  with an accompanying `video <https://www.youtube.com/watch?v=4r7OC-lBKMg>`_ by Red Hat
- `Using Hypothesis and Schemathesis to Test FastAPI <https://testdriven.io/blog/fastapi-hypothesis/>`_ by **@amalshaji**

Non-English content:

- `A tutorial <https://habr.com/ru/company/oleg-bunin/blog/576496/>`_ (RUS) about Schemathesis by **@Stranger6667**

License
-------

The code in this project is licensed under `MIT license`_.
By contributing to Schemathesis, you agree that your contributions will be licensed under its MIT license.

.. |Build| image:: https://github.com/schemathesis/schemathesis/workflows/build/badge.svg
   :target: https://github.com/schemathesis/schemathesis/actions
.. |Coverage| image:: https://codecov.io/gh/schemathesis/schemathesis/branch/master/graph/badge.svg
   :target: https://codecov.io/gh/schemathesis/schemathesis/branch/master
   :alt: codecov.io status for master branch
.. |Version| image:: https://img.shields.io/pypi/v/schemathesis.svg
   :target: https://pypi.org/project/schemathesis/
.. |Python versions| image:: https://img.shields.io/pypi/pyversions/schemathesis.svg
   :target: https://pypi.org/project/schemathesis/
.. |License| image:: https://img.shields.io/pypi/l/schemathesis.svg
   :target: https://opensource.org/licenses/MIT
.. |Chat| image:: https://img.shields.io/discord/938139740912369755
   :target: https://discord.gg/R9ASRAmHnA
   :alt: Discord
.. |Docs| image:: https://readthedocs.org/projects/schemathesis/badge/?version=stable
   :target: https://schemathesis.readthedocs.io/en/stable/?badge=stable
   :alt: Documentation Status

.. _MIT license: https://opensource.org/licenses/MIT
