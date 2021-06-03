import pytest
from hypothesis import strategies as st
from requests.structures import CaseInsensitiveDict

import schemathesis
from schemathesis import DataGenerationMethod
from schemathesis.models import APIOperation, Case
from schemathesis.schemas import operations_to_dict


def test_contains(swagger_20):
    assert "/users" in swagger_20


def test_getitem(simple_schema, mocker):
    swagger = schemathesis.from_dict(simple_schema)
    mocked = mocker.patch("schemathesis.schemas.operations_to_dict", wraps=operations_to_dict)
    assert "_operations" not in swagger.__dict__
    assert isinstance(swagger["/users"], CaseInsensitiveDict)
    assert mocked.call_count == 1
    # Check cached access
    assert "_operations" in swagger.__dict__
    assert isinstance(swagger["/users"], CaseInsensitiveDict)
    assert mocked.call_count == 1


def test_len(swagger_20):
    assert len(swagger_20) == 1


def test_iter(swagger_20):
    assert list(swagger_20) == ["/users"]


def test_repr(swagger_20):
    assert str(swagger_20) == "SwaggerV20 for Sample API (1.0.0)"


@pytest.mark.parametrize("method", ("GET", "get"))
def test_operation_access(swagger_20, method):
    assert isinstance(swagger_20["/users"][method], APIOperation)


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_as_strategy(swagger_20):
    operation = swagger_20["/users"]["GET"]
    strategy = operation.as_strategy()
    assert isinstance(strategy, st.SearchStrategy)
    assert strategy.example() == Case(operation, data_generation_method=DataGenerationMethod.positive)


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_reference_in_path():
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Blank API", "version": "1.0"},
        "servers": [{"url": "http://localhost/api"}],
        "paths": {
            "/{key}": {
                "get": {
                    "parameters": [{"$ref": "#/components/parameters/PathParameter"}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        "components": {
            "parameters": {
                "PathParameter": {"in": "path", "name": "key", "required": True, "schema": {"type": "string"}}
            }
        },
    }
    schema = schemathesis.from_dict(raw_schema)
    strategy = schema["/{key}"]["GET"].as_strategy()
    assert isinstance(strategy.example().path_parameters["key"], str)
