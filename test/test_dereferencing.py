import pytest

from .utils import as_param, get_schema, integer


@pytest.fixture()
def petstore():
    return get_schema("petstore_v2.yaml")


@pytest.mark.parametrize(
    "ref, expected",
    (
        (
            {"$ref": "#/definitions/Category"},
            {
                "properties": {"id": {"format": "int64", "type": "integer"}, "name": {"type": "string"}},
                "type": "object",
                "xml": {"name": "Category"},
            },
        ),
        (
            {"$ref": "#/definitions/Pet"},
            {
                "properties": {
                    "category": {
                        "properties": {"id": {"format": "int64", "type": "integer"}, "name": {"type": "string"}},
                        "type": "object",
                        "xml": {"name": "Category"},
                    },
                    "id": {"format": "int64", "type": "integer"},
                    "name": {"example": "doggie", "type": "string"},
                    "photoUrls": {
                        "items": {"type": "string"},
                        "type": "array",
                        "xml": {"name": "photoUrl", "wrapped": True},
                    },
                    "status": {
                        "description": "pet status in the store",
                        "enum": ["available", "pending", "sold"],
                        "type": "string",
                    },
                    "tags": {
                        "items": {
                            "properties": {"id": {"format": "int64", "type": "integer"}, "name": {"type": "string"}},
                            "type": "object",
                            "xml": {"name": "Tag"},
                        },
                        "type": "array",
                        "xml": {"name": "tag", "wrapped": True},
                    },
                },
                "required": ["name", "photoUrls"],
                "type": "object",
                "xml": {"name": "Pet"},
            },
        ),
    ),
)
def test_resolve(petstore, ref, expected):
    assert petstore.resolve(ref) == expected


def test_simple_dereference(testdir):
    # When a given parameter contains a JSON reference
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method == "GET"
    assert_int(case.query["id"])
""",
        **as_param({"$ref": "#/definitions/SimpleIntRef"}),
        definitions={"SimpleIntRef": integer(name="id", required=True)},
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_recursive_dereference(testdir):
    # When a given parameter contains a JSON reference, that reference an object with another reference
    testdir.make_test(
        """
@schema.parametrize(method="POST")
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method == "POST"
    assert_int(case.body["id"])
""",
        paths={
            "/users": {
                "post": {
                    "parameters": [
                        {
                            "schema": {"$ref": "#/definitions/ObjectRef"},
                            "in": "body",
                            "name": "object",
                            "required": True,
                        }
                    ]
                }
            }
        },
        definitions={
            "ObjectRef": {
                "required": ["id"],
                "type": "object",
                "additionalProperties": False,
                "properties": {"id": {"$ref": "#/definitions/SimpleIntRef"}},
            },
            "SimpleIntRef": {"type": "integer"},
        },
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_inner_dereference(testdir):
    # When a given parameter contains a JSON reference inside a property of an object
    testdir.make_test(
        """
@schema.parametrize(method="POST")
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method == "POST"
    assert_int(case.body["id"])
""",
        paths={
            "/users": {
                "post": {
                    "parameters": [
                        {
                            "schema": {
                                "type": "object",
                                "required": ["id"],
                                "properties": {"id": {"$ref": "#/definitions/SimpleIntRef"}},
                            },
                            "in": "body",
                            "name": "object",
                            "required": True,
                        }
                    ]
                }
            }
        },
        definitions={"SimpleIntRef": {"type": "integer"}},
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_inner_dereference_with_lists(testdir):
    # When a given parameter contains a JSON reference inside a list in `allOf`
    testdir.make_test(
        """
@schema.parametrize(method="POST")
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method == "POST"
    assert_int(case.body["id"]["a"])
    assert_str(case.body["id"]["b"])
""",
        paths={
            "/users": {
                "post": {
                    "parameters": [
                        {
                            "schema": {
                                "type": "object",
                                "required": ["id"],
                                "properties": {
                                    "id": {"allOf": [{"$ref": "#/definitions/A"}, {"$ref": "#/definitions/B"}]}
                                },
                            },
                            "in": "body",
                            "name": "object",
                            "required": True,
                        }
                    ]
                }
            }
        },
        definitions={
            "A": {"type": "object", "required": ["a"], "properties": {"a": {"type": "integer"}}},
            "B": {"type": "object", "required": ["b"], "properties": {"b": {"type": "string"}}},
        },
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


@pytest.mark.parametrize(
    "nullable, expected",
    (
        (
            {
                "properties": {
                    "id": {"format": "int64", "type": "integer", "x-nullable": True},
                    "name": {"type": "string"},
                },
                "type": "object",
            },
            {
                "properties": {
                    "id": {"anyOf": [{"format": "int64", "type": "integer"}, {"type": "null"}]},
                    "name": {"type": "string"},
                },
                "type": "object",
            },
        ),
        (
            {
                "parameters": [
                    {"name": "id", "in": "query", "type": "integer", "format": "int64", "x-nullable": True},
                    {"name": "name", "type": "string"},
                ]
            },
            {
                "parameters": [
                    {"name": "id", "in": "query", "format": "int64", "anyOf": [{"type": "integer"}, {"type": "null"}]},
                    {"name": "name", "type": "string"},
                ]
            },
        ),
        (
            {
                "properties": {
                    "id": {"type": "string", "enum": ["a", "b"], "x-nullable": True},
                    "name": {"type": "string"},
                },
                "type": "object",
            },
            {
                "properties": {
                    "id": {"anyOf": [{"type": "string", "enum": ["a", "b"]}, {"type": "null"}]},
                    "name": {"type": "string"},
                },
                "type": "object",
            },
        ),
    ),
)
def test_x_nullable(petstore, nullable, expected):
    assert petstore.resolve(nullable) == expected


def test_nullable_parameters(testdir):
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method == "GET"
    assert case.query["id"] is None
""",
        **as_param(integer(name="id", required=True, **{"x-nullable": True})),
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_nullable_properties(testdir):
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method == "GET"
    assert case.query["attributes"]["id"] is None
""",
        **as_param(
            {
                "type": "object",
                "in": "query",
                "name": "attributes",
                "properties": {"id": {"type": "integer", "format": "int64", "x-nullable": True}},
                "required": ["id"],
            }
        ),
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-vv", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_nullable_ref(testdir):
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method == "GET"
    assert case.query["id"] is None
""",
        **as_param({"$ref": "#/definitions/NullableIntRef"}),
        definitions={"NullableIntRef": integer(name="id", required=True, **{"x-nullable": True})},
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_nullable_enum(testdir):
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method == "GET"
    assert case.query["id"] is None
""",
        **as_param(integer(name="id", required=True, enum=[1, 2], **{"x-nullable": True})),
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])
