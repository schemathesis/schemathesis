Recipes
=======

Per-route request timeouts
--------------------------

Different API operations may need different timeouts during testing. You could achieve it this way:

.. code-block:: python

    import schemathesis

    DEFAULT_TIMEOUT = 10  # in seconds
    SCHEMA_URL = "http://127.0.0.1/schema.json"
    schema = schemathesis.openapi.from_uri(SCHEMA_URL)


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

Generating only required parameters
-----------------------------------

Sometimes you don't need to generate all parameters for your API, and want to limit Schemathesis to only required ones.
You can do it with the following hook:

.. code-block:: python

    import schemathesis


    @schemathesis.hook
    def before_init_operation(context, operation):
        for parameter in operation.iter_parameters():
            schema = parameter.definition.get("schema", {})
            transform(schema, drop_optional_properties)
        for alternative in operation.body:
            schema = alternative.definition.get("schema", {})
            transform(schema, drop_optional_properties)


    def transform(schema, callback):
        if isinstance(schema, dict):
            schema = callback(schema)
            for key, sub_item in schema.items():
                schema[key] = transform(sub_item, callback)
        elif isinstance(schema, list):
            schema = [transform(sub_item, callback) for sub_item in schema]
        return schema


    def drop_optional_properties(schema):
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        for name in list(properties):
            if name not in required:
                del properties[name]
        return schema

This hook will remove all optional properties from the parsed API operations.
