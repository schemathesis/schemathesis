Contributing to Schemathesis
============================

Welcome! We are delighted that you're reading this!

Your feedback and your experience are essential for the project :)

.. contents::
   :depth: 2
   :backlinks: none

Feature requests and feedback
-----------------------------

If you'd like to suggest a feature, feel free to `submit an issue <https://github.com/schemathesis/schemathesis/issues>`_
and:

* Write a simple and descriptive title to identify your suggestion.
* Provide as many details as possible, explain your context, and how the feature should work.
* Explain why this improvement would be useful.
* Keep the scope narrow. It will make it easier to implement.

Report bugs
-----------

Report bugs for Schemathesis in the `issue tracker <https://github.com/schemathesis/schemathesis/issues>`_.

If you are reporting a bug, please:

* Write a simple and descriptive title to identify the problem.
* Describe the exact steps which reproduce the problem in as many details as possible.
* Describe the behavior you observed after following the steps and point out the problem with that behavior.
* Explain which behavior you expected to see instead and why.
* Include Python / Schemathesis versions.

It would be awesome if you can submit a failing test that demonstrates the problem.

Submitting Pull Requests
------------------------

#. Fork the repository.
#. Enable and install `pre-commit <https://pre-commit.com>`_ to ensure style-guides and code checks are followed.
#. Target the ``master`` branch.
#. Follow **PEP-8** for naming and `black <https://github.com/psf/black>`_ for formatting.
#. Tests are run using ``tox``::

    tox -e py37

   The test environment above is usually enough to cover most cases locally.

#. Write an entry to `changelog.rst <https://github.com/schemathesis/schemathesis/blob/master/docs/changelog.rst>`_
#. Format your commit message according to the Conventional Commits `specification <https://www.conventionalcommits.org/en/>`_

For each pull request, we aim to review it as soon as possible.
If you wait a few days without a reply, please feel free to ping the thread by adding a new comment.

Using a local test server
-------------------------

Schemathesis provides a local test server to simplify the development of new features or fixing bugs.
It allows you to configure the generated API schema to reflect various common scenarios of server-side behavior.

To start using it, you need to prepare a virtual environment.

.. code:: bash

    pip install

To start the server, run the following command in your terminal:

.. code:: bash

    ./test_server.sh 8081

It will start the test server on the 8081 port with a simple Open API 2.0 schema.
The local server supports three specs via the ``--spec`` command-line option - ``openapi2``, ``openapi3``, and ``graphql``.

GraphQL
~~~~~~~

This spec serves a simple schema:

.. code:: graphql

    type Author {
      name: String
      books: [Book]
    }

    type Book {
      title: String
      author: Author
    }

    type Query {
      getBooks: [Book]
      getAuthors: [Author]
    }

OpenAPI
~~~~~~~

Both ``openapi2`` and ``openapi3`` expose semantically the same schema with version-specific keywords.
By default, the server will generate an API schema with the following API operations:

