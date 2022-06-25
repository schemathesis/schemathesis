Schemathesis as a Service
=========================

`Schemathesis.io <https://app.schemathesis.io/auth/sign-up/?utm_source=oss_docs&utm_content=saas_docs_top>`_ is a platform that runs property-based API tests and visualises their outcomes for you. It also may store
your CLI test results and run additional analysis on them.

On top of the usual Schemathesis benefits, the platform gives you:

- Handy visual navigation through test results
- Additional static analysis of your API schema & app responses
- Improved data generation, that finds more bugs
- Many more additional checks for Open API & GraphQL issues
- Visual API schema coverage (**COMING SOON**)
- Tailored tips on API schema improvement (**COMING SOON**)
- Support for gRPC, AsyncAPI, and SOAP (**COMING SOON**)

Tutorial
--------

This step-by-step tutorial walks you through the flow of setting up your Schemathesis.io account to test your Open API schema.
As part of this tutorial, you will:

- Add your Open API schema to Schemathesis.io
- Execute property-based tests against your application
- See what request parameters cause issues

.. note::

    We provide a sample Flask application with a pre-defined set of problems to demonstrate some of the possible issues
    Schemathesis.io can find automatically. You can find the `source code <https://github.com/schemathesis/schemathesis/tree/master/test/apps/openapi/_flask>`_ in the Schemathesis repository.

Alternatively, you can follow this guide as a reference and run tests against your Open API or GraphQL based application.

Prerequisites
~~~~~~~~~~~~~

- A Schemathesis.io `account <https://app.schemathesis.io/auth/sign-up/?utm_source=oss_docs&utm_content=saas_docs_prerequisites>`_

Step 1: Add the API schema
~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Open `Schemathesis.io dashboard <https://app.schemathesis.io/apis/>`_
2. Click on the **Add API** button to get to the API schema submission form

.. image:: https://raw.githubusercontent.com/schemathesis/schemathesis/master/img/service_no_apis_yet.png

3. Enter your API name, so you can easily identify it later (for example, "Example API")
4. Fill **https://example.schemathesis.io/openapi.json** into the "API Schema" field
5. **Optional**. If your API requires authentication, choose the appropriate authentication type (HTTP Basic & Header are available at the moment) and fill in its details
6. **Optional**. If your API is available on a different domain than your API schema, fill the proper base URL into the "Base URL" field
7. Save the API schema entry by clicking "Add"

.. image:: https://raw.githubusercontent.com/schemathesis/schemathesis/master/img/service_api_form.png

.. warning::

    Don't ever run tests against your production deployments!

Step 2: Run API tests
~~~~~~~~~~~~~~~~~~~~~

At this point, you can start testing your API! The simplest option is to use our test runners on the "Cloud" tab.

.. image:: https://raw.githubusercontent.com/schemathesis/schemathesis/master/img/service_api_created.png

**Optional**. If you'd like to run tests on your side and upload the results to Schemathesis.io feel free to use one of the provided code samples:

Generate an access token and authenticate into Schemathesis.io first:

.. code:: text

    # Replace `LOmOZoBh3V12aP3rRkvqYYKGGGV6Ag` with your token
    st auth login LOmOZoBh3V12aP3rRkvqYYKGGGV6Ag

And then run the tests:

.. code::

    st run demo-1 --checks all --report

.. note::

    Replace ``demo-1`` with the appropriate API ID shown in the SaaS code sample

Once all events are uploaded to Schemathesis.io you'll see a message at the end of the CLI output:

.. code:: text

    Upload: COMPLETED

    Your test report is successfully uploaded! Please, follow this link for details:

    https://app.schemathesis.io/r/mF9ke/

To observe the test run results, follow the link from the output.

Step 3: Observe the results
~~~~~~~~~~~~~~~~~~~~~~~~~~~

As the tests are running you will see failures appear in the UI:

.. image:: https://raw.githubusercontent.com/schemathesis/schemathesis/master/img/service_run_results.png

Each entry in the **Failures** list is clickable, and you can check its details. The failure below shows that the application
response does not conform to its API schema and shows what part of the schema was violated.

.. image:: https://raw.githubusercontent.com/schemathesis/schemathesis/master/img/service_non_conforming_response.png

In this case, the schema requires the "success" property to be present but it is absent in the response.

Each failure is accompanied by a cURL snippet you can use to reproduce the issue.

.. image:: https://raw.githubusercontent.com/schemathesis/schemathesis/master/img/service_server_error.png

Alternatively, you can use the **Replay** button on the failure page.

What data is sent?
------------------

CLI sends info to Schemathesis.io in the following cases:

- Authentication. Metadata about your host machine, that helps us to understand our users better. We collect your Python interpreter version, implementation, system/OS name and release. For more information look at ``service/metadata.py``
- Test runs. Most of Schemathesis runner's events, including all generated data and explicitly passed headers. For more information look at ``service/serialization.py``
- Some environment variables specific to CI providers. We use them to comment on pull requests.
- Command-line options without free-form values. It helps us to understand how you use the CLI.
