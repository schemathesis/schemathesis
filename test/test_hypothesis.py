import pytest
from hypothesis import given, strategies

from schemathesis import Case, register_string_format
from schemathesis._hypothesis import PARAMETERS, get_case_strategy, get_examples
from schemathesis.models import Endpoint


def _make(cls, **kwargs):
    return cls("/users", "GET", **kwargs)


def make_endpoint(**kwargs):
    return _make(Endpoint, **kwargs)


def make_case(**kwargs):
    return _make(Case, **kwargs)


@pytest.mark.parametrize("name", PARAMETERS)
def test_get_examples(name):
    example = {"name": "John"}
    endpoint = make_endpoint(
        **{
            name: {
                "required": ["name"],
                "type": "object",
                "additionalProperties": False,
                "properties": {"name": {"type": "string"}},
                "example": example,
            }
        }
    )
    assert list(get_examples(endpoint)) == [make_case(**{name: example})]


def test_warning():
    example = {"name": "John"}
    endpoint = make_endpoint(**{"query": {"example": example}})
    with pytest.warns(None) as record:
        assert list(get_examples(endpoint)) == [make_case(**{"query": example})]
    assert not record


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_custom_strategies():
    register_string_format("even_4_digits", strategies.from_regex(r"\A[0-9]{4}\Z").filter(lambda x: int(x) % 2 == 0))
    endpoint = make_endpoint(
        query={
            "required": ["id"],
            "type": "object",
            "additionalProperties": False,
            "properties": {"id": {"type": "string", "format": "even_4_digits"}},
        }
    )
    result = get_case_strategy(endpoint).example()
    assert len(result.query["id"]) == 4
    assert int(result.query["id"]) % 2 == 0


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


def test_valid_headers(base_url):
    endpoint = Endpoint(
        "/api/success",
        "GET",
        base_url=base_url,
        headers={
            "properties": {"api_key": {"name": "api_key", "in": "header", "type": "string"}},
            "additionalProperties": False,
            "type": "object",
            "required": ["api_key"],
        },
    )

    @given(case=get_case_strategy(endpoint))
    def inner(case):
        case.call()

    inner()
