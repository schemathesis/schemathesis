"""Tests for behavior not specific to forms."""

import pytest

import schemathesis
from schemathesis.parameters import PayloadAlternatives
from schemathesis.specs.openapi.parameters import OpenAPI20Body, OpenAPI30Body


@pytest.mark.parametrize(
    "consumes",
    (
        ["application/json"],
        # Multiple values in "consumes" implies multiple payload variants
        ["application/json", "application/xml"],
    ),
)
def test_payload_open_api_2(
    consumes,
    assert_parameters,
    make_openapi_2_schema,
    open_api_2_user_form_with_file_parameters,
    open_api_2_user_in_body,
    user_jsonschema,
):
    # A single "body" parameter is used for all payload variants
    schema = make_openapi_2_schema(consumes, [open_api_2_user_in_body])
    assert_parameters(
        schema,
        PayloadAlternatives(
            [OpenAPI20Body(definition=open_api_2_user_in_body, media_type=value) for value in consumes]
        ),
        # For each one the schema is extracted from the parameter definition and transformed to the proper JSON Schema
        [user_jsonschema] * len(consumes),
    )


@pytest.mark.parametrize(
    "media_types",
    (
        ["application/json"],
        # Each media type corresponds to a payload variant
        ["application/json", "application/xml"],
        # Forms can be also combined
        ["application/x-www-form-urlencoded", "multipart/form-data"],
    ),
)
def test_payload_open_api_3(media_types, assert_parameters, make_openapi_3_schema, open_api_3_user, user_jsonschema):
    schema = make_openapi_3_schema(
        {
            "required": True,
            "content": {media_type: {"schema": open_api_3_user} for media_type in media_types},
        }
    )
    assert_parameters(
        schema,
        PayloadAlternatives(
            [
                OpenAPI30Body(definition={"schema": open_api_3_user}, media_type=media_type, required=True)
                for media_type in media_types
            ]
        ),
        # The converted schema should correspond the schema in the relevant "requestBody" part
        # In this case they are the same
        [user_jsonschema] * len(media_types),
    )


def test_parameter_set_get(make_openapi_3_schema):
    header = {"in": "header", "name": "id", "required": True, "schema": {}}
    raw_schema = make_openapi_3_schema(parameters=[header])
    schema = schemathesis.from_dict(raw_schema)
    headers = schema["/users"]["POST"].headers
    assert "id" in headers
    assert headers.contains("id")
    assert not headers.contains("foo")
    assert "foo" not in headers
