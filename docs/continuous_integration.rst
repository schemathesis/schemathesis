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
    container: schemathesis/schemathesis:stable

    env:
      # API Schema location
      API_SCHEMA: 'http://localhost:5000/api/openapi.json'
      # OPTIONAL. Whether you'd like to see the results in a Web UI in Schemathesis.io
      SCHEMATHESIS_REPORT: 'true'
      # OPTIONAL. Your Schemathesis.io token
      SCHEMATHESIS_TOKEN: ${{ secrets.SCHEMATHESIS_TOKEN }}

    steps:
      # Runs Schemathesis tests with all checks enabled
      - run: st run $API_SCHEMA --checks=all

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
      # OPTIONAL. Whether you'd like to see the results in a Web UI in Schemathesis.io
      SCHEMATHESIS_REPORT: 'true'
      # OPTIONAL. Your Schemathesis.io token
      SCHEMATHESIS_TOKEN: ${{ secrets.SCHEMATHESIS_TOKEN }}

    script:
      - st run $API_SCHEMA --checks=all

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
    container: python:3.10.5-slim

    env:
      # Your API schema location
      API_SCHEMA: 'http://localhost:5000/api/openapi.json'
      # OPTIONAL. Whether you'd like to see the results in a Web UI in Schemathesis.io
      SCHEMATHESIS_REPORT: 'true'
      # OPTIONAL. Maximum time in seconds to wait on the API schema availability.
      SCHEMATHESIS_WAIT_FOR_SCHEMA: 5
      # OPTIONAL. Your Schemathesis.io token
      SCHEMATHESIS_TOKEN: ${{ secrets.SCHEMATHESIS_TOKEN }}

    steps:
      # Gets a copy of the source code in your repository before running API tests
      - uses: actions/checkout@v3.0.0

      # Installs project's dependencies & Schemathesis
      - run: pip install -r requirements.txt schemathesis

      # Start the API in the background
      - run: python main.py &

      # Run Schemathesis tests with all checks enabled
      - run: st run $API_SCHEMA --checks=all

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
    container: schemathesis/schemathesis:stable

    env:
      # API schema file path
      API_SCHEMA: './docs/openapi.json'
      # API base URL
      SCHEMATHESIS_BASE_URL: 'http://127.0.0.1:8080/api/v2/'
      # OPTIONAL. Whether you'd like to see the results in a Web UI in Schemathesis.io
      SCHEMATHESIS_REPORT: 'true'
      # OPTIONAL. Your Schemathesis.io token
      SCHEMATHESIS_TOKEN: ${{ secrets.SCHEMATHESIS_TOKEN }}

.. _Python app: https://github.com/schemathesis/schemathesis/tree/master/example
.. _GitHub app: https://github.com/apps/schemathesis
