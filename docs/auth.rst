Authentication
==============

There are many ways one may authenticate in an API, and Schemathesis provides flexible support for custom authentication methods.

Static
------

If the API authentication credentials do not change over time, you can use them via CLI options or in Python tests directly.

**CLI**

Schemathesis CLI accepts ``--auth`` option for Basic Auth:

.. code:: text

    st run --auth username:$PASSWORD ...

Alternatively, use ``--header`` to set the ``Authorization`` header directly:

.. code:: text

    st run -H "Authorization: Bearer TOKEN" ...


It is possible to specify more custom headers to be sent with each request. Each header value should be in the ``KEY: VALUE`` format.
You can also provide multiple headers by using the ``-H`` option multiple times:

.. code:: text

    st run -H "Authorization: ..." -H "X-API-Key: ..."

**Python**

``case.call`` and ``case.call_and_validate`` proxy custom keyword arguments to ``requests.Session.request``. Therefore, you can use ``auth`` or ``headers``:

.. code-block:: python

    import schemathesis

    SCHEMA_URL = "http://localhost/schema.json"

    schema = schemathesis.from_uri(SCHEMA_URL)


    @schema.parametrize()
    def test_api(case):
        # If you need `response`
        response = case.call(auth=("user", "password"))
        # Alternatively if you don't need `response`
        case.call_and_validate(auth=("user", "password"))
        # Or custom headers
        case.call_and_validate(headers={"Authorization": "Bearer <MY-TOKEN>"})

Dynamic
-------

You need to create a Python class with two methods and plug it into Schemathesis. This class will work the same way with CLI and the ``pytest`` integration.
Authentication supports Open API and GraphQL without any difference in setup.

It should have two methods:

- ``get``. Get the authentication data and return it from the method.
- ``set``. Modify the generated ``Case`` instance so it contains the authentication data.

The basic version of such a class might look like this:

.. code:: python

    import requests

    # Some details are skipped in this example
    class TokenAuth:
        def get(self, context):
            # This is a real endpoint, try it out!
            response = requests.post(
                "https://example.schemathesis.io/api/token/",
                json={"username": "demo", "password": "test"},
            )
            data = response.json()
            return data["access_token"]

        def set(self, case, data, context):
            # Modify `case` the way you need
            case.headers = {"Authorization": f"Bearer {data}"}

The ``context`` argument contains a few useful attributes and represents the state relevant for the authentication process:

- ``operation``. API operation that is currently being processed.
- ``app``. A Python application if the WSGI / ASGI integration is used.

Depending on the level of granularity you need in your tests, you use this class in multiple ways.

**Globally**

.. code:: python

    import schemathesis


    @schemathesis.auth.register()
    class Auth:
        ...

This auth will be used with every generated test case. If you use CLI, then it is the way to go.

.. note::

    You can take a look at how to extend CLI :ref:`here <extend-cli>`

**Schema**

.. code:: python

    import schemathesis

    schema = schemathesis.from_uri(...)


    @schema.auth.register()
    class Auth:
        ...

This one will work only for tests generated via the ``schema`` instance.

**Test**

.. code:: python

    import schemathesis

    schema = schemathesis.from_uri(...)


    class Auth:
        ...


    @schema.auth.apply(Auth)
    @schema.parametrize()
    def test_api(case):
        ...

Auth will be used only for the ``test_api`` function.

Refresh interval
~~~~~~~~~~~~~~~~

By default, the authentication data from the ``get`` method is cached for a while (300 seconds by default).
To change this, use the ``refresh_interval`` argument in the ``register`` / ``apply`` functions.
It expects the number of seconds for which the results will be cached after a non-cached ``get`` call. Use ``None`` to disable it completely.

.. code:: python

    import schemathesis


    @schemathesis.auth.register(refresh_interval=600)
    class Auth:
        ...


WSGI / ASGI support
~~~~~~~~~~~~~~~~~~~

If you are testing a Python app, you might want to use the WSGI / ASGI integrations and get authentication data from your application instance directly.

It could be done by using the ``context`` to get the application instance:

**FastAPI**:

.. code:: python

    from myapp import app
    from starlette.testclient import TestClient

    schema = schemathesis.from_asgi("/openapi.json", app=app)


    @schema.auth.register()
    class Auth:
        def get(self, context):
            client = TestClient(context.app)
            response = client.post(
                "/auth/token/", json={"username": "test", "password": "pass"}
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


    @schema.auth.register()
    class Auth:
        def get(self, context):
            client = werkzeug.Client(context.app)
            response = client.post(
                "/auth/token/", json={"username": "test", "password": "pass"}
            )
            return response.json["access_token"]

        def set(self, case, data, context):
            case.headers = case.headers or {}
            case.headers["Authorization"] = f"Bearer {data}"

Additional state
~~~~~~~~~~~~~~~~

As auth provider class can hold additional state, you can use it to implement more complex authentication flows.
For example, you can use refresh tokens for authentication.

.. code:: python

    import requests
    import schemathesis


    @schemathesis.auth.register()
    class TokenAuth:
        def __init__(self):
            self.refresh_token = None

        def get(self, context):
            if self.refresh_token is not None:
                return self.refresh(context)
            return self.login(context)

        def login(self, context):
            response = requests.post(
                "https://auth.myapp.com/api/token/",
                json={"username": "demo", "password": "test"},
            )
            data = response.json()
            self.refresh_token = data["refresh_token"]
            return data["access_token"]

        def refresh(self, context):
            response = requests.post(
                "https://auth.myapp.com/api/refresh/",
                headers={"Authorization": f"Bearer {self.refresh_token}"},
            )
            data = response.json()
            self.refresh_token = data["refresh_token"]
            return data["access_token"]

        def set(self, case, data, context):
            # Modify `case` the way you need
            case.headers = {"Authorization": f"Bearer {data}"}
