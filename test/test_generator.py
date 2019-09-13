from unittest.mock import ANY

import pytest

from schemathesis.generator import PARAMETERS, Case, get_examples
from schemathesis.schemas import Endpoint


def test_path(case_factory):
    case = case_factory(path="/users/{name}", path_parameters={"name": "test"})
    assert case.formatted_path == "/users/test"


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
