import json
from unittest.mock import Mock

import pytest
import requests
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from schemathesis.core.transforms import UNRESOLVABLE, resolve_pointer
from schemathesis.core.transport import Response
from schemathesis.generation.stateful.state_machine import StepOutput
from schemathesis.schemas import APIOperation, OperationDefinition
from schemathesis.specs.openapi import expressions
from schemathesis.specs.openapi.adapter import v3_0
from schemathesis.specs.openapi.adapter.parameters import OpenApiBody
from schemathesis.specs.openapi.expressions.errors import RuntimeExpressionError
from schemathesis.specs.openapi.expressions.lexer import Token

DOCUMENT = {
    "foo": ["bar", "baz"],
    "": 0,
    "a/b": 1,
    "c%d": 2,
    "e^f": 3,
    "g|h": 4,
    "i\\j": 5,
    'k"l': 6,
    " ": 7,
    "m~n": 8,
    "bool-value": True,
}


@pytest.fixture
def operation(openapi_30):
    media_type = "application/json"
    content = {"schema": {}}
    definition = {"requestBody": {"content": {media_type: content}}}
    instance = APIOperation(
        "/users/{user_id}",
        "PUT",
        OperationDefinition(definition),
        openapi_30,
        responses=openapi_30._parse_responses({}, ""),
        security=openapi_30._parse_security({}),
        label="PUT /users/{user_id}",
        base_url="http://127.0.0.1:8080/api",
    )
    instance.add_parameter(
        OpenApiBody.from_definition(
            definition=content,
            media_type=media_type,
            is_required=False,
            resource_name=None,
            name_to_uri={},
            adapter=v3_0,
        )
    )
    return instance


@pytest.fixture
def case(operation):
    return operation.Case(
        path_parameters={"user_id": 5},
        query={"username": "foo"},
        headers={"X-Token": "secret"},
        body={"a": 1},
    )


class Headers(dict):
    def getlist(self, key):
        v = self.get(key)
        if v is not None:
            return [v]


@pytest.fixture(scope="module")
def response():
    response = requests.Response()
    response._content = json.dumps(DOCUMENT).encode()
    response.status_code = 200
    response.headers["Content-Type"] = "application/json"
    response.headers["X-Response"] = "Y"
    response.raw = Mock(headers=Headers({"Content-Type": "application/json", "X-Response": "Y"}))
    return Response.from_requests(response, True)


@pytest.fixture
def output(case, response):
    return StepOutput(response=response, case=case)


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("", ""),
        ("foo", "foo"),
        ("$url", "http://127.0.0.1:8080/api/users/5?username=foo"),
        ("$method", "PUT"),
        ("$statusCode", "200"),
        ("ID_{$method}", "ID_PUT"),
        ("$request.query.username", "foo"),
        ("spam_{$request.query.username}_baz_{$request.query.username}", "spam_foo_baz_foo"),
        ("spam_{$request.query.unknown}", UNRESOLVABLE),
        ("spam_{$request.path.user_id}", "spam_5"),
        ("spam_{$request.path.unknown}", UNRESOLVABLE),
        ("spam_{$request.header.X-Token}", "spam_secret"),
        ("spam_{$request.header.x-token}", "spam_secret"),
        ("spam_{$request.header.x-unknown}", UNRESOLVABLE),
        ("$request.path.user_id", 5),
        ("$request.body", {"a": 1}),
        ("$request.body#/a", 1),
        ("$request.body#/unknown", UNRESOLVABLE),
        ("spam_{$response.header.X-Response}", "spam_Y"),
        ("spam_{$response.header.x-response}", "spam_Y"),
        ("$response.body#/foo/0", "bar"),
        ("$response.body#/g|h", 4),
        (42, 42),
        ("$response.body", DOCUMENT),
        ("ID_{$response.body#/g|h}", "ID_4"),
        ("ID_{$response.body#/g|h}_{$response.body#/a~1b}", "ID_4_1"),
        ("eq.{$response.body#/g|h}", "eq.4"),
        ("eq.{$response.body#/unknown}", UNRESOLVABLE),
        ("eq.{$response.header.Content-Type#regex:/(.+)}", "eq.json"),
        ("eq.{$response.header.Content-Type#regex:qwe(.+)}", UNRESOLVABLE),
        ("eq.{$response.header.Unknown}", UNRESOLVABLE),
        ("eq.{$request.query.username#regex:f(.+)}", "eq.oo"),
        ("eq.{$request.query.username#regex:t(.+)}", UNRESOLVABLE),
        ("eq.{$request.query.unknown}", UNRESOLVABLE),
    ],
)
def test_evaluate(output, expr, expected):
    assert expressions.evaluate(expr, output) == expected


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ({"key": "value"}, {"key": "value"}),
        ({"key": "$response.body#/a~1b"}, {"key": 1}),
        ({"$response.body#/": "value"}, {"0": "value"}),
        ({"$response.body#/unknown": "value"}, UNRESOLVABLE),
        ({"$response.body#/foo": "value"}, {'["bar", "baz"]': "value"}),
        ({"$response.body#/foo/unknown": "value"}, UNRESOLVABLE),
        ({"$response.body#/a~1b": "value"}, {"1": "value"}),
        ({"$response.body#/bool-value": "value"}, {"true": "value"}),
        (["$response.body#/foo/unknown"], UNRESOLVABLE),
        (
            {"key": "$response.body#/foo/0", "items": ["$response.body#/foo/1", "literal", 42]},
            {"items": ["baz", "literal", 42], "key": "bar"},
        ),
    ],
)
def test_dynamic_body(output, expr, expected):
    assert expressions.evaluate(expr, output, evaluate_nested=True) == expected


