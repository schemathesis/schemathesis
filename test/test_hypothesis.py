from base64 import b64decode

import pytest
from hypothesis import HealthCheck, given, settings, strategies

import schemathesis
from schemathesis import Case, register_string_format
from schemathesis._hypothesis import PARAMETERS, get_case_strategy, get_example, is_valid_query
from schemathesis.exceptions import InvalidSchema
from schemathesis.models import Endpoint


def make_endpoint(schema, **kwargs) -> Endpoint:
    return Endpoint("/users", "POST", definition={}, schema=schema, **kwargs)


@pytest.mark.parametrize("name", sorted(PARAMETERS - {"modified_path_parameters", "modified_body"}))
def test_get_examples(name, swagger_20):
    example = {"name": "John"}
    endpoint = make_endpoint(
        swagger_20,
        **{
            name: {
                "required": ["name"],
                "type": "object",
                "additionalProperties": False,
                "properties": {"name": {"type": "string"}},
                "example": example,
            }
        },
    )
    assert get_example(endpoint) == Case(endpoint, **{name: example})


def test_no_body_in_get(swagger_20):
    endpoint = Endpoint(
        path="/api/success",
        method="GET",
        definition={},
        schema=swagger_20,
        query={
            "required": ["name"],
            "type": "object",
            "additionalProperties": False,
            "properties": {"name": {"type": "string"}},
            "example": {"name": "John"},
        },
    )
    assert get_example(endpoint).body is None


def test_invalid_body_in_get(swagger_20):
    endpoint = Endpoint(
        path="/foo",
        method="GET",
        definition={},
        schema=swagger_20,
        body={"required": ["foo"], "type": "object", "properties": {"foo": {"type": "string"}}},
    )
    with pytest.raises(InvalidSchema, match=r"^Body parameters are defined for GET request.$"):
        get_case_strategy(endpoint)


@pytest.mark.hypothesis_nested
def test_invalid_body_in_get_disable_validation(simple_schema):
    schema = schemathesis.from_dict(simple_schema, validate_schema=False)
    endpoint = Endpoint(
        path="/foo",
        method="GET",
        definition={},
        schema=schema,
        body={"required": ["foo"], "type": "object", "properties": {"foo": {"type": "string"}}},
    )
    strategy = get_case_strategy(endpoint)

    @given(strategy)
    @settings(max_examples=1)
    def test(case):
        assert case.body is not None

    test()


def test_warning(swagger_20):
    example = {"name": "John"}
    endpoint = make_endpoint(swagger_20, query={"example": example})
    with pytest.warns(None) as record:
        assert get_example(endpoint) == Case(endpoint, query=example)
    assert not record


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_custom_strategies(swagger_20):
    register_string_format("even_4_digits", strategies.from_regex(r"\A[0-9]{4}\Z").filter(lambda x: int(x) % 2 == 0))
    endpoint = make_endpoint(
        swagger_20,
        query={
            "required": ["id"],
            "type": "object",
            "additionalProperties": False,
            "properties": {"id": {"type": "string", "format": "even_4_digits"}},
        },
    )
    result = get_case_strategy(endpoint).example()
    assert len(result.query["id"]) == 4
    assert int(result.query["id"]) % 2 == 0


def test_register_default_strategies():
    # If schemathesis is imported
    import schemathesis

    # Default strategies should be registered
    from hypothesis_jsonschema._from_schema import STRING_FORMATS

    assert "binary" in STRING_FORMATS
    assert "byte" in STRING_FORMATS


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_default_strategies_binary(swagger_20):
    endpoint = make_endpoint(
        swagger_20,
        form_data={
            "required": ["file"],
            "type": "object",
            "additionalProperties": False,
            "properties": {"file": {"type": "string", "format": "binary"}},
        },
    )
    result = get_case_strategy(endpoint).example()
    assert isinstance(result.form_data["file"], bytes)


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_default_strategies_bytes(swagger_20):
    endpoint = make_endpoint(
        swagger_20,
        body={
            "required": ["byte"],
            "type": "object",
            "additionalProperties": False,
            "properties": {"byte": {"type": "string", "format": "byte"}},
        },
    )
    result = get_case_strategy(endpoint).example()
    assert isinstance(result.body["byte"], str)
    b64decode(result.body["byte"])


@pytest.mark.parametrize(
    "values, error",
    (
        (("valid", "invalid"), f"strategy must be of type {strategies.SearchStrategy}, not {str}"),
        ((123, strategies.from_regex(r"\d")), f"name must be of type {str}, not {int}"),
    ),
)
def test_invalid_custom_strategy(values, error):
    with pytest.raises(TypeError) as exc:
        register_string_format(*values)
    assert error in str(exc.value)


@pytest.mark.hypothesis_nested
def test_valid_headers(base_url, swagger_20):
    endpoint = Endpoint(
        "/api/success",
        "GET",
        definition={},
        schema=swagger_20,
        base_url=base_url,
        headers={
            "properties": {"api_key": {"name": "api_key", "in": "header", "type": "string"}},
            "additionalProperties": False,
            "type": "object",
            "required": ["api_key"],
        },
    )

    @given(case=get_case_strategy(endpoint))
    @settings(suppress_health_check=[HealthCheck.filter_too_much], deadline=None)
    def inner(case):
        case.call()

    inner()


@pytest.mark.parametrize("value, expected", (({"key": "1"}, True), ({"key": 1}, True), ({"key": "\udcff"}, False)))
def test_is_valid_query(value, expected):
    assert is_valid_query(value) == expected


@pytest.mark.hypothesis_nested
def test_is_valid_query_strategy():
    strategy = strategies.sampled_from([{"key": "1"}, {"key": "\udcff"}]).filter(is_valid_query)

    @given(strategy)
    @settings(max_examples=10)
    def test(value):
        assert value == {"key": "1"}

    test()
