import pytest
from hypothesis import given, settings

import schemathesis
from schemathesis.specs.openapi._hypothesis import get_case_strategy


@pytest.fixture
def operation(make_openapi_3_schema):
    schema = make_openapi_3_schema(
        body={
            "required": True,
            "content": {"application/json": {"schema": {"type": "string"}}},
        },
        parameters=[
            {"in": "path", "name": "p1", "required": True, "schema": {"type": "string"}},
            {"in": "header", "name": "h1", "required": True, "schema": {"type": "string"}},
            {"in": "cookie", "name": "c1", "required": True, "schema": {"type": "string"}},
            {"in": "query", "name": "q1", "required": True, "schema": {"type": "string"}},
        ],
    )
    return schemathesis.from_dict(schema)["/users"]["POST"]


@pytest.mark.parametrize(
    "values",
    (
        {"body": "TEST"},
        {"path_parameters": {"p1": "TEST"}},
        {"headers": {"h1": "TEST"}},
        {"cookies": {"c1": "TEST"}},
        {"query": {"q1": "TEST"}},
    ),
)
def test_explicit_attributes(operation, values):
    # When some Case's attribute is passed explicitly to the case strategy
    strategy = get_case_strategy(endpoint=operation, **values)

    @given(strategy)
    @settings(max_examples=1)
    def test(case):
        # Then it should be taken as is
        for attr_name, expected in values.items():
            value = getattr(case, attr_name)
            assert value == expected

    test()
