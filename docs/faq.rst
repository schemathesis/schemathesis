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

How is Schemathesis different from Dredd?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Schemathesis focuses on finding inputs that result in application crash, but it shares the goal of keeping the API documentation up to date with Dredd.
Both tools can generate requests to the API under test, but they approach it differently.

Schemathesis uses Property-Based Testing to infer all input values and uses examples defined in the API schema as separate test cases.
Dredd uses examples described in the API schema as the primary source of inputs (and `requires <https://dredd.org/en/latest/how-it-works.html#uri-parameters>`_ them to work) and
generates data only in `some situations <https://dredd.org/en/latest/how-it-works.html#id8>`_.

By using `Hypothesis <https://hypothesis.readthedocs.io/en/latest/>`_ as the underlying testing framework, Schemathesis benefits from all its features like test case reduction and stateful testing.
Dredd works more in a way that requires you to write some sort of example-based tests when Schemathesis requires only a valid API schema and will generate tests for you.

There are a lot of features that Dredd has are Schemathesis has not (e.g., API Blueprint support, that powerful hook system, and many more) and probably vice versa.
Definitely, Schemathesis can learn a lot from Dredd and if you miss any feature that exists in Dredd but doesn't exist in Schemathesis, let us know.

How should I run Schemathesis?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

There are two main ways to run it — as a part of Python's test suite, and as a command-line tool.

If you wrote a Python application and you want to utilize the features of an existing test suite, then the in-code option will best suit your needs.

If you wrote your application in a language other than Python, you should use the built-in CLI. Please keep in mind that you will need to have a running application where you can run Schemathesis against.


Should I always have my application running before starting the test suite?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Yes, except for Python apps that are either built with AioHTTP or implement WSGI (like Flask or Django).

Can I exclude particular data from being generated?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Yes. Schemathesis's hooks mechanism allows you to adapt its behavior and generate data that better fits your use case.

Also, if your application fails on some input early in the code, then it's often a good idea to exclude this input from the next test run so you can explore deeper parts of your codebase.

How can I use database objects IDs in tests?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``case`` object that is injected in each test can be modified, assuming your URL template is ``/api/users/{user_id}`` then in tests, it can be done like this:

.. code:: python

    @schema.parametrize()
    def test_api(case):
        case.path_parameters["user_id"] = 42

Why does Schemathesis fail to parse my API schema generate by FastAPI?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Because FastAPI uses JSON Draft 7 under the hood (via ``pydantic``), which is not compatible with JSON drafts defined by
the Open API 2 / 3.0.x versions. It is a `known issue <https://github.com/tiangolo/fastapi/issues/240>`_ on the FastAPI side.
Schemathesis is more strict in schema handling by default, but we provide optional fixups for this case:

.. code:: python

    import schemathesis

    # will install all available compatibility fixups.
    schemathesis.fixups.install()
    # You can also provide a list of fixup names as the first argument
    # schemathesis.fixups.install(["fastapi"])

For more information, take a look into the "Compatibility" section.

Working with API schemas
------------------------

How to disallow random field names in my schema?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

You need to add ``additionalProperties: false`` to the relevant object definition. But there is a caveat with emulating
inheritance with Open API via ``allOf``.

In this case, it is better to use YAML anchors to share schema parts; otherwise it will prevent valid data from passing the validation.
