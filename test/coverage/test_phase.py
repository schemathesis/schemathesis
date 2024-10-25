from unittest.mock import ANY

import pytest
from hypothesis import Phase, settings

import schemathesis
from schemathesis import experimental
from schemathesis._hypothesis import create_test
from schemathesis.constants import NOT_SET
from schemathesis.experimental import COVERAGE_PHASE
from schemathesis.generation._methods import DataGenerationMethod
from schemathesis.models import TestPhase
from schemathesis.specs.openapi.constants import LOCATION_TO_CONTAINER
from test.utils import assert_requests_call


@pytest.fixture(autouse=True)
def with_phase():
    COVERAGE_PHASE.enable()


POSITIVE_CASES = [
    {"headers": {"h1": "5", "h2": "000"}, "query": {"q1": 5, "q2": "0000"}, "body": {"j-prop": 0}},
    {"headers": {"h1": "5", "h2": "000"}, "query": {"q1": 6, "q2": "000"}, "body": {"j-prop": 0}},
    {"headers": {"h1": "5", "h2": "00"}, "query": {"q1": 5, "q2": "000"}, "body": {"j-prop": 0}},
    {"headers": {"h1": "4", "h2": "000"}, "query": {"q1": 5, "q2": "000"}, "body": {"j-prop": 0}},
    {"headers": {"h1": "5", "h2": "000"}, "query": {"q1": 5, "q2": "000"}, "body": {"x-prop": ""}},
    {"headers": {"h1": "5", "h2": "000"}, "query": {"q1": 5, "q2": "000"}, "body": {"j-prop": 0}},
]
NEGATIVE_CASES = [
    {"query": {"q1": ANY}, "headers": {"h1": ANY, "h2": "0"}, "body": 0},
    {"query": {"q2": 0}, "headers": {"h1": ANY, "h2": "0"}, "body": 0},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": ANY}, "body": 0},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h2": "0"}, "body": 0},
    {"query": {"q1": ANY, "q2": "00"}, "headers": {"h1": ANY, "h2": "0"}, "body": 0},
    {"query": {"q1": ANY, "q2": {}}, "headers": {"h1": ANY, "h2": "0"}, "body": 0},
    {"query": {"q1": ANY, "q2": []}, "headers": {"h1": ANY, "h2": "0"}, "body": 0},
    {"query": {"q1": ANY, "q2": None}, "headers": {"h1": ANY, "h2": "0"}, "body": 0},
    {"query": {"q1": 4, "q2": 0}, "headers": {"h1": ANY, "h2": "0"}, "body": 0},
    {"query": {"q1": {}, "q2": 0}, "headers": {"h1": ANY, "h2": "0"}, "body": 0},
    {"query": {"q1": [], "q2": 0}, "headers": {"h1": ANY, "h2": "0"}, "body": 0},
    {"query": {"q1": "", "q2": 0}, "headers": {"h1": ANY, "h2": "0"}, "body": 0},
    {"query": {"q1": None, "q2": 0}, "headers": {"h1": ANY, "h2": "0"}, "body": 0},
    {"query": {"q1": False, "q2": 0}, "headers": {"h1": ANY, "h2": "0"}, "body": 0},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": ANY, "h2": "0000"}, "body": 0},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": ANY, "h2": "{}"}, "body": 0},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": ANY, "h2": "[]"}, "body": 0},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": ANY, "h2": "null"}, "body": 0},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": "6", "h2": "0"}, "body": 0},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": "{}", "h2": "0"}, "body": 0},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": "[]", "h2": "0"}, "body": 0},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": "", "h2": "0"}, "body": 0},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": "null", "h2": "0"}, "body": 0},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": "false", "h2": "0"}, "body": 0},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": ANY, "h2": "0"}, "body": {}},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": ANY, "h2": "0"}, "body": {"x-prop": {}}},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": ANY, "h2": "0"}, "body": {"x-prop": []}},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": ANY, "h2": "0"}, "body": {"x-prop": None}},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": ANY, "h2": "0"}, "body": {"x-prop": 0}},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": ANY, "h2": "0"}, "body": []},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": ANY, "h2": "0"}, "body": ""},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": ANY, "h2": "0"}},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": ANY, "h2": "0"}, "body": 0},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": ANY, "h2": "0"}, "body": {}},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": ANY, "h2": "0"}, "body": {"j-prop": {}}},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": ANY, "h2": "0"}, "body": {"j-prop": []}},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": ANY, "h2": "0"}, "body": {"j-prop": ""}},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": ANY, "h2": "0"}, "body": {"j-prop": None}},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": ANY, "h2": "0"}, "body": {"j-prop": False}},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": ANY, "h2": "0"}, "body": {"j-prop": ANY}},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": ANY, "h2": "0"}, "body": []},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": ANY, "h2": "0"}, "body": ""},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": ANY, "h2": "0"}},
    {"query": {"q1": ANY, "q2": 0}, "headers": {"h1": ANY, "h2": "0"}, "body": 0},
]
MIXED_CASES = [
    {"query": {"q1": 5}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": "00"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": {}}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": []}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": None}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": 0}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": "0000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": 4, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": {}, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": [], "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": "", "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": None, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": False, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": ANY, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": 6, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "0000"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "{}"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "[]"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "null"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "0"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "00"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "6", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "{}", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "[]", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "null", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "false", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": ANY, "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "4", "h2": "000"}, "body": {"j-prop": 0}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"x-prop": {}}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"x-prop": []}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"x-prop": None}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"x-prop": 0}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": []},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": ""},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": 0},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"x-prop": ""}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": {}}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": []}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": ""}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": None}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": False}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": ANY}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": []},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": ""},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": 0},
    {"query": {"q1": 5, "q2": "000"}, "headers": {"h1": "5", "h2": "000"}, "body": {"j-prop": 0}},
]


