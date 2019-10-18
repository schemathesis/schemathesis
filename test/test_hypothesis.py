from unittest.mock import ANY

import pytest
from hypothesis import strategies
from hypothesis_jsonschema import from_schema

from schemathesis import Case, register_string_format
from schemathesis._hypothesis import PARAMETERS, get_examples
from schemathesis.models import Endpoint


def _make(cls, default, **kwargs):
    for parameter in PARAMETERS:
        kwargs.setdefault(parameter, default)
    return cls("/users", "GET", **kwargs)


def make_endpoint(**kwargs):
    return _make(Endpoint, {}, **kwargs)


def make_case(**kwargs):
    return _make(Case, ANY, **kwargs)


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


def test_custom_strategies():
    register_string_format("even_4_digits", strategies.from_regex(r"\A[0-9]{4}\Z").filter(lambda x: int(x) % 2 == 0))
    endpoint = make_endpoint(
        **{
            "query": {
                "required": ["id"],
                "type": "object",
                "additionalProperties": False,
                "properties": {"id": {"type": "string", "format": "even_4_digits"}},
            }
        }
    )
    result = strategies.builds(
        Case,
        path=strategies.just(endpoint.path),
        method=strategies.just(endpoint.method),
        query=from_schema(endpoint.query),
    ).example()
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
