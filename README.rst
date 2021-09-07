Schemathesis
============

|Build| |Coverage| |Version| |Python versions| |Docs| |Chat| |License|

Schemathesis is a modern API testing tool for web applications built with Open API and GraphQL specifications.

It reads the application schema and generates test cases, which will ensure that your application is compliant with its schema.

The application under test could be written in any language; the only thing you need is a valid API schema in a supported format.

Simple to use and yet powerful to uncover hard-to-find errors thanks to the property-based testing approach backed by state-of-the-art `Hypothesis <http://hypothesis.works/>`_ library.

📣 **Please fill out our** `quick survey <https://forms.gle/dv4s5SXAYWzvuwFWA>`_ so that we can learn how satisfied you are with Schemathesis, and what improvements we should make. Thank you!

Features
--------

- Content-Type, schema, and status code conformance checks for Open API;
- Testing of explicit examples from the input schema;
- Stateful testing via Open API links;
- Concurrent test execution;
- Targeted testing;
- Storing and replaying network requests;
- Built-in ASGI / WSGI application support;
- Code samples for easy failure reproduction;
- Ready-to-go Docker image;
- Configurable with user-defined checks, string formats, hooks, and targets.

Installation
------------

To install Schemathesis via ``pip`` run the following command:

.. code:: bash

    pip install schemathesis

Usage
-----

You can use Schemathesis in the command line:

.. code:: bash

  schemathesis run --stateful=links --checks all http://0.0.0.0:8081/schema.yaml

.. image:: https://raw.githubusercontent.com/schemathesis/schemathesis/master/img/schemathesis.gif

Or in your Python tests:

.. code:: python

    import schemathesis

    schema = schemathesis.from_uri("http://0.0.0.0:8081/schema.yaml")


    @schema.parametrize()
    def test_api(case):
        case.call_and_validate()

CLI is simple to use and requires no coding; the in-code approach gives more flexibility.

Both examples above will run hundreds of requests against the API under test and report all found failures and inconsistencies along with instructions to reproduce them.

💡 See a complete working example project in the ``/example`` directory. 💡

Contributing
------------

Any contribution to development, testing, or any other area is highly appreciated and useful to the project.
For guidance on how to contribute to Schemathesis, see the `contributing guidelines <https://github.com/schemathesis/schemathesis/blob/master/CONTRIBUTING.rst>`_.

Support this project
--------------------

Hi, my name is Dmitry! I started this project during my work at `Kiwi.com <https://kiwi.com/>`_. I am grateful to them for all the support they
provided to this project during its early days and for the opportunity to evolve Schemathesis independently.

In order to grow the community of contributors and users, and allow me to devote more time to this project, please `donate today <https://github.com/sponsors/Stranger6667>`_.

Also, I occasionally write posts about Schemathesis in `my blog <https://dygalo.dev/>`_ and offer consulting services for businesses.

Commercial support
------------------

If you are interested in the effective integration of Schemathesis to your private project, you can contact me via email or `Twitter <https://twitter.com/Stranger6667>`_ and I will help you do that.

Links
-----

- **Documentation**: https://schemathesis.readthedocs.io/en/stable/
- **Releases**: https://pypi.org/project/schemathesis/
- **Code**: https://github.com/schemathesis/schemathesis
- **Issue tracker**: https://github.com/schemathesis/schemathesis/issues
- **Chat**: https://gitter.im/schemathesis/schemathesis

Additional content:

- `An article <https://dygalo.dev/blog/schemathesis-property-based-testing-for-api-schemas/>`_ about Schemathesis by **@Stranger6667**
- `Effective API schemas testing <https://youtu.be/VVLZ25JgjD4>`_ from DevConf.cz by **@Stranger6667**
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
.. |Chat| image:: https://img.shields.io/gitter/room/schemathesis/schemathesis.svg
   :target: https://gitter.im/schemathesis/schemathesis
   :alt: Gitter
.. |Docs| image:: https://readthedocs.org/projects/schemathesis/badge/?version=stable
   :target: https://schemathesis.readthedocs.io/en/stable/?badge=stable
   :alt: Documentation Status

.. _MIT license: https://opensource.org/licenses/MIT
