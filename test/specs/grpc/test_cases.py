import base64

import pytest
from google.protobuf import json_format, message_factory
from hypothesis import HealthCheck, given, settings

import schemathesis
from schemathesis.core.control import SkipTest
from schemathesis.generation import GenerationMode
from schemathesis.generation.hypothesis.examples import generate_many
from schemathesis.specs.grpc.formats import grpc_formats
from test.apps.catalog.grpc.descriptors import all_types_descriptor_set
from test.utils import run_given

METHODS = ["Scalars", "Maps", "WellKnown", "Node", "Envelope", "Choice", "Legacy", "Edition"]


@pytest.fixture(scope="module")
def path(tmp_path_factory):
    path = tmp_path_factory.mktemp("grpc") / "api.binpb"
    path.write_bytes(all_types_descriptor_set())
    return path


@pytest.fixture
def schema(path):
    return schemathesis.grpc.from_path(path)


def test_case_shape(schema):
    case = generate_many(schema["types.Types"]["Legacy"].as_strategy(), 1)[0]
    assert (
        case.method,
        case.path,
        case.media_type,
        case.body,
        case.headers,
        case.query,
        case.cookies,
        case.path_parameters,
        case.meta.generation.mode,
    ) == (
        "POST",
        "/types.Types/Legacy",
        "application/grpc",
        {"id": 0, "blob": {}, "label": ""},
        {},
        {},
        {},
        {},
        GenerationMode.POSITIVE,
    )


def test_explicit_body_and_headers(schema):
    strategy = schema["types.Types"]["Edition"].as_strategy(body={"id": 1}, headers={"authorization": "Bearer x"})
    case = generate_many(strategy, 1)[0]
    assert (case.body, case.headers) == ({"id": 1}, {"authorization": "Bearer x"})


def test_body_hooks_apply(schema):
    @schema.hook
    def map_body(context, body):
        return {"id": 7}

    case = generate_many(schema["types.Types"]["Edition"].as_strategy(), 1)[0]
    assert case.body == {"id": 7}


def test_negative_only_skips(schema):
    schema.config.generation.update(modes=[GenerationMode.NEGATIVE])
    strategy = schema["types.Types"]["Edition"].as_strategy(generation_mode=GenerationMode.NEGATIVE)

    @given(strategy)
    @settings(max_examples=1, suppress_health_check=list(HealthCheck))
    def test(case):
        pass

    with pytest.raises(SkipTest, match="Negative test cases are not available for gRPC operations"):
        test()


def test_negative_falls_back_to_positive(schema):
    @run_given(schema["types.Types"]["Edition"].as_strategy(generation_mode=GenerationMode.NEGATIVE), max_examples=1)
    def test(case):
        assert case.meta.generation.mode == GenerationMode.POSITIVE


def test_bytes_use_url_safe_alphabet():
    @run_given(grpc_formats()["byte"], max_examples=50)
    def test(value):
        assert "+" not in value
        assert "/" not in value
        assert base64.urlsafe_b64encode(base64.urlsafe_b64decode(value)).decode() == value


@pytest.mark.parametrize("method", METHODS)
def test_every_positive_value_encodes(schema, method):
    operation = schema["types.Types"][method]
    message = message_factory.GetMessageClass(operation.definition.raw.input_type)

    # Bounds and map keys are rare draws.
    @run_given(case=operation.as_strategy(), max_examples=100)
    def test(case):
        json_format.ParseDict(case.body, message()).SerializeToString()
