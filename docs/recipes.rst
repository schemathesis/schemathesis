Recipes
=======

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
