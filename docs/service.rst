Schemathesis as a Service
=========================

This library integrates with the Schemathesis.io platform, where you can store, view, and track issues found by Schemathesis.

.. important::

    Schemathesis.io is currently in alpha and intended for testing only. If you want to try it out, ping me at **hello@schemathesis.io**.

Quickstart
----------

First, you need to add your API to Schemathesis.io by clicking on the "Add API" button and filling details in a form on that page:

.. image:: https://raw.githubusercontent.com/schemathesis/schemathesis/master/img/service_no_apis_yet.png

Then get a token from the "Details" page by clicking on a little gear icon:

.. image:: https://raw.githubusercontent.com/schemathesis/schemathesis/master/img/service_gear_icon.png

Then you can find an API token there. Note that tokens are unique per API:

.. image:: https://raw.githubusercontent.com/schemathesis/schemathesis/master/img/service_details.png

Once you got an API token, you can use it with Schemathesis CLI. Integrations with ``pytest`` and ``unittest`` are in the works.

Command Line Interface
~~~~~~~~~~~~~~~~~~~~~~

Add ``--schemathesis-io-token=<YOUR API TOKEN>`` to your Schemathesis CLI invocation:

.. code:: bash

    schemathesis run http://127.0.0.1:8081/schema.yaml \
      --schemathesis-io-token=6656aeaaf89046ef9180f42f9929871e

Once all events are uploaded to Schemathesis.io you'll see a message at the end of the CLI output:

.. code:: text

    Schemathesis.io: SUCCESS

Features overview
-----------------

Schemathesis.io stores data about your test runs and aims to provide a convenient UI to navigate through found failures.
These failures are aggregated into groups, so you can track recurring ones.

.. image:: https://raw.githubusercontent.com/schemathesis/schemathesis/master/img/service_issues_list.png

You also have better failure description and an ability to replay failures from the UI:

.. image:: https://raw.githubusercontent.com/schemathesis/schemathesis/master/img/service_issue_detail.png

You can use Schemathesis.io test runners instead of CLI. They have more checks & better data generation than Open Source Schemathesis.

.. important::

    Again, it is an alpha version and I am delighted to hear from you! Please, if you miss any feature or have any comments on this, let me know.

How it works
------------

The integration is done with a separate event handler that sends events to Schemathesis.io in a separate thread.

What data is sent to Schemathesis.io
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

At the moment, Schemathesis sends almost everything defined in ``schemathesis.runner.events``, so
you have all information needed to reproduce failures. However, it might change in the future.
