"""Tests for parsing of parameters related to forms."""

import pytest

from schemathesis.parameters import PayloadAlternatives
from schemathesis.specs.openapi.parameters import OpenAPI20CompositeBody, OpenAPI20Parameter, OpenAPI30Body


@pytest.mark.parametrize(
    "consumes",
    (
        ["application/x-www-form-urlencoded"],
        ["application/x-www-form-urlencoded", "multipart/form-data"],
    ),
)
def test_forms_open_api_2(
    consumes, assert_parameters, make_openapi_2_schema, user_jsonschema, open_api_2_user_form_parameters
):
    # In Open API 2.0, forms are separate "formData" parameters
    schema = make_openapi_2_schema(consumes, open_api_2_user_form_parameters)
    assert_parameters(
        schema,
        PayloadAlternatives(
            [
                # They are represented as a single "composite" body for each media type
                OpenAPI20CompositeBody(
                    definition=[OpenAPI20Parameter(parameter) for parameter in open_api_2_user_form_parameters],
                    media_type=value,
                )
                for value in consumes
            ]
        ),
        # Each converted schema should correspond to the default User schema.
        [user_jsonschema] * len(consumes),
    )


@pytest.mark.parametrize(
    "consumes",
    (
        ["multipart/form-data"],
        # When "consumes" is not defined, then multipart is the default media type for "formData" parameters
        [],
    ),
)
def test_multipart_form_open_api_2(
    consumes,
    assert_parameters,
    make_openapi_2_schema,
    user_jsonschema_with_file,
    open_api_2_user_form_with_file_parameters,
):
    # Multipart forms are represented as a list of "formData" parameters
    schema = make_openapi_2_schema(consumes, open_api_2_user_form_with_file_parameters)
    assert_parameters(
        schema,
        PayloadAlternatives(
            [
                # Is represented with a "composite" body
                OpenAPI20CompositeBody(
                    definition=[
                        OpenAPI20Parameter(parameter) for parameter in open_api_2_user_form_with_file_parameters
                    ],
                    media_type="multipart/form-data",
                )
            ]
        ),
        [user_jsonschema_with_file],
    )


def test_urlencoded_form_open_api_3(assert_parameters, make_openapi_3_schema, open_api_3_user, user_jsonschema):
    # A regular urlencoded form in Open API 3
    schema = make_openapi_3_schema(
        {
            "required": True,
            "content": {"application/x-www-form-urlencoded": {"schema": open_api_3_user}},
        }
    )
    assert_parameters(
        schema,
        PayloadAlternatives(
            [
                OpenAPI30Body(
                    definition={"schema": open_api_3_user},
                    media_type="application/x-www-form-urlencoded",
                    required=True,
                )
            ]
        ),
        # It should correspond to the default User schema
        [user_jsonschema],
    )


def test_loose_urlencoded_form_open_api_3(assert_parameters, make_openapi_3_schema, make_user_schema, user_jsonschema):
    # The schema doesn't define "type": "object"
    loose_schema = {"schema": make_user_schema(is_loose=True, middle_name={"type": "string", "nullable": True})}
    schema = make_openapi_3_schema(
        {
            "required": True,
            "content": {"application/x-www-form-urlencoded": loose_schema},
        }
    )
    assert_parameters(
        schema,
        PayloadAlternatives(
            [OpenAPI30Body(definition=loose_schema, media_type="application/x-www-form-urlencoded", required=True)]
        ),
        # But when it is converted to JSON Schema, Schemathesis sets `type` to `object`
        # Therefore it corresponds to the default JSON Schema defined for a User
        [user_jsonschema],
    )


def test_multipart_form_open_api_3(
    assert_parameters, make_openapi_3_schema, user_jsonschema_with_file, open_api_3_user_with_file
):
    # A regular multipart for with a file upload in Open API 3
    schema = make_openapi_3_schema(
        {
            "required": True,
            "content": {"multipart/form-data": {"schema": open_api_3_user_with_file}},
        }
    )
    assert_parameters(
        schema,
        PayloadAlternatives(
            [
                OpenAPI30Body(
                    definition={"schema": open_api_3_user_with_file}, media_type="multipart/form-data", required=True
                )
            ]
        ),
        # When converted, the schema corresponds the default schema defined for a User object with a file
        [user_jsonschema_with_file],
    )