- ``GET /api/success`` - returns ``{"success": true}``
- ``GET /api/failure`` - returns 500 with the ``text/plain`` content type
- ``POST /api/payload`` - returns the request's payload
- ``GET /api/get_payload`` - returns the request's payload, but accepts only GET requests
- ``GET /api/multiple_failures`` - returns different response statuses, depending on the provided integer ``id`` parameter. For negative values returns 200 with ``{"result": "OK"}`` payload, 500 if ``id`` is 0, and 504 for positive ``id`` values.
- ``GET /api/slow`` - always returns ``{"slow": true}`` after 100 ms delay
- ``GET /api/path_variable/{key}`` - receives the ``key`` path parameter and unconditionally returns ``{"success": true}``
- ``POST /api/unsatisfiable`` - parameters for this operation are impossible to generate
- ``POST /api/performance`` - depending on the number of "0" in the input value, responds slower and if the input value has more than ten "0", returns 500
- ``GET /api/flaky`` - returns 1:1 ratio of 200/500 responses
- ``GET /api/recursive`` - accepts a recursive structure and responds with a recursive one
- ``GET /api/basic`` - Requires HTTP basic auth (use `test` as username and password)
- ``GET /api/empty`` - Returns an empty response
- ``GET /api/empty_string`` - Returns a response with an empty string as a payload
- ``POST /api/multipart`` - accepts two body parameters as multipart payload
- ``POST /api/upload_file`` - accepts a file and a body parameter
- ``POST /api/form`` - accepts ``application/x-www-form-urlencoded`` payload
- ``POST /api/teapot`` - returns 418 status code that is not listed in the schema
- ``GET /api/text`` - returns ``text/plain`` responses, which are not declared in the schema
- ``GET /api/cp866`` - returns ``text/plain`` responses encoded with CP866. This content type is not expected by the schema
- ``POST /api/text`` - expects payload as ``text/plain``
- ``POST /api/csv`` - expects payload as ``text/csv`` and returns its equivalent in JSON.
- ``GET /api/malformed_json`` - returns malformed JSON with ``application/json`` content type header
- ``GET /api/custom_format`` - accepts a string in the custom "digits" format. This operation is used to verify custom string formats
- ``GET /api/headers`` - returns the passed headers
- ``POST /api/users/`` (``create_user``) - creates a user and stores it in memory. Provides Open API links to the operations below
- ``GET /api/users/{user_id}`` (``get_user``) - returns a user stored in memory
- ``PATCH /api/users/{user_id}`` (``update_user``) - updates a user stored in memory
- ``GET /api/foo:bar`` (``reserved``) - contains ``:`` in its path
- ``GET /api/read_only`` - includes `readOnly` properties in its schema
- ``POST /api/write_only`` - includes `writeOnly` properties in its schema

You can find the complete schema at ``http://127.0.0.1:8081/schema.yaml`` (replace 8081 with the port you chose in the start server command).

There are also few operations with deliberately malformed schemas, that are not included by default:

- ``POST /api/invalid`` - invalid parameter definition. Uses ``int`` instead of ``integer``
- ``GET /api/invalid_response`` - response doesn't conform to the declared schema
- ``GET /api/invalid_path_parameter/{id}`` - the parameter declaration is invalid (``required`` keyword is set to ``false``)
- ``GET /api/missing_path_parameter/{id}`` - the ``id`` parameter is missing

To select only a subset of the operations above, you could use the ``--operations`` command-line option and provide a
list of names separated by a comma. Values in this list are either mentioned in parentheses or are the path part after ``/api/``.
For example, to select the ``GET /api/success``, ``GET /api/path_variable/{key}``, and  ``POST /api/users/`` operations, you can run the following command:

.. code:: bash

    ./test_server.sh 8081 --operations=success,path_variable,create_user

To select all available operations, use ``--operations=all``.

Then you could use CLI against this server:

.. code::

    st run http://127.0.0.1:8081/schema.yaml
    =========================================== Schemathesis test session starts ==========================================
    platform Linux -- Python 3.8.5, schemathesis-2.5.0, hypothesis-5.23.0, hypothesis_jsonschema-0.17.3, jsonschema-3.2.0
    rootdir: /
    hypothesis profile 'default' -> database=DirectoryBasedExampleDatabase('/.hypothesis/examples')
    Schema location: http://127.0.0.1:8081/schema.yaml
    Base URL: http://127.0.0.1:8081/api
    Specification version: Swagger 2.0
    Workers: 1
    Collected API operations: 3

    GET /api/path_variable/{key} .                                              [ 33%]
    GET /api/success .                                                          [ 66%]
    POST /api/users/ .                                                          [100%]

    ======================================================= SUMMARY =======================================================

    Performed checks:
        not_a_server_error                    201 / 201 passed          PASSED

    ================================================== 3 passed in 1.77s ==================================================

Maintainers
-----------

At present, the core developers are:

- Dmitry Dygalo (`@Stranger6667`_)

Preferred communication language
--------------------------------

We prefer to keep all communications in English.

Thanks!

.. _@Stranger6667: https://github.com/Stranger6667
