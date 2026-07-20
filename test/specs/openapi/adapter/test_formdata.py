from schemathesis.specs.openapi.adapter.formdata import prepare_multipart_v3


def test_content_type_for_undefined_single_property(ctx):
    # When encoding specifies contentType for a field NOT in schema properties
    schema = ctx.openapi.load_schema(
        {
            "/upload": {
                "post": {
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {
                                "schema": {"type": "object", "properties": {}},
                                "encoding": {"field": {"contentType": "text/plain"}},
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.0.0",
    )
    operation = schema["/upload"]["POST"]
    form_data = {"field": "value"}

    files, data = prepare_multipart_v3(operation, form_data)

    assert files == [("field", (None, "value", "text/plain"))]


def test_content_type_for_undefined_array_property(ctx):
    # When encoding specifies contentType for an array field NOT in schema properties
    schema = ctx.openapi.load_schema(
        {
            "/upload": {
                "post": {
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {
                                "schema": {"type": "object", "properties": {}},
                                "encoding": {"items": {"contentType": "image/jpeg"}},
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.0.0",
    )
    operation = schema["/upload"]["POST"]
    form_data = {"items": ["data1", "data2"]}

    files, data = prepare_multipart_v3(operation, form_data)

    assert files == [
        ("items", (None, "data1", "image/jpeg")),
        ("items", (None, "data2", "image/jpeg")),
    ]