@pytest.mark.parametrize(
    ("methods", "expected"),
    [
        (
            [DataGenerationMethod.positive],
            POSITIVE_CASES,
        ),
        (
            [DataGenerationMethod.negative],
            NEGATIVE_CASES,
        ),
        (
            [DataGenerationMethod.positive, DataGenerationMethod.negative],
            MIXED_CASES,
        ),
    ],
)
def test_phase(ctx, methods, expected):
    schema = ctx.openapi.build_schema(
        {
            "/foo": {
                "post": {
                    "parameters": [
                        {
                            "in": "query",
                            "name": "q1",
                            "schema": {"type": "integer", "minimum": 5},
                            "required": True,
                        },
                        {
                            "in": "query",
                            "name": "q2",
                            "schema": {"type": "string", "minLength": 3},
                            "required": True,
                        },
                        {
                            "in": "header",
                            "name": "h1",
                            "schema": {"type": "integer", "maximum": 5},
                            "required": True,
                        },
                        {
                            "in": "header",
                            "name": "h2",
                            "schema": {"type": "string", "maxLength": 3},
                            "required": True,
                        },
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"j-prop": {"type": "integer"}},
                                    "required": ["j-prop"],
                                },
                            },
                            "application/xml": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"x-prop": {"type": "string"}},
                                    "required": ["x-prop"],
                                },
                            },
                        },
                    },
                    "responses": {"default": {"description": "OK"}},
                }
            },
        }
    )
    assert_coverage(schema, methods, expected)


def test_phase_no_body(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/foo": {
                "post": {
                    "parameters": [
                        {
                            "in": "query",
                            "name": "q1",
                            "schema": {"type": "integer", "minimum": 5},
                            "required": True,
                        },
                    ],
                    "responses": {"default": {"description": "OK"}},
                }
            },
        }
    )
    assert_coverage(schema, [DataGenerationMethod.positive], [{"query": {"q1": 6}}, {"query": {"q1": 5}}])


def test_with_example(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/foo": {
                "post": {
                    "parameters": [
                        {
                            "in": "query",
                            "name": "q1",
                            "schema": {"type": "string", "example": "secret"},
                            "required": True,
                        },
                    ],
                    "responses": {"default": {"description": "OK"}},
                }
            },
        }
    )
    assert_coverage(
        schema,
        [DataGenerationMethod.positive],
        [{"query": {"q1": "secret"}}],
    )


