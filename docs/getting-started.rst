Getting Started
===============

Choose from multiple ways to start testing your API with Schemathesis.

.. note:: Your API schema can be either a URL or a local path to a JSON/YAML file.

Command-Line Interface
----------------------

Quick and easy for those who prefer the command line.

Python
^^^^^^

1. Install via pip: ``python -m pip install schemathesis``
2. Run tests:

.. code-block:: bash

   st run --checks all https://example.schemathesis.io/openapi.json

Docker
^^^^^^

1. Pull Docker image: ``docker pull schemathesis/schemathesis:stable``
2. Run tests:

.. code-block:: bash

   docker run schemathesis/schemathesis:stable
      run --checks all https://example.schemathesis.io/openapi.json

Python Library
--------------

For more control and customization, integrate Schemathesis into your Python codebase.

1. Install via pip: ``python -m pip install schemathesis``
2. Add to your tests:

.. code-block:: python

   import schemathesis

   schema = schemathesis.from_uri("https://example.schemathesis.io/openapi.json")


   @schema.parametrize()
   def test_api(case):
       case.call_and_validate()

.. note:: See a complete working example project in the `/example <https://github.com/schemathesis/schemathesis/tree/master/example>`_ directory.

GitHub Integration
------------------

GitHub Actions
^^^^^^^^^^^^^^

.. note::

    💡 See our `GitHub Tutorial <https://docs.schemathesis.io/tutorials/github>`_ for a step-by-step guidance.

Run Schemathesis tests as a part of your CI/CD pipeline. Add this YAML configuration to your GitHub Actions:

.. code-block:: yaml

   api-tests:
     runs-on: ubuntu-22.04
     steps:
       - uses: schemathesis/action@v1
         with:
           schema: "https://example.schemathesis.io/openapi.json"
           # OPTIONAL. Add Schemathesis.io token for pull request reports
           token: ${{ secrets.SCHEMATHESIS_TOKEN }}

For more details, check out our `GitHub Action <https://github.com/schemathesis/action>`_ repository.

GitHub App
^^^^^^^^^^

Receive automatic comments in your pull requests and updates on GitHub checks status. Requires usage of our `SaaS platform <https://app.schemathesis.io/auth/sign-up/?utm_source=oss_docs&utm_content=index_note>`_.

1. Install the `GitHub app <https://github.com/apps/schemathesis>`_.
2. Enable in your repository settings.

.. image:: https://raw.githubusercontent.com/schemathesis/schemathesis/master/img/service_github_report.png

Service
-------

If you prefer an all-in-one solution with quick setup, we have a `free tier <https://schemathesis.io/#pricing>`_ available.
