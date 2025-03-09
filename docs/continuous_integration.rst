Continuous Integration
======================

This guide outlines how to set up Schemathesis for automated API testing in your Continuous Integration workflows.

GitHub Actions
--------------

For initial setup in a GitHub Actions workflow:

.. code-block:: yaml

  api-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: schemathesis/action@v1
        with:
          schema: 'http://127.0.0.1:5000/api/openapi.json'

To add headers like ``Authorization``:

.. code-block:: yaml

  # Save access token to $GITHUB_ENV as ACCESS_TOKEN.
  - name: Set access token
    run: echo "ACCESS_TOKEN=super-secret" >> $GITHUB_ENV

  - uses: schemathesis/action@v1
    with:
      schema: 'http://example.com/api/openapi.json'
      args: '-H "Authorization: Bearer ${{ env.ACCESS_TOKEN }}"'

.. note::

    For more details on using Schemathesis with GitHub Actions, refer to the `full documentation <https://github.com/schemathesis/action>`_

GitLab CI
---------

For GitLab users, here's how to set up Schemathesis in your CI pipeline:

.. code-block:: yaml

  api-tests:
    stage: test
    image:
      name: schemathesis/schemathesis:stable
      entrypoint: [""]

    script:
      - st run http://127.0.0.1:5000/api/openapi.json --checks=all --report

Preparing Your App
------------------

In most cases, you'll want to set up your app in the CI environment. Here's a GitHub Actions example for a `Python app`_:

.. code-block:: yaml

  api-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3.0.0
      - uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      - run: pip install -r requirements.txt
      # Start the API in the background
      - run: python main.py &
      - uses: schemathesis/action@v1
        with:
          schema: 'http://127.0.0.1:5000/api/openapi.json'

API Schema in a File
--------------------

If your API schema is maintained separately from the application, specify its path and set a base URL.
This is useful in scenarios where the API schema undergoes independent versioning or resides in a separate repository.

.. code-block:: yaml

  api-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: schemathesis/action@v1
        with:
          schema: './docs/openapi.json'
          base-url: 'http://127.0.0.1:5000/api/v2/'

Environment Variables
---------------------

You can configure Schemathesis behavior using the following environment variables:

- **SCHEMATHESIS_HOOKS**: Points to a Python module with user-defined Schemathesis extensions. Example: ``my_module.my_hooks``

- **SCHEMATHESIS_BASE_URL**: Set when using a file-based schema to specify the API's base URL. Example: ``http://127.0.0.1:5000/api/v2/``

- **SCHEMATHESIS_WAIT_FOR_SCHEMA**: Time in seconds to wait for the schema to be accessible. Example: ``10``

- **SCHEMATHESIS_REPORT_SUGGESTION**: Enable or disable report suggestions to upload to SaaS. Valid values: ``true``, ``false``

- **SCHEMATHESIS_TOKEN**: For SaaS-based pull request comments.

- **SCHEMATHESIS_TELEMETRY**: Toggle sending metadata to SaaS. Valid values: ``true``, ``false``

- **SCHEMATHESIS_REPORT**: Enable or disable reporting. Valid values: ``true``, ``false``

.. _Python app: https://github.com/schemathesis/schemathesis/tree/master/example