EXPECTED_EXAMPLES = [
    {"query": {"q1": "A1", "q2": 20}},
    {"query": {"q1": "B2", "q2": 10}},
    {"query": {"q1": "A1", "q2": 10}},
]


def test_with_examples_openapi_3(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/foo": {
                "post": {
                    "parameters": [
                        {
                            "in": "query",
                            "name": "q1",
                            "schema": {"type": "string"},
                            "required": True,
                            "examples": {
                                "first": {"value": "A1"},
                                "second": {"value": "B2"},
                            },
                        },
                        {
                            "in": "query",
                            "name": "q2",
                            "schema": {"type": "integer"},
                            "required": True,
                            "examples": {
                                "first": {"value": 10},
                                "second": {"value": 20},
                            },
                        },
                    ],
                    "responses": {"default": {"description": "OK"}},
                }
            },
        }
    )
    assert_coverage(
        schema,
        [DataGenerationMethod.positive],
        EXPECTED_EXAMPLES,
    )


def test_with_optional_parameters(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/foo": {
                "post": {
                    "parameters": [
                        {"in": "query", "name": "q1", "schema": {"type": "string"}, "required": True, "example": "A1"},
                        {"in": "query", "name": "q2", "schema": {"type": "integer"}, "required": False, "example": 10},
                        {"in": "query", "name": "q3", "schema": {"type": "integer"}, "required": False, "example": 15},
                        {"in": "query", "name": "q4", "schema": {"type": "integer"}, "required": False, "example": 20},
                    ],
                    "responses": {"default": {"description": "OK"}},
                }
            },
        }
    )
    assert_coverage(
        schema,
        [DataGenerationMethod.positive],
        [
            {
                "query": {
                    "q1": "A1",
                    "q3": 15,
                    "q4": 20,
                },
            },
            {
                "query": {
                    "q1": "A1",
                    "q2": 10,
                    "q4": 20,
                },
            },
            {
                "query": {
                    "q1": "A1",
                    "q2": 10,
                    "q3": 15,
                },
            },
            {
                "query": {
                    "q1": "A1",
                    "q4": 20,
                },
            },
            {
                "query": {
                    "q1": "A1",
                    "q3": 15,
                },
            },
            {
                "query": {
                    "q1": "A1",
                    "q2": 10,
                },
            },
            {
                "query": {
                    "q1": "A1",
                },
            },
            {
                "query": {
                    "q1": "A1",
                    "q2": 10,
                    "q3": 15,
                    "q4": 20,
                },
            },
        ],
    )


def test_with_example_openapi_3(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/foo": {
                "post": {
                    "parameters": [
                        {"in": "query", "name": "q1", "schema": {"type": "string"}, "required": True, "example": "A1"},
                        {"in": "query", "name": "q2", "schema": {"type": "integer"}, "required": True, "example": 10},
                    ],
                    "responses": {"default": {"description": "OK"}},
                }
            },
        }
    )
    assert_coverage(
        schema,
        [DataGenerationMethod.positive],
        [
            {
                "query": {
                    "q1": "A1",
                    "q2": 10,
                },
            },
        ],
    )


def test_with_response_example_openapi_3(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/items/{itemId}/": {
                "get": {
                    "parameters": [{"name": "itemId", "in": "path", "schema": {"type": "string"}, "required": True}],
                    "responses": {
                        "200": {
                            "description": "",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Item"},
                                    "examples": {
                                        "Example1": {"value": {"id": "123456"}},
                                        "Example2": {"value": {"itemId": "456789"}},
                                    },
                                }
                            },
                        }
                    },
                }
            }
        },
        components={"schemas": {"Item": {"properties": {"id": {"type": "string"}}}}},
    )
    assert_coverage(
        schema,
        [DataGenerationMethod.positive],
        [
            {
                "path_parameters": {
                    "itemId": "456789",
                },
            },
            {
                "path_parameters": {
                    "itemId": "123456",
                },
            },
        ],
        path=("/items/{itemId}/", "get"),
    )


