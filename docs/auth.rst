Authentication
==============

In this section, we'll cover how to use Schemathesis to test APIs that require authentication.
We'll start with the basics of setting authentication credentials manually using headers, cookies, and query strings.
Then, we'll move on to more advanced topics, including HTTP Basic, Digest Authentication, custom authentication mechanisms, and reusing sessions in Python tests.

Setting credentials
-------------------

To set authentication credentials manually, you can pass a key-value pairs to Schemathesis when running tests.
Here's an example command for setting a custom header or cookie using the CLI:

.. code:: text

    st run -H "Authorization: Bearer TOKEN" ...
    st run -H "Cookie: session=SECRET" ...

You can also provide multiple headers by using the ``-H`` option multiple times:

.. code:: text

    st run -H "Authorization: Bearer TOKEN" -H "X-Session-Id: SECRET" ...

.. note::

    Query string authentication is not yet supported in the Schemathesis CLI, however, you can use custom authentication mechanisms to set authentication in a query string parameter.
    Details on how to do this are described in the :ref:`Custom Authentication <custom-auth>` section below.

For Python tests you can set a header, cookie or a query parameter inside your test function:

.. code-block:: python

    import schemathesis

    schema = schemathesis.from_uri("https://example.schemathesis.io/openapi.json")


    @schema.parametrize()
    def test_api(case):
        # Header
        case.call_and_validate(headers={"Authorization": "Bearer TOKEN"})
        # Cookie
        case.call_and_validate(cookies={"session": "SECRET"})
        # Query parameter
        case.call_and_validate(params={"Api-Key": "KEY"})

Built-In Authentication mechanisms
----------------------------------

`HTTP Basic <https://datatracker.ietf.org/doc/html/rfc7617>`_ and `HTTP Digest <https://datatracker.ietf.org/doc/html/rfc7616>`_ are two common authentication schemes supported by Schemathesis out of the box.

.. code:: text

    st run --auth user:pass --auth-type=basic ...
    st run --auth user:pass --auth-type=digest ...

In Python tests, you can use the `requests <https://github.com/psf/requests>`_ library to send requests with HTTP Basic or HTTP Digest authentication.
You can pass the authentication credentials using the ``auth`` arguments of the ``call`` or ``call_and_validate`` methods:

.. code-block:: python

    import schemathesis
    from requests.auth import HTTPDigestAuth

    schema = schemathesis.from_uri("https://example.schemathesis.io/openapi.json")


    @schema.parametrize()
    def test_api(case):
        # HTTP Basic
        case.call_and_validate(auth=("user", "password"))
        # HTTP Digest
        case.call_and_validate(auth=HTTPDigestAuth("user", "password"))

.. _custom-auth:

Custom Authentication
---------------------

In addition to the built-in authentication options, Schemathesis also allows you to implement your own custom authentication mechanisms in Python.
It can be useful if you are working with an API that uses a custom authentication method.
This section will explain how to define custom authentication mechanisms and use them in CLI and Python tests.

Implementation
~~~~~~~~~~~~~~

To implement a custom authentication mechanism, you need to create a Python class with two methods and plug it into Schemathesis.

The two methods your class should contain are:

- ``get``: This method should get the authentication data and return it.
- ``set``: This method should modify the generated test sample so that it contains the authentication data.

Here's an example of a simple custom authentication class. However, please note that this code alone will not work without the necessary registration steps, which will be described later in this section.

.. code:: python

    import requests

    TOKEN_ENDPOINT = "https://example.schemathesis.io/api/token/"
    USERNAME = "demo"
    PASSWORD = "test"


    class MyAuth:
        def get(self, case, context):
            response = requests.post(
                TOKEN_ENDPOINT,
                json={"username": USERNAME, "password": PASSWORD},
            )
            data = response.json()
            return data["access_token"]

        def set(self, case, data, context):
            case.headers = case.headers or {}
            case.headers["Authorization"] = f"Bearer {data}"

The ``get`` method sends a request to a token endpoint and returns the access token retrieved from the JSON response.
The ``set`` method modifies the generated ``Case`` instance so that it contains the authentication data, adding an ``Authorization`` header with the retrieved token.

The ``context`` argument contains a few attributes useful for the authentication process:

- ``context.operation``. API operation that is currently being tested
- ``context.app``. A Python application if the WSGI / ASGI integration is used

Using in CLI
~~~~~~~~~~~~

To use your custom authentication mechanism in the Schemathesis CLI, you need to register it globally. Here's an example of how to do that:

.. code:: python

    import schemathesis


    @schemathesis.auth()
    class MyAuth:
        # Here goes your implementation
        ...

Then put your code into a Python file (for example, ``my_file.py``) and set the ``SCHEMATHESIS_HOOKS`` environment variable to point to it:

.. code:: bash

    SCHEMATHESIS_HOOKS=my_file
    st run http://127.0.0.1/openapi.yaml

That is it! Now Schemathesis will use your custom authentication mechanism for all tests.

.. note::

    The registration process is the same as for any other extension, and you can find more details on how to extend Schemathesis in the :ref:`Extending Schemathesis <enabling-extensions>` section.

