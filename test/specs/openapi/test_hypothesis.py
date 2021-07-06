import pytest
from hypothesis import assume, given, settings

import schemathesis
from schemathesis.specs.openapi._hypothesis import get_case_strategy, is_valid_header, make_positive_strategy


@pytest.fixture
def operation(make_openapi_3_schema):
    schema = make_openapi_3_schema(
        body={
            "required": True,
            "content": {"application/json": {"schema": {"type": "string"}}},
        },
        parameters=[
            {"in": "path", "name": "p1", "required": True, "schema": {"type": "string", "enum": ["FOO"]}},
            {"in": "header", "name": "h1", "required": True, "schema": {"type": "string", "enum": ["FOO"]}},
            {"in": "cookie", "name": "c1", "required": True, "schema": {"type": "string", "enum": ["FOO"]}},
            {"in": "query", "name": "q1", "required": True, "schema": {"type": "string", "enum": ["FOO"]}},
        ],
    )
    return schemathesis.from_dict(schema)["/users"]["POST"]


@pytest.mark.parametrize(
    "values, expected",
    (
        ({"body": "TEST"}, {"body": "TEST"}),
        ({"path_parameters": {"p1": "TEST"}}, {"path_parameters": {"p1": "TEST"}}),
        ({"path_parameters": {}}, {"path_parameters": {"p1": "FOO"}}),
        ({"headers": {"h1": "TEST"}}, {"headers": {"h1": "TEST"}}),
        ({"headers": {}}, {"headers": {"h1": "FOO"}}),
        # Even if the explicit value does not match the schema, it should appear in the output
        ({"headers": {"invalid": "T"}}, {"headers": {"h1": "FOO", "invalid": "T"}}),
        ({"cookies": {"c1": "TEST"}}, {"cookies": {"c1": "TEST"}}),
        ({"cookies": {}}, {"cookies": {"c1": "FOO"}}),
        ({"query": {"q1": "TEST"}}, {"query": {"q1": "TEST"}}),
        ({"query": {}}, {"query": {"q1": "FOO"}}),
    ),
)
def test_explicit_attributes(operation, values, expected):
    # When some Case's attribute is passed explicitly to the case strategy
    strategy = get_case_strategy(operation=operation, **values)

    @given(strategy)
    @settings(max_examples=1)
    def test(case):
        # Then it should appear in the final result
        for attr_name, expected_values in expected.items():
            value = getattr(case, attr_name)
            assert value == expected_values

    test()


@pytest.fixture
def deeply_nested_schema(empty_open_api_3_schema):
    empty_open_api_3_schema["paths"] = {
        "/data": {
            "get": {
                "parameters": [
                    {
                        "name": "key",
                        "in": "query",
                        "required": True,
                        "schema": {
                            # In the end it will be replaced with "#/components/schemas/bar"
                            "$ref": "#/components/schemas/foo1"
                        },
                    }
                ],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }
    empty_open_api_3_schema["components"] = {
        "schemas": {
            "foo1": {"$ref": "#/components/schemas/foo2"},
            "foo2": {"$ref": "#/components/schemas/foo3"},
            "foo3": {"$ref": "#/components/schemas/foo4"},
            "foo4": {"$ref": "#/components/schemas/foo5"},
            "foo5": {"$ref": "#/components/schemas/foo6"},
            "foo6": {"$ref": "#/components/schemas/bar"},
            "bar": {
                "type": "string",
            },
        }
    }
    return empty_open_api_3_schema


def test_missed_ref(deeply_nested_schema):
    # See GH-1167
    # When not resolved references are present in the schema during constructing a strategy
    schema = schemathesis.from_dict(deeply_nested_schema)

    @given(schema["/data"]["GET"].as_strategy())
    @settings(max_examples=10)
    def test(case):
        # Then the reference should be correctly resolved
        assert isinstance(case.query["key"], str)

    test()


def test_inlined_definitions(deeply_nested_schema):
    # See GH-1162
    # When not resolved references are present in the schema during constructing a strategy
    # And the referenced schema contains Open API specific keywords
    deeply_nested_schema["components"]["schemas"]["bar"]["nullable"] = True

    schema = schemathesis.from_dict(deeply_nested_schema)

    @given(schema["/data"]["GET"].as_strategy())
    @settings(max_examples=1)
    def test(case):
        # Then the referenced schema should be properly transformed to the JSON Schema form
        assume(case.query["key"] == "null")

    test()


@pytest.mark.parametrize("keywords", ({}, {"pattern": r"\A[A-F0-9]{12}\Z"}))
@pytest.mark.hypothesis_nested
def test_valid_headers(keywords):
    # When headers are generated
    # And there is no other keywords than "type"
    strategy = make_positive_strategy(
        {
            "type": "object",
            "properties": {"X-Foo": {"type": "string", **keywords}},
            "required": ["X-Foo"],
            "additionalProperties": False,
        },
        "GET /users/",
        "header",
        None,
    )

    @given(strategy)
    def test(headers):
        # Then headers are always valid
        assert is_valid_header(headers)

    test()


@pytest.mark.hypothesis_nested
def test_no_much_filtering_in_headers():
    # When headers are generated
    # And there are keywords other than "type"
    strategy = make_positive_strategy(
        {
            "type": "object",
            "properties": {"X-Foo": {"type": "string", "minLength": 12, "maxLength": 12}},
            "required": ["X-Foo"],
            "additionalProperties": False,
        },
        "GET /users/",
        "header",
        None,
    )

    @given(strategy)
    def test(_):
        # Then there should be no failed health checks
        pass

    test()