def test_with_examples_openapi_3_1():
    experimental.OPEN_API_3_1.enable()
    schema = {
        "openapi": "3.1.0",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {
            "/foo": {
                "post": {
                    "parameters": [
                        {
                            "in": "query",
                            "name": "q1",
                            "schema": {"type": "string", "examples": ["A1", "B2"]},
                            "required": True,
                        },
                        {
                            "in": "query",
                            "name": "q2",
                            "schema": {"type": "integer", "examples": [10, 20]},
                            "required": True,
                        },
                    ],
                    "responses": {"default": {"description": "OK"}},
                }
            }
        },
    }
    assert_coverage(
        schema,
        [DataGenerationMethod.positive],
        EXPECTED_EXAMPLES,
    )


def test_with_examples_openapi_3_request_body(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/foo": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "age": {"type": "integer"},
                                        "tags": {"type": "array", "items": {"type": "string"}},
                                        "address": {
                                            "type": "object",
                                            "properties": {"street": {"type": "string"}, "city": {"type": "string"}},
                                        },
                                    },
                                    "required": ["name", "age"],
                                },
                                "examples": {
                                    "example1": {
                                        "value": {
                                            "name": "John Doe",
                                            "age": 30,
                                            "tags": ["developer", "python"],
                                            "address": {"street": "123 Main St", "city": "Anytown"},
                                        }
                                    },
                                    "example2": {
                                        "value": {
                                            "name": "Jane Smith",
                                            "age": 25,
                                            "tags": ["designer", "ui/ux"],
                                            "address": {"street": "456 Elm St", "city": "Somewhere"},
                                        }
                                    },
                                },
                            }
                        },
                        "required": True,
                    },
                    "responses": {"default": {"description": "OK"}},
                }
            },
        }
    )
    assert_coverage(
        schema,
        [DataGenerationMethod.positive],
        [
            {"body": {"address": {}, "age": 25, "name": "John Doe", "tags": ["designer", "ui/ux"]}},
            {
                "body": {
                    "address": {"street": "456 Elm St"},
                    "age": 25,
                    "name": "John Doe",
                    "tags": ["designer", "ui/ux"],
                }
            },
            {"body": {"address": {"city": "Anytown"}, "age": 25, "name": "John Doe", "tags": ["designer", "ui/ux"]}},
            {
                "body": {
                    "address": {"street": "456 Elm St", "city": "Somewhere"},
                    "age": 25,
                    "name": "John Doe",
                    "tags": ["designer", "ui/ux"],
                }
            },
            {
                "body": {
                    "address": {"city": "Anytown", "street": "123 Main St"},
                    "age": 25,
                    "name": "John Doe",
                    "tags": ["developer", "python"],
                }
            },
            {
                "body": {
                    "address": {"city": "Anytown", "street": "123 Main St"},
                    "age": 30,
                    "name": "John Doe",
                    "tags": ["designer", "ui/ux"],
                }
            },
            {
                "body": {
                    "address": {"city": "Anytown", "street": "123 Main St"},
                    "age": 25,
                    "name": "Jane Smith",
                    "tags": ["designer", "ui/ux"],
                }
            },
            {"body": {"age": 25, "name": "John Doe"}},
            {"body": {"age": 25, "name": "John Doe", "tags": ["designer", "ui/ux"]}},
            {"body": {"address": {"city": "Anytown", "street": "123 Main St"}, "age": 25, "name": "John Doe"}},
            {
                "body": {
                    "name": "Jane Smith",
                    "age": 25,
                    "tags": ["designer", "ui/ux"],
                    "address": {"street": "456 Elm St", "city": "Somewhere"},
                }
            },
            {
                "body": {
                    "name": "John Doe",
                    "age": 30,
                    "tags": ["developer", "python"],
                    "address": {"street": "123 Main St", "city": "Anytown"},
                }
            },
        ],
    )


