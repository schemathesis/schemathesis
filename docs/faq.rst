.. _faq:

Frequently Asked Questions
==========================

This page answers some of the often asked questions about Schemathesis.

Usage & Configuration
---------------------

What kind of data does Schemathesis generate?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Schemathesis generates random test data that conforms to the given API schema.
This data consists of all possible data types from the JSON schema specification in various combinations and different nesting levels.

What parts of the application is Schemathesis targeting during its tests?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

It depends. The test data that Schemathesis generates is random. Input validation is, therefore, more frequently examined than other parts.

Since Schemathesis generates data that fits the application's API schema, it can reach the app's business logic, but it depends on the architecture of each particular application.

How should I run Schemathesis?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

There are two main ways to run it — as a part of Python's test suite, and as a command-line tool.

If you wrote a Python application and you want to utilize the features of an existing test suite, then the in-code option will best suit your needs.

If you wrote your application in a language other than Python, then you should use the built-in CLI. Please keep in mind that you will need to have a running application where you can run Schemathesis against.


Should I always have my application running before starting the test suite?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Yes, except for Python apps that are either built with AioHTTP or implement WSGI (like Flask or Django).

Can I exclude particular data from being generated?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Yes. Schemathesis' hooks mechanism allows you to adapt its behavior and generate data that fits better with your use case.

Also, if your application fails on some input early in the code, then it's often a good idea to exclude this input from the next test run so you can explore deeper parts of your codebase.

How can I use database objects IDs in tests?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``case`` object, that is injected in each test can be modified, assuming your URL template is ``/api/users/{user_id}`` then in tests it can be done like this:

.. code:: python

    @schema.parametrize()
    def test_api(case):
        case.path_parameters["user_id"] = 42

Working with API schemas
------------------------

How to disallow random field names in my schema?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

You need to add ``additionalProperties: false`` to the relevant object definition. But there is a caveat with emulating
inheritance with Open API via ``allOf``.

In this case, it is better to use YAML anchors to share schema parts, otherwise it will prevent valid data to pass the validation.