Using in Python tests
~~~~~~~~~~~~~~~~~~~~~

To use your custom authentication mechanism in Python tests, you also need to register it.
The registration process is similar to the global registration for CLI, but instead, you can register your auth implementation at the schema or test level.

The following example shows how to use auth only tests generated via the ``schema`` instance:

.. code:: python

    import schemathesis

    schema = schemathesis.from_uri("https://example.schemathesis.io/openapi.json")


    @schema.auth()
    class MyAuth:
        # Here goes your implementation
        ...

And this one shows auth applied only to the ``test_api`` function:

.. code:: python

    import schemathesis

    schema = schemathesis.from_uri("https://example.schemathesis.io/openapi.json")


    class MyAuth:
        # Here goes your implementation
        ...


    @schema.auth(MyAuth)
    @schema.parametrize()
    def test_api(case):
        ...

Conditional Authentication
~~~~~~~~~~~~~~~~~~~~~~~~~~

Schemathesis offers a way to apply authentication to only a specific set of API operations during testing.
This is helpful when you need to test different authentication types for different API operations or when the API has a combination of authenticated and unauthenticated endpoints.

Multiple filters can be combined and applied to include or exclude API operations based on exact values, regular expressions, or custom functions.
Here is how you can apply auth to all API operations with the ``/users/`` path, but exclude the ``POST`` method.

.. code:: python

    import schemathesis


    @schemathesis.auth().apply_to(path="/users/").skip_for(method="POST")
    class MyAuth:
        # Here goes your implementation
        ...


    schema = schemathesis.from_uri("https://example.schemathesis.io/openapi.json")


    @schema.auth(MyAuth).apply_to(path="/users/").skip_for(method="POST")
    @schema.parametrize()
    def test_api(case):
        ...

.. note::

    This decorator syntax is supported only on Python 3.9+. For older Python versions you need to bind separate variables for each term.

Basic rules:

- ``apply_to`` applies authentication to all API operations that match the filter term
- ``skip_for`` skips authentication for all API operations that match the filter term
- All conditions within a filter term are combined with the ``AND`` logic
- Each ``apply_to`` and ``skip_for`` term is combined with the ``OR`` logic
- Both ``apply_to`` and ``skip_for`` use the same set of conditions as arguments

Conditions:

- ``path``: the path of the API operation without its ``basePath``.
- ``method``: the upper-cased HTTP method of the API operation
- ``name``: the name of the API operation, such as ``GET /users/`` or ``Query.getUsers``
- ``tag``: the tag assigned to the API operation. For Open API it comes from the ``tags`` field.
- ``operation_id``: the ID of an API operation. For Open API it comes from the ``operationId`` field.
- Each condition can take either a single string or a list of options as input
- You can also use a regular expression to match the conditions by adding ``_regex`` to the end of the condition and passing a string or a compiled regex.

Here are some examples for ``path``, other conditions works the same:

.. code:: python

    import re
    import schemathesis

    schema = schemathesis.from_uri("https://example.schemathesis.io/openapi.json")


    # Only `/users/`
    @schema.auth().apply_to(path="/users/")
    # Only `/users/` and `/orders/`
    @schema.auth().apply_to(path=["/users/", "/orders/"])
    # Only paths starting with `/u`
    @schema.auth().apply_to(path_regex="^/u")
    # Only paths starting with `/u` case insensitive
    @schema.auth().apply_to(path_regex=re.compile("^/u", re.IGNORECASE))
    # Only `GET /users/` or `POST /orders/`
    @schema.auth().apply_to(
        method="GET",
        path="/users/",
    ).apply_to(
        method="POST",
        path="/orders/",
    )
    class MyAuth:
        # Here goes your implementation
        ...

You can also use a custom function to determine whether to apply or skip authentication for a given operation.
The function should take an ``AuthContext`` instance and return a boolean value.

To use a custom function with ``apply_to`` or ``skip_for``, simply pass it as the first argument. For example:

.. code:: python

    import schemathesis

    schema = schemathesis.from_uri("https://example.schemathesis.io/openapi.json")


    def is_deprecated(ctx):
        return ctx.operation.definition.get("deprecated") is True


    # Skip auth for all deprecated API operations
    @schema.auth().skip_for(is_deprecated)
    class MyAuth:
        # Here goes your implementation
        ...

Refreshing credentials
~~~~~~~~~~~~~~~~~~~~~~

By default, the authentication data from the ``get`` method is cached for a while (300 seconds by default).
To customize the caching behavior, pass the ``refresh_interval`` argument to the ``auth`` / ``register`` / ``apply`` functions.
This parameter specifies the number of seconds for which the authentication data will be cached after a non-cached ``get`` call.
To disable caching completely, set ``refresh_interval`` to None. For example, the following code sets the caching time to 600 seconds:

.. code:: python

    import schemathesis


    @schemathesis.auth(refresh_interval=600)
    class MyAuth:
        # Here goes your implementation
        ...

The default implementation does not use a cache key, but you can provide one to distinguish tokens based on specific criteria.
For instance, you may want separate cache entries for tokens with different OAuth scopes.

