Recipes
=======

Disabling TLS certificate verification
--------------------------------------

Sometimes, during testing, it is needed to disable TLS verification of the service under test.

**CLI**

.. code-block:: text

    st run http://localhost/schema.json --request-tls-verify

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

It is possible to use an unencrypted certificate (e.g. PEM) by using the ``--request-cert /file/path/example.pem`` argument.

.. code-block:: text

    st run --request-cert client.pem ...


It is also possible to provide the certificate and the private key separately with the usage of the ``--request-cert-key /file/path/example.key`` argument.

.. code-block:: text

    st run --request-cert client.crt --request-cert-key client.key ...

Using an HTTP(S) proxy
----------------------

Sometimes you need to send your traffic to some other tools. You could set up a proxy via the following env variables:

.. code-block:: bash

    $ export HTTP_PROXY="http://10.10.1.10:3128"
    $ export HTTPS_PROXY="http://10.10.1.10:1080"
    $ st run http://localhost/schema.json

Per-route request timeouts
--------------------------

Different API operations may need different timeouts during testing. You could achieve it this way:

.. code-block:: python

    import schemathesis

    DEFAULT_TIMEOUT = 10  # in seconds
    SCHEMA_URL = "http://localhost/schema.json"
    schema = schemathesis.from_uri(SCHEMA_URL)


    @schema.parametrize()
    def test_api(case):
        key = (
            case.operation.method.upper(),
            case.operation.path,
        )
        timeout = {
            ("GET", "/users"): 5,
            # and so on
        }.get(key, DEFAULT_TIMEOUT)
        case.call_and_validate(timeout=timeout)

In the example above, the default timeout is 10 seconds, but for `GET /users` it will be 5 seconds.
