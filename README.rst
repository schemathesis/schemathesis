Schemathesis
============

|Build| |Coverage| |Version| |Python versions| |Docs| |Chat| |License|

Schemathesis is a modern API testing tool for web applications built with Open API and GraphQL specifications.

It reads the application schema and generates test cases, which will ensure that your application is compliant with its schema.

The application under test could be written in any language; the only thing you need is a valid API schema in a supported format.

Simple to use and yet powerful to uncover hard-to-find errors thanks to the property-based testing approach backed by state-of-the-art `Hypothesis <http://hypothesis.works/>`_ library.

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

.. image:: https://github.com/schemathesis/schemathesis/blob/master/img/schemathesis.gif

Or in your Python tests:

.. code:: python

    import schemathesis

    schema = schemathesis.from_uri("http://example.com/swagger.json")

    @schema.parametrize()
    def test_api(case):
        case.call_and_validate()

CLI is simple to use and requires no coding; the in-code approach gives more flexibility.

Contributing
------------

Any contribution to development, testing, or any other area is highly appreciated and useful to the project.
For guidance on how to contribute to Schemathesis, see the `contributing guidelines <https://github.com/schemathesis/schemathesis/blob/master/CONTRIBUTING.rst>`_.

**Please, help us to improve!**

We'd kindly ask you to share your experience with Schemathesis. It will help me to make improvements to it and prioritize new features!
It will take 5 minutes. The results are anonymous.

**Survey**: https://forms.gle/dv4s5SXAYWzvuwFWA

Support this project
--------------------

Hi, my name is Dmitry! I started this project during my work at `Kiwi.com <https://kiwi.com/>`_. I am grateful to them for all the support they
provided to this project during its early days and for the opportunity to evolve Schemathesis independently.

In order to grow the community of contributors and users, and allow me to devote more time to this project, please `donate today <https://github.com/sponsors/Stranger6667>`_.

Also, I occasionally write posts about Schemathesis in `my blog <https://dygalo.dev/>`_ and offer consulting services for businesses.

Links
-----

- **Documentation**: https://schemathesis.readthedocs.io/en/stable/
- **Releases**: https://pypi.org/project/schemathesis/
- **Code**: https://github.com/schemathesis/schemathesis
- **Issue tracker**: https://github.com/schemathesis/schemathesis/issues
- **Chat**: https://gitter.im/schemathesis/schemathesis

Additional content:

- `An article <https://dygalo.dev/blog/schemathesis-property-based-testing-for-api-schemas/>`_ about Schemathesis by **@Stranger6667**
- `A video <https://www.youtube.com/watch?v=9FHRwrv-xuQ>`_ from EuroPython 2020 by **@hultner**

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
