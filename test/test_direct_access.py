import pytest
from hypothesis import strategies as st

import schemathesis
from schemathesis.generation.meta import CaseMetadata, FuzzingPhaseData, GenerationInfo, PhaseInfo, TestPhase
from schemathesis.generation.modes import GenerationMode
from schemathesis.schemas import APIOperation


def test_contains(swagger_20):
    assert "/users" in swagger_20


def test_getitem(simple_schema):
    swagger = schemathesis.openapi.from_dict(simple_schema)
    assert isinstance(swagger["/users"]["GET"], APIOperation)


def test_len(swagger_20):
    assert len(swagger_20) == 1


def test_iter(swagger_20):
    assert list(swagger_20) == ["/users"]


def test_repr(swagger_20):
    assert str(swagger_20) == "<OpenApiSchema for Sample API 1.0.0>"


@pytest.mark.parametrize("method", ["GET", "get"])
def test_operation_access(swagger_20, method):
    assert isinstance(swagger_20["/users"][method], APIOperation)


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_as_strategy(swagger_20):
    operation = swagger_20["/users"]["GET"]
    strategy = operation.as_strategy()
    assert isinstance(strategy, st.SearchStrategy)
    assert strategy.example() == operation.Case(
        _meta=CaseMetadata(
            generation=GenerationInfo(time=0.0, mode=GenerationMode.POSITIVE),
            components={},
            phase=PhaseInfo(
                name=TestPhase.FUZZING,
                data=FuzzingPhaseData(
                    description="",
                    parameter=None,
                    parameter_location=None,
                    location=None,
                ),
            ),
        ),
    )


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
    schema = schemathesis.openapi.from_dict(raw_schema)
    strategy = schema["/{key}"]["GET"].as_strategy()
    assert isinstance(strategy.example().path_parameters["key"], str)
