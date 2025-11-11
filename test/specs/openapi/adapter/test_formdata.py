import schemathesis
from schemathesis.specs.openapi.adapter.formdata import prepare_multipart_v3


def test_content_type_for_undefined_single_property():
    # When encoding specifies contentType for a field NOT in schema properties
    schema = schemathesis.openapi.from_dict(
        {
            "openapi": "3.0.0",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
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
        }
    )
    operation = schema["/upload"]["POST"]
    form_data = {"field": "value"}

    files, data = prepare_multipart_v3(operation, form_data)

    assert files is not None
    assert len(files) == 1
    name, file_tuple = files[0]
    assert name == "field"
    assert len(file_tuple) == 3
    assert file_tuple[0] is None
    assert file_tuple[1] == "value"
    assert file_tuple[2] == "text/plain"


def test_content_type_for_undefined_array_property():
    # When encoding specifies contentType for an array field NOT in schema properties
    schema = schemathesis.openapi.from_dict(
        {
            "openapi": "3.0.0",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
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
        }
    )
    operation = schema["/upload"]["POST"]
    form_data = {"items": ["data1", "data2"]}

    files, data = prepare_multipart_v3(operation, form_data)

    assert files is not None
    assert len(files) == 2
    for name, file_tuple in files:
        assert name == "items"
        assert len(file_tuple) == 3
        assert file_tuple[0] is None
        assert file_tuple[1] in ["data1", "data2"]
        assert file_tuple[2] == "image/jpeg"
