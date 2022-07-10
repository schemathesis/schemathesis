Continuous Integration
======================

Welcome to the Schemathesis CI guide! This document provides all the necessary information to integrate Schemathesis
into your Continuous Integration workflows.

GitHub Actions
--------------

Publicly available `Open API 3 app`_:

.. code-block:: yaml

  api-tests:
    runs-on: ubuntu-20.04
    container: schemathesis/schemathesis:stable

    env:
      # Your API schema location
      API_SCHEMA: 'https://example.schemathesis.io/openapi.json'
      # Whether you'd like to see the results in a Web UI in Schemathesis.io. Off by default
      SCHEMATHESIS_REPORT: 'true'

    steps:
      # Runs positive Schemathesis tests with all checks enabled
      - run: st run $API_SCHEMA --checks=all

A `Python app`_ which you need to start manually:

.. code-block:: yaml

  api-tests:
    runs-on: ubuntu-20.04
    container: python:3.10.5-slim

    env:
      # Your API schema location
      API_SCHEMA: 'http://localhost:5000/api/openapi.json'
      # Whether you'd like to see the results in a Web UI in Schemathesis.io. Off by default
      SCHEMATHESIS_REPORT: 'true'

    steps:
      # Gets a copy of the source code in your repository before running API tests
      - uses: actions/checkout@v3.0.0

      # Install the project's dependencies and Schemathesis
      - run: pip install asyncpg attrs connexion[aiohttp,swagger-ui] schemathesis

      # Start the API in the background
      - run: python main.py &

      # Waits until localhost:5000 is available
      # Tries to connect every 200 ms with a total waiting time of 5 seconds
      - name: Wait for API
        run: >
          timeout 5
          bash -c
          'until printf "" 2>>/dev/null >>/dev/tcp/$0/$1; do sleep 0.2; done'
          localhost 5000

      # Run positive Schemathesis tests with all checks enabled
      - run: st run $API_SCHEMA --checks=all

.. note::

    For the complete example, check ``.github/workflows/example-project.yml`` in the repository.

GitLab CI
---------

Publicly available `Open API 3 app`_:

.. code-block:: yaml

  api-tests:
    stage: test
    image:
      name: schemathesis/schemathesis:stable
      entrypoint: [""]

    variables:
      # Your API schema location
      API_SCHEMA: 'https://example.schemathesis.io/openapi.json'
      # Whether you'd like to see the results in a Web UI in Schemathesis.io. Off by default
      SCHEMATHESIS_REPORT: 'true'

    script:
      - st run $API_SCHEMA --checks=all


A `Python app`_ which you need to start manually:

.. code-block:: yaml

  api-tests:
    stage: test
    image:
      name: schemathesis/schemathesis:stable
      entrypoint: [""]

    variables:
      # Your API schema location
      API_SCHEMA: 'https://example.schemathesis.io/openapi.json'
      # Whether you'd like to see the results in a Web UI in Schemathesis.io. Off by default
      SCHEMATHESIS_REPORT: 'true'

    script:
      # Install the project's dependencies and Schemathesis
      - pip install asyncpg attrs connexion[aiohttp,swagger-ui] schemathesis

      # Start the API in the background
      - python main.py &

      # Waits until localhost:5000 is available
      # Tries to connect every 200 ms with a total waiting time of 5 seconds
      - |
        timeout 5
        bash -c
        'until printf "" 2>>/dev/null >>/dev/tcp/$0/$1; do sleep 0.2; done'
        localhost 5000

      # Run positive Schemathesis tests with all checks enabled
      - st run $API_SCHEMA --checks=all

How does it works?
------------------

Schemathesis works over HTTP and expects that your application is reachable from the CI environment.
The application itself could live separately from the CI environment or could be built as the previous step.

For the latter case, you need to ensure that the app has started before running Schemathesis.
Here is a Bash snippet you can copy-paste:

.. code-block::

    timeout 5
    bash -c
    'until printf "" 2>>/dev/null >>/dev/tcp/$0/$1; do sleep 0.2; done'
    localhost 5000

It will try to connect to ``localhost:5000`` until it is available or bail out after 5 seconds.

.. _Open API 3 app: https://example.schemathesis.io/openapi.json
.. _Python app: https://github.com/schemathesis/schemathesis/tree/master/example
