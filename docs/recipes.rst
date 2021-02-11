Recipes
=======

Disabling TLS certificate verification
--------------------------------------

Sometimes, during testing, it is needed to disable TLS verification of the service under test.

**CLI**

.. code-block:: text

    schemathesis run http://localhost/schema.json --request-tls-verify

**Python**

.. code-block:: python

    import schemathesis

    schema = schemathesis.from_uri("http://localhost/schema.json")


    @schema.parametrize()
    def test_api(case):
        # If you need `response`
        response = case.call(verify=False)
        # Alternatively if you don't need `response`
        case.call_and_validate(verify=False)

Authentication
--------------

Most of the APIs are not public and require some form of authentication.

**CLI**

Schemathesis CLI accepts ``--auth`` option for Basic Auth:

.. code:: text

    schemathesis run --auth username:$PASSWORD ...

Alternatively, use ``--header`` to set the ``Authorization`` header directly:

.. code:: text

    schemathesis run -H "Authorization: Bearer TOKEN" ...


It is possible to specify more custom headers to be sent with each request. Each header value should be in the ``KEY: VALUE`` format.
You can also provide multiple headers by using the ``-H`` option multiple times:

.. code:: text

    schemathesis run -H "Authorization: ..." -H "X-API-Key: ..."

**Python**

``case.call`` and ``case.call_and_validate`` proxy custom keyword arguments to ``requests.Session.request``. Therefore, you can use ``auth``:

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

Token-based authentication via a separate ``pytest`` fixture:

.. code-block:: python

    import pytest
    import schemathesis

    SCHEMA_URL = "http://localhost/schema.json"
    LOGIN_ENDPOINT = "http://localhost/api/login/"

    schema = schemathesis.from_uri(SCHEMA_URL)


    @pytest.fixture
    def token():
        # Make a login request
        response = requests.post(
            LOGIN_ENDPOINT, json={"login": "test", "password": "password"}
        )
        # Parse the response and extract token
        return response.json()["auth_token"]


    @schema.parametrize()
    def test_api(case, token):
        # `headers` may be `None`, depending on your schema
        case.headers = case.headers or {}
        case.headers["Authorization"] = f"Bearer {token}"
        # Run the usual testing code below
        case.call_and_validate()
