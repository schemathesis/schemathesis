from unittest.mock import ANY

import pytest

from schemathesis import Case
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
