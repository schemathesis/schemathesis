Continuous Integration
======================

Welcome to the Schemathesis CI guide! This document provides all the necessary information to integrate Schemathesis
into your Continuous Integration workflows.

Quickstart
----------

You can use these code samples to test your API in a pull request or run tests against a publicly resolvable API.

If you need to start your API server locally before testing, check out the `Preparing your App`_ section below.

GitHub Actions
~~~~~~~~~~~~~~

.. important::

    We have a native `GitHub app`_ that reports test results directly to your pull requests.

.. code-block:: yaml

  api-tests:
    runs-on: ubuntu-20.04
    steps:
      # Runs Schemathesis tests with all checks enabled
      - uses: schemathesis/action@v1
        with:
          # Your API schema location
          schema: 'http://localhost:5000/api/openapi.json'
          # OPTIONAL. Your Schemathesis.io token
          token: ${{ secrets.SCHEMATHESIS_TOKEN }}

For the fully working example, check |no-build.yml|_ in the repository.

.. |no-build.yml| replace:: ``.github/workflows/example-no-build.yml``
.. _no-build.yml: https://github.com/schemathesis/schemathesis/blob/master/.github/workflows/example-no-build.yml

If you enabled PR comments via our `GitHub app`_, you'll see a test report once your pipeline is finished:

.. image:: https://raw.githubusercontent.com/schemathesis/schemathesis/master/img/service_github_report.png

GitLab CI
~~~~~~~~~

.. code-block:: yaml

  api-tests:
    stage: test
    image:
      name: schemathesis/schemathesis:stable
      entrypoint: [""]

    variables:
      # API Schema location
      API_SCHEMA: 'http://localhost:5000/api/openapi.json'
      # OPTIONAL. Your Schemathesis.io token
      SCHEMATHESIS_TOKEN: ${{ secrets.SCHEMATHESIS_TOKEN }}

    script:
      - st run $API_SCHEMA --checks=all --report

How does it work?
------------------

Schemathesis works over HTTP and expects that your application is reachable from the CI environment.
You can prepare your application in the same CI as the previous step or run it against a staging environment.

Preparing your App
------------------

Start API before testing
~~~~~~~~~~~~~~~~~~~~~~~~

It is common to have a test suite as a part of the application repo. For this scenario, you will need to build your app first.

The application could be built in **any programming language**, Schemathesis expects only its API schema.

Here is a GitHub Actions workflow for a sample `Python app`_:

.. code-block:: yaml

  api-tests:
    runs-on: ubuntu-20.04
    steps:
      # Gets a copy of the source code in your repository before running API tests
      - uses: actions/checkout@v3.0.0

      - uses: actions/setup-python@v4
        with:
          python-version: '3.10'

      # Installs project's dependencies
      - run: pip install -r requirements.txt

      # Start the API in the background
      - run: python main.py &

      # Runs Schemathesis tests with all checks enabled
      - uses: schemathesis/action@v1
        with:
          # Your API schema location
          schema: 'http://localhost:5000/api/openapi.json'
          # OPTIONAL. Your Schemathesis.io token
          token: ${{ secrets.SCHEMATHESIS_TOKEN }}

.. note::

   This example expects the API schema available at ``http://localhost:5000/api/openapi.json`` inside the CI environment.

For the fully working example, check |build.yml|_ in the repository.

.. |build.yml| replace:: ``.github/workflows/example-build.yml``
.. _build.yml: https://github.com/schemathesis/schemathesis/blob/master/.github/workflows/example-build.yml

API schema in a file
~~~~~~~~~~~~~~~~~~~~

If you store your API schema in a file, use its file path for the ``API_SCHEMA`` environment variable.
Set your API base path to ``SCHEMATHESIS_BASE_URL``:

.. code-block:: yaml

  api-tests:
    runs-on: ubuntu-20.04
    steps:
      # Runs positive Schemathesis tests
      - uses: schemathesis/action@v1
        with:
          # A local API schema location
          schema: './docs/openapi.json'
          # API base URL
          base-url: 'http://127.0.0.1:8080/api/v2/'
          # OPTIONAL. Your Schemathesis.io token
          token: ${{ secrets.SCHEMATHESIS_TOKEN }}

.. _Python app: https://github.com/schemathesis/schemathesis/tree/master/example
.. _GitHub app: https://github.com/apps/schemathesis
