import pytest


def _form_media_types(operation):
    return [parameter.media_type for parameter in operation.body if parameter.location == "body"]


@pytest.mark.parametrize(
    ("consumes", "parameters", "expected"),
    [
        (
            ["application/json"],
            [{"name": "name", "in": "formData", "type": "string", "required": True}],
            ["application/x-www-form-urlencoded"],
        ),
        (
            ["application/xml"],
            [{"name": "name", "in": "formData", "type": "string"}],
            ["application/x-www-form-urlencoded"],
        ),
        (
            ["application/json"],
            [
                {"name": "name", "in": "formData", "type": "string"},
                {"name": "attachment", "in": "formData", "type": "file"},
            ],
            ["multipart/form-data"],
        ),
        (
            ["application/x-www-form-urlencoded"],
            [{"name": "name", "in": "formData", "type": "string"}],
            ["application/x-www-form-urlencoded"],
        ),
        (
            ["multipart/form-data"],
            [{"name": "f", "in": "formData", "type": "file"}],
            ["multipart/form-data"],
        ),
        (
            ["application/json", "application/x-www-form-urlencoded"],
            [{"name": "name", "in": "formData", "type": "string"}],
            ["application/x-www-form-urlencoded"],
        ),
        (
            None,
            [{"name": "name", "in": "formData", "type": "string"}],
            ["multipart/form-data"],
        ),
    ],
    ids=[
        "json-consumes-overridden-to-urlencoded",
        "xml-consumes-overridden-to-urlencoded",
        "json-consumes-with-file-overridden-to-multipart",
        "urlencoded-consumes-unchanged",
        "multipart-consumes-unchanged",
        "mixed-consumes-drops-non-form",
        "no-consumes-defaults-to-multipart",
    ],
)
def test_form_data_media_type_override(ctx, consumes, parameters, expected):
    operation = {"parameters": parameters, "responses": {"200": {"description": "OK"}}}
    if consumes is not None:
        operation["consumes"] = consumes
    schema = ctx.openapi.load_schema({"/op": {"post": operation}}, version="2.0")
    assert _form_media_types(schema["/op"]["POST"]) == expected