@pytest.mark.parametrize(
    "expr",
    [
        "$u",
        "$urlfoo",
        "{{$foo.$bar}}",
        "{$foo",
        "$foo}",
        "$request..",
        "$request.unknown",
        "$request.body.unknown",
        "$response..",
        "$response.unknown",
        "$response.body.something",
        "$response.header..",
        "$response.header.unknown#wrong",
        "$response.header.unknown#regex:[",
        "$response.header.unknown#regex:(.+)(.+)",
        "$response}",
    ],
)
def test_invalid_expression(output, expr):
    with pytest.raises(RuntimeExpressionError):
        expressions.evaluate(expr, output)


@given(expr=(st.text() | (st.lists(st.sampled_from([".", "}", "{", "$"]) | st.text()).map("".join))))
@settings(deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_random_expression(expr, output):
    try:
        expressions.evaluate(expr, output)
    except RuntimeExpressionError:
        pass


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("$url", [Token.variable("$url", 3)]),
        ("foo", [Token.string("foo", 2)]),
        ("foo1", [Token.string("foo1", 3)]),
        ("{}", [Token.lbracket(0), Token.rbracket(1)]),
        ("{foo}", [Token.lbracket(0), Token.string("foo", 3), Token.rbracket(4)]),
        ("{$foo}", [Token.lbracket(0), Token.variable("$foo", 4), Token.rbracket(5)]),
        (
            "foo{$bar}spam",
            [
                Token.string("foo", 2),
                Token.lbracket(3),
                Token.variable("$bar", 7),
                Token.rbracket(8),
                Token.string("spam", 12),
            ],
        ),
        ("$foo.bar", [Token.variable("$foo", 3), Token.dot(4), Token.string("bar", 7)]),
        ("$foo.$bar", [Token.variable("$foo", 3), Token.dot(4), Token.variable("$bar", 8)]),
        (
            "{$foo.$bar}",
            [Token.lbracket(0), Token.variable("$foo", 4), Token.dot(5), Token.variable("$bar", 9), Token.rbracket(10)],
        ),
        (
            "$request.body#/foo/bar",
            [Token.variable("$request", 7), Token.dot(8), Token.string("body", 12), Token.pointer("#/foo/bar", 21)],
        ),
    ],
)
def test_lexer(expr, expected):
    tokens = list(expressions.lexer.tokenize(expr))
    assert tokens == expected
    assert tokens[-1].end == len(expr) - 1


@pytest.mark.parametrize(
    ("pointer", "expected"),
    [
        ("", DOCUMENT),
        ("abc", UNRESOLVABLE),
        ("/foo/123", UNRESOLVABLE),
        ("/foo", ["bar", "baz"]),
        ("/foo/0", "bar"),
        ("/", 0),
        ("/a~1b", 1),
        ("/c%d", 2),
        ("/c%d/foo", UNRESOLVABLE),
        ("/e^f", 3),
        ("/g|h", 4),
        ("/i\\j", 5),
        ('/k"l', 6),
        ("/ ", 7),
        ("/m~0n", 8),
    ],
)
def test_pointer(pointer, expected):
    assert resolve_pointer(DOCUMENT, pointer) == expected
