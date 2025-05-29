Authentication
==============

In this section, we'll cover how to use Schemathesis to test APIs that require authentication.
We'll start with the basics of setting authentication credentials manually using headers, cookies, and query strings.
Then, we'll move on to more advanced topics, including HTTP Basic, Digest Authentication, custom authentication mechanisms, and reusing sessions in Python tests.

Built-In Authentication mechanisms
----------------------------------

`HTTP Basic <https://datatracker.ietf.org/doc/html/rfc7617>`_ is supported by Schemathesis out of the box.

In Python tests, you can use the `requests <https://github.com/psf/requests>`_ library to send requests with HTTP Basic or HTTP Digest authentication.
You can pass the authentication credentials using the ``auth`` arguments of the ``call`` or ``call_and_validate`` methods:

.. code-block:: python

    import schemathesis
    from requests.auth import HTTPDigestAuth

    schema = schemathesis.openapi.from_url("https://example.schemathesis.io/openapi.json")


    @schema.parametrize()
    def test_api(case):
        # HTTP Basic
        case.call_and_validate(auth=("user", "password"))
        # HTTP Digest
        case.call_and_validate(auth=HTTPDigestAuth("user", "password"))

.. _custom-auth:

Using in Python tests
~~~~~~~~~~~~~~~~~~~~~

To use your custom authentication mechanism in Python tests, you also need to register it.
The registration process is similar to the global registration for CLI, but instead, you can register your auth implementation at the schema or test level.

The following example shows how to use auth only tests generated via the ``schema`` instance:

.. code:: python

    import schemathesis

    schema = schemathesis.openapi.from_url("https://example.schemathesis.io/openapi.json")


    @schema.auth()
    class MyAuth:
        # Here goes your implementation
        ...

And this one shows auth applied only to the ``test_api`` function:

.. code:: python

    import schemathesis

    schema = schemathesis.openapi.from_url("https://example.schemathesis.io/openapi.json")


    class MyAuth:
        # Here goes your implementation
        ...


    @schema.auth(MyAuth)
    @schema.parametrize()
    def test_api(case):
        ...

WSGI / ASGI support
~~~~~~~~~~~~~~~~~~~

If you are testing a Python app, you might want to use the WSGI / ASGI integrations and get authentication data from your application instance directly.

It could be done by using the ``context`` to get the application instance:

**FastAPI**:

.. code:: python

    from myapp import app
    from starlette_testclient import TestClient

    schema = schemathesis.openapi.from_asgi("/openapi.json", app=app)

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

    schema = schemathesis.openapi.from_wsgi("/openapi.json", app=app)

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

Custom test client in Python tests
----------------------------------

Sometimes you need to reuse the same test client across multiple tests to share authentication data or execute custom events during session startup or shutdown (such as establishing a database connection):

.. code-block:: python

    from myapp import app
    from starlette_testclient import TestClient

    schema = schemathesis.openapi.from_asgi("/openapi.json", app=app)


    @schema.parametrize()
    def test_api(case):
        with TestClient(app) as session:
            case.call_and_validate(session=session)
