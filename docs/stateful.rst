.. _stateful:

Stateful testing
================

By default, Schemathesis generates random data for all endpoints in your schema. With Schemathesis's `stateful testing`
Schemathesis CLI will try to reuse data from requests that were sent and responses received for generating requests to
other endpoints.

Open API Links
--------------

The `official documentation <https://swagger.io/docs/specification/links/>`_ describes this feature like this:

    Using links, you can describe how various values returned by one operation can be used as input for other operations

Schemathesis uses this data to generate additional requests and send them to their respective endpoints.
It enables Schemathesis to reach much deeper into your codebase that it is possible with randomly generated data.
Let's take the example from the docs:

.. code:: yaml

    paths:
      /users:
        post:
          summary: Creates a user and returns the user ID
          operationId: createUser
          requestBody:
            required: true
            description: User object
            content:
              application/json:
                schema:
                  $ref: '#/components/schemas/User'
          responses:
            '201':
              ...
              links:
                GetUserByUserId:
                  operationId: getUser
                  parameters:
                    userId: '$response.body#/id'
      /users/{userId}:
        get:
          summary: Gets a user by ID
          operationId: getUser
          parameters:
            - in: path
              name: userId
              required: true
              schema:
                type: integer
                format: int64

Based on this definition, Schemathesis will:

- Test ``POST /users`` endpoint as usual;
- Each response with 201 code will be parsed and used for additional tests of ``GET /users/{user_id}`` endpoint;
- All data that is not filled from responses will be generated as usual;

In this case, it is much more likely that instead of a 404 response for a randomly-generated ``user_id`` we'll receive
something else - for example, HTTP codes 200 or 500.

By default, stateful testing is disabled. You can add it via ``--stateful=links`` CLI option. Please, note that more
different algorithms for stateful testing might be implemented in the future.

.. code:: bash

    schemathesis run --stateful=links http://0.0.0.0/schema.yaml

    ...

    POST /api/users/ .                                     [ 33%]
        -> GET /api/users/{user_id} .                      [ 50%]
            -> PATCH /api/users/{user_id} .                [ 60%]
        -> PATCH /api/users/{user_id} .                    [ 66%]
    GET /api/users/{user_id} .                             [ 83%]
        -> PATCH /api/users/{user_id} .                    [ 85%]
    PATCH /api/users/{user_id} .                           [100%]

    ...

Each additional test will be indented and prefixed with ``->`` in the CLI output.
You can specify recursive links if you want. The default recursion depth limit is ``5``, it can be changed with
``--stateful-recursion-limit=<N>`` CLI option.

Even though this feature appears only in Open API 3.0 specification, under Open API 2.0, you can use it
via the ``x-links`` extension, the syntax is the same, but you need to use the ``x-links`` keyword instead of ``links``.

The `runtime expressions <https://swagger.io/docs/specification/links/#runtime-expressions>`_ are supported with the
following restriction:

- Symbol ``}`` can not be used as a part of a JSON pointer even though it is a valid symbol.
  This is done due to ambiguity in the runtime expressions syntax, where ``}`` cannot be distinguished from the
  closing bracket of an embedded runtime expression.

**IMPORTANT**. The Open API standard defines ``requestBody`` keyword value in this way:

    A literal value or {expression} to use as a request body when calling the target operation.

This means you cannot use multiple runtime expressions for different parameters, and you always have to provide either a literal
or an expression.
