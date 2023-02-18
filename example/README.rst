Example project
===============

A simple web app, built with `connexion <https://github.com/zalando/connexion>`_,
`aiohttp <https://github.com/aio-libs/aiohttp>`_, `attrs <https://github.com/python-attrs/attrs>`_ and `asyncpg <https://github.com/MagicStack/asyncpg>`_.
It contains many intentional errors, which should be found by running Schemathesis.

There is also `a tutorial <https://habr.com/ru/company/oleg-bunin/blog/576496/>`_ in Russian that follows this example project.

Setup
-----

To run the examples below, you need the recent version of `docker-compose <https://docs.docker.com/compose/install/>`_ and Schemathesis installed locally.

Start the application via `docker-compose`:

.. code::

    docker-compose up

It will spin up a web server available at ``http://127.0.0.1:5000``. You can take a look at API documentation at ``http://127.0.0.1:5000/api/ui/``.
Note, the app will run in the current terminal.

Install ``schemathesis`` via ``pip`` to a virtual environment:

.. code::

    pip install schemathesis

It will install additional dependencies, including ``pytest``.

Python tests
------------

Run the test suite via ``pytest`` in a separate terminal:

.. code::

    pytest -v test

These tests include:

- A unit test & an integration test;
- Custom hypothesis settings;
- Using ``pytest`` fixtures;
- Providing a custom authorization header;
- Custom strategy for Open API string format;
- A hook for data generation;
- Custom response check;

See the details in the ``/test`` directory.

Command-line
------------

Here are examples of how you can run Schemathesis CLI:

.. code:: bash

    export SCHEMA_URL="http://127.0.0.1:5000/api/openapi.json"
    export PYTHONPATH=$(pwd)

    # Default config. Runs unit tests for all API operations with `not_a_server_error` check
    st run $SCHEMA_URL

    # Select what to test. Only `POST` operations that have `booking` in their path
    st run -E booking -M POST $SCHEMA_URL

    # What checks to run
    st run -c status_code_conformance $SCHEMA_URL

    # Include your own checks. They should be registered in the `test/hooks.py` module
    SCHEMATHESIS_HOOKS=test.hooks st run $SCHEMA_URL

    # Provide custom headers
    st run -H "Authorization: Bearer <token>" $SCHEMA_URL

    # Configure hypothesis parameters. Run up to 1000 examples per tested operation
    st run --hypothesis-max-examples 1000 $SCHEMA_URL

    # Run in multiple threads
    st run -w 8 $SCHEMA_URL

    # Store network log to a file
    st run --cassette-path=cassette.yaml $SCHEMA_URL
    # Replay requests from the log
    st replay cassette.yaml

    # Integration tests
    st run $SCHEMA_URL
