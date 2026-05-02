import pytest

import schemathesis
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.result import Err


def _schema(servers):
    return {
        "openapi": "3.0.0",
        "info": {"title": "T", "version": "1"},
        "servers": servers,
        "paths": {"/x": {"get": {"responses": {"200": {"description": "OK"}}}}},
    }


@pytest.mark.parametrize(
    "servers",
    [
        [{}],
        [{"url": None}],
        [{"url": 42}],
        [{"url": "{var}", "variables": None}],
        [{"url": "{var}", "variables": {"var": "x"}}],
        [{"url": "{var}", "variables": {"var": {}}}],
        [None],
        ["http://x"],
        "not-a-list",
        {"url": "http://x"},
        [{"url": "http://x/{undefined}"}],
    ],
    ids=[
        "missing_url",
        "url_none",
        "url_non_string",
        "variables_none",
        "variables_string_value",
        "variable_missing_default",
        "server_none",
        "server_string",
        "servers_string",
        "servers_dict",
        "url_undefined_variable",
    ],
)
def test_invalid_servers_v3(servers):
    with pytest.raises(InvalidSchema):
        schema = schemathesis.openapi.from_dict(_schema(servers))
        assert schema.base_path


def _swagger_schema(**overrides):
    base = {
        "swagger": "2.0",
        "info": {"title": "T", "version": "1"},
        "host": "api.example.com",
        "basePath": "/v1",
        "schemes": ["https"],
        "paths": {"/x": {"get": {"responses": {"200": {"description": "OK"}}}}},
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "overrides",
    [
        {"basePath": None},
        {"basePath": 42},
        {"basePath": ["/v1"]},
    ],
    ids=[
        "basePath_none",
        "basePath_int",
        "basePath_list",
    ],
)
def test_invalid_base_path_v2(overrides):
    schema = schemathesis.openapi.from_dict(_swagger_schema(**overrides))
    with pytest.raises(InvalidSchema):
        assert schema.base_path


@pytest.mark.parametrize(
    "overrides",
    [
        {"parameters": None},
        {"parameters": [None]},
        {"parameters": ["not-a-dict"]},
    ],
    ids=[
        "parameters_none",
        "parameter_none",
        "parameter_string",
    ],
)
def test_invalid_parameters_v2(overrides):
    base = _swagger_schema()
    base["paths"]["/x"]["get"].update(overrides)
    schema = schemathesis.openapi.from_dict(base)
    results = list(schema.get_all_operations())
    assert results
    for result in results:
        assert isinstance(result, Err)
        assert isinstance(result.err(), InvalidSchema)


@pytest.mark.parametrize(
    "parameters",
    [None, [None], ["not-a-dict"], [42]],
    ids=["parameters_none", "parameter_none", "parameter_string", "parameter_int"],
)
def test_invalid_parameters_v3(parameters):
    schema = schemathesis.openapi.from_dict(
        {
            "openapi": "3.0.0",
            "info": {"title": "T", "version": "1"},
            "paths": {"/x": {"get": {"parameters": parameters, "responses": {"200": {"description": "OK"}}}}},
        }
    )
    results = list(schema.get_all_operations())
    assert results
    for result in results:
        assert isinstance(result, Err)
        assert isinstance(result.err(), InvalidSchema)
