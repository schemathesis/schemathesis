"""Tests for parsing of non-payload parameters."""

from schemathesis.parameters import ParameterSet
from schemathesis.specs.openapi.parameters import OpenAPI20Parameter, OpenAPI30Parameter


def test_headers_open_api_2(assert_parameters, make_openapi_2_schema):
    header = {"in": "header", "name": "id", "required": True, "type": "string"}
    schema = make_openapi_2_schema([], [header])
    assert_parameters(schema, ParameterSet([OpenAPI20Parameter(definition=header)]), [{"type": "string"}], "headers")


def test_headers_open_api_3(assert_parameters, make_openapi_3_schema):
    # It is possible to omit "type" in the "schema" keyword
    header = {"in": "header", "name": "id", "required": True, "schema": {}}
    schema = make_openapi_3_schema(parameters=[header])
    # Schemathesis enforces `type=string` for headers
    assert_parameters(schema, ParameterSet([OpenAPI30Parameter(definition=header)]), [{"type": "string"}], "headers")