.. code:: python

    def get_scopes(context):
        security = context.operation.definition.raw.get("security", [])
        if not security:
            return None
        scopes = security[0][context.operation.get_security_requirements()[0]]
        if not scopes:
            return None
        return frozenset(scopes)

    def cache_by_key(case: Case, context: AuthContext) -> str:
        scopes = get_scopes(context) or []
        return ",".join(scopes)

    @schema.auth(cache_by_key=cache_by_key)
    class OAuth2Bearer:
        ...

WSGI / ASGI support
~~~~~~~~~~~~~~~~~~~

If you are testing a Python app, you might want to use the WSGI / ASGI integrations and get authentication data from your application instance directly.

It could be done by using the ``context`` to get the application instance:

**FastAPI**:

.. code:: python

    from myapp import app
    from starlette_testclient import TestClient

    schema = schemathesis.from_asgi("/openapi.json", app=app)

    TOKEN_ENDPOINT = "/auth/token/"
    USERNAME = "demo"
    PASSWORD = "test"


    @schema.auth()
    class MyAuth:
        def get(self, case, context):
            client = TestClient(context.app)
            response = client.post(
                TOKEN_ENDPOINT, json={"username": USERNAME, "password": PASSWORD}
            )
            return response.json()["access_token"]

        def set(self, case, data, context):
            case.headers = case.headers or {}
            case.headers["Authorization"] = f"Bearer {data}"

**Flask**:

.. code:: python

    from myapp import app
    import werkzeug

    schema = schemathesis.from_wsgi("/openapi.json", app=app)

    TOKEN_ENDPOINT = "/auth/token/"
    USERNAME = "demo"
    PASSWORD = "test"


    @schema.auth()
    class MyAuth:
        def get(self, case, context):
            client = werkzeug.Client(context.app)
            response = client.post(
                TOKEN_ENDPOINT, json={"username": USERNAME, "password": PASSWORD}
            )
            return response.json["access_token"]

        def set(self, case, data, context):
            case.headers = case.headers or {}
            case.headers["Authorization"] = f"Bearer {data}"

Refresh tokens
~~~~~~~~~~~~~~

As auth provider class can hold additional state, you can use it to implement more complex authentication flows.
For example, you can use refresh tokens for authentication.

.. code:: python

    import requests
    import schemathesis

    TOKEN_ENDPOINT = "https://auth.myapp.com/api/token/"
    REFRESH_ENDPOINT = "https://auth.myapp.com/api/refresh/"
    USERNAME = "demo"
    PASSWORD = "test"


    @schemathesis.auth()
    class MyAuth:
        def __init__(self):
            self.refresh_token = None

        def get(self, case, context):
            if self.refresh_token is not None:
                return self.refresh(context)
            return self.login(context)

        def login(self, context):
            response = requests.post(
                TOKEN_ENDPOINT,
                json={"username": USERNAME, "password": PASSWORD},
            )
            data = response.json()
            self.refresh_token = data["refresh_token"]
            return data["access_token"]

        def refresh(self, context):
            response = requests.post(
                REFRESH_ENDPOINT,
                headers={"Authorization": f"Bearer {self.refresh_token}"},
            )
            data = response.json()
            self.refresh_token = data["refresh_token"]
            return data["access_token"]

        def set(self, case, data, context):
            case.headers = case.headers or {}
            case.headers = {"Authorization": f"Bearer {data}"}

Third-party implementation
--------------------------

If you'd like to use an authentication mechanism that is not natively supported by Schemathesis, you can use third-party extensions to the ``requests`` library inside Schemathesis tests.

You can pass a ``requests.auth.AuthBase`` subclass instance to ``auth.set_from_requests`` and Schemathesis will use it automatically for every request it makes during testing.

.. important::

    Note, that this feature works only over HTTP and Python's WSGI transport is not supported.

Here is an example that uses the `requests-ntlm <https://github.com/requests/requests-ntlm>`_ library that supports the `NTLM HTTP Authentication <https://datatracker.ietf.org/doc/html/rfc4559>`_ protocol.

.. code:: python

    import schemathesis
    from requests_ntlm import HttpNtlmAuth

    schemathesis.auth.set_from_requests(HttpNtlmAuth("domain\\username", "password"))

.. note::

    You'll need to load this code as any other hook for CLI.

For Python tests it works similarly:

.. code-block:: python

    import schemathesis
    from requests_ntlm import HttpNtlmAuth

    schema = schemathesis.from_uri("https://example.schemathesis.io/openapi.json")

    schema.auth.set_from_requests(HttpNtlmAuth("domain\\username", "password"))


    @schema.parametrize()
    def test_api(case):
        ...

Custom test client in Python tests
----------------------------------

Sometimes you need to reuse the same test client across multiple tests to share authentication data or execute custom events during session startup or shutdown (such as establishing a database connection):

.. code-block:: python

    from myapp import app
    from starlette_testclient import TestClient

    schema = schemathesis.from_asgi("/openapi.json", app=app)


    @schema.parametrize()
    def test_api(case):
        with TestClient(app) as session:
            case.call_and_validate(session=session)
