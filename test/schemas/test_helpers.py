import pytest

from schemathesis.schemas import convert_parameters


@pytest.mark.parametrize(
    "parameters, place, expected",
    (
        (
            [{"schema": {"id": {"type": "integer"}}, "in": "body", "name": "object", "required": True}],
            "body",
            {
                "properties": {"object": {"id": {"type": "integer"}}},
                "required": ["object"],
                "additionalProperties": False,
                "type": "object",
            },
        ),
    ),
)
def test_convert_parameters(parameters, place, expected):
    assert convert_parameters(parameters, place) == expected