def test_with_examples_openapi_2(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/foo": {
                "post": {
                    "parameters": [
                        {
                            "in": "query",
                            "name": "q1",
                            "type": "string",
                            "required": True,
                            "x-examples": {
                                "first": {"value": "A1"},
                                "second": {"value": "B2"},
                            },
                        },
                        {
                            "in": "query",
                            "name": "q2",
                            "type": "integer",
                            "required": True,
                            "x-examples": {
                                "first": {"value": 10},
                                "second": {"value": 20},
                            },
                        },
                    ],
                    "responses": {"default": {"description": "OK"}},
                }
            },
        },
        version="2.0",
    )
    assert_coverage(
        schema,
        [DataGenerationMethod.positive],
        EXPECTED_EXAMPLES,
    )


def test_negative_patterns(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/foo": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {
                                            "type": "string",
                                            "minLength": 3,
                                            "maxLength": 10,
                                            "pattern": "^[a-zA-Z0-9-_]$",
                                        },
                                    },
                                    "required": ["name"],
                                },
                            }
                        },
                        "required": True,
                    },
                    "responses": {"default": {"description": "OK"}},
                }
            },
        }
    )
    assert_coverage(
        schema,
        [DataGenerationMethod.negative],
        [
            {
                "body": {},
            },
            {
                "body": {
                    "name": "000",
                },
            },
            {
                "body": {
                    "name": "00000000000",
                },
            },
            {
                "body": {
                    "name": "00",
                },
            },
            {
                "body": {
                    "name": {},
                },
            },
            {
                "body": {
                    "name": [],
                },
            },
            {
                "body": {
                    "name": None,
                },
            },
            {
                "body": {
                    "name": 0,
                },
            },
            {
                "body": [],
            },
            {
                "body": "",
            },
            {},
            {
                "body": 0,
            },
        ],
    )


def test_required_header(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/foo": {
                "post": {
                    "parameters": [
                        {"name": "X-API-Key-1", "in": "header", "required": True, "schema": {"type": "string"}},
                        {"name": "X-API-Key-2", "in": "header", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    assert_coverage(
        schema,
        [DataGenerationMethod.negative],
        [
            {
                "headers": {"X-API-Key-1": "0"},
            },
            {
                "headers": {"X-API-Key-2": "0"},
            },
            {
                "headers": {"X-API-Key-1": "0", "X-API-Key-2": "{}"},
            },
            {
                "headers": {"X-API-Key-1": "0", "X-API-Key-2": "[]"},
            },
            {
                "headers": {"X-API-Key-1": "0", "X-API-Key-2": "null"},
            },
            {
                "headers": {"X-API-Key-1": "{}", "X-API-Key-2": "0"},
            },
            {
                "headers": {"X-API-Key-1": "[]", "X-API-Key-2": "0"},
            },
            {
                "headers": {"X-API-Key-1": "null", "X-API-Key-2": "0"},
            },
        ],
    )


def test_path_parameter(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/foo/{id}": {
                "post": {
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    assert_coverage(
        schema,
        [DataGenerationMethod.negative],
        [
            {
                "path_parameters": {
                    "id": {},
                },
            },
            {
                "path_parameters": {
                    "id": [],
                },
            },
            {
                "path_parameters": {
                    "id": None,
                },
            },
        ],
        path=("/foo/{id}", "post"),
    )


def assert_coverage(schema, methods, expected, path=None):
    schema = schemathesis.from_dict(schema, validate_schema=True)

    cases = []
    operation = schema[path[0]][path[1]] if path else schema["/foo"]["post"]

    def test(case):
        if case.meta.phase != TestPhase.COVERAGE:
            return
        assert_requests_call(case)
        if len(methods) == 1:
            assert case.data_generation_method == methods[0]
        output = {}
        for container in LOCATION_TO_CONTAINER.values():
            value = getattr(case, container)
            if value is not None and value is not NOT_SET:
                output[container] = value
        cases.append(output)

    test_func = create_test(
        operation=operation, test=test, data_generation_methods=methods, settings=settings(phases=[Phase.explicit])
    )

    test_func()

    assert cases == expected
