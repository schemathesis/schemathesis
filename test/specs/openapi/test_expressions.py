import json

import pytest
import requests
from hypothesis import given, settings
from hypothesis import strategies as st

from schemathesis.models import APIOperation, Case, OperationDefinition
from schemathesis.specs.openapi import expressions
from schemathesis.specs.openapi.expressions.errors import RuntimeExpressionError
from schemathesis.specs.openapi.expressions.lexer import Token
from schemathesis.specs.openapi.references import UNRESOLVABLE, resolve_pointer

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
    return APIOperation(
        "/users/{user_id}",
        "PUT",
        OperationDefinition(
            {"requestBody": {"content": {"application/json": {"schema": {}}}}},
            {"requestBody": {"content": {"application/json": {"schema": {}}}}},
            "",
        ),
        openapi_30,
        verbose_name="PUT /users/{user_id}",
        base_url="http://127.0.0.1:8080/api",
    )


@pytest.fixture
def case(operation):
    return Case(
        operation,
        generation_time=0.0,
        path_parameters={"user_id": 5},
        query={"username": "foo"},
        headers={"X-Token": "secret"},
        body={"a": 1},
    )


@pytest.fixture(scope="module")
def response():
    response = requests.Response()
    response._content = json.dumps(DOCUMENT).encode()
    response.status_code = 200
    response.headers["Content-Type"] = "application/json"
    response.headers["X-Response"] = "Y"
    return response


@pytest.fixture
def context(case, response):
    return expressions.ExpressionContext(response=response, case=case)


@pytest.mark.parametrize(
    "expr, expected",
    (
        ("", ""),
        ("foo", "foo"),
        ("$url", "http://127.0.0.1:8080/api/users/5?username=foo"),
        ("$method", "PUT"),
        ("$statusCode", "200"),
        ("ID_{$method}", "ID_PUT"),
        ("$request.query.username", "foo"),
        ("spam_{$request.query.username}_baz_{$request.query.username}", "spam_foo_baz_foo"),
        ("spam_{$request.query.unknown}", "spam_"),
        ("spam_{$request.path.user_id}", "spam_5"),
        ("spam_{$request.path.unknown}", "spam_"),
        ("spam_{$request.header.X-Token}", "spam_secret"),
        ("spam_{$request.header.x-token}", "spam_secret"),
        ("spam_{$request.header.x-unknown}", "spam_"),
        ("$request.path.user_id", 5),
        ("$request.body", {"a": 1}),
        ("$request.body#/a", 1),
        ("$request.body#/unknown", None),
        ("spam_{$response.header.X-Response}", "spam_Y"),
        ("spam_{$response.header.x-response}", "spam_Y"),
        ("$response.body#/foo/0", "bar"),
        ("$response.body#/g|h", 4),
        (42, 42),
        ("$response.body", DOCUMENT),
        ("ID_{$response.body#/g|h}", "ID_4"),
        ("ID_{$response.body#/g|h}_{$response.body#/a~1b}", "ID_4_1"),
        ("eq.{$response.body#/g|h}", "eq.4"),
        ("eq.{$response.body#/unknown}", "eq."),
        ("eq.{$response.header.Content-Type#regex:/(.+)}", "eq.json"),
        ("eq.{$response.header.Content-Type#regex:qwe(.+)}", "eq."),
        ("eq.{$response.header.Unknown}", "eq."),
        ("eq.{$request.query.username#regex:f(.+)}", "eq.oo"),
        ("eq.{$request.query.username#regex:t(.+)}", "eq."),
        ("eq.{$request.query.unknown}", "eq."),
    ),
)
def test_evaluate(context, expr, expected):
    assert expressions.evaluate(expr, context) == expected


@pytest.mark.parametrize(
    "expr, expected",
    [
        ({"key": "value"}, {"key": "value"}),
        ({"key": "$response.body#/a~1b"}, {"key": 1}),
        ({"$response.body#/": "value"}, {"0": "value"}),
        ({"$response.body#/unknown": "value"}, {"null": "value"}),
        ({"$response.body#/foo": "value"}, {'["bar", "baz"]': "value"}),
        ({"$response.body#/a~1b": "value"}, {"1": "value"}),
        ({"$response.body#/bool-value": "value"}, {"true": "value"}),
        (
            {"key": "$response.body#/foo/0", "items": ["$response.body#/foo/1", "literal", 42]},
            {"items": ["baz", "literal", 42], "key": "bar"},
        ),
    ],
)
def test_dynamic_body(context, expr, expected):
    assert expressions.evaluate(expr, context, evaluate_nested=True) == expected


@pytest.mark.parametrize(
    "expr",
    (
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
    ),
)
def test_invalid_expression(context, expr):
    with pytest.raises(RuntimeExpressionError):
        expressions.evaluate(expr, context)


@given(expr=(st.text() | (st.lists(st.sampled_from([".", "}", "{", "$"]) | st.text()).map("".join))))
@settings(deadline=None)
def test_random_expression(expr):
    try:
        expressions.evaluate(expr, context)
    except RuntimeExpressionError:
        pass


@pytest.mark.parametrize(
    "expr, expected",
    (
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
    ),
)
def test_lexer(expr, expected):
    tokens = list(expressions.lexer.tokenize(expr))
    assert tokens == expected
    assert tokens[-1].end == len(expr) - 1


@pytest.mark.parametrize(
    "pointer, expected",
    (
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
    ),
)
def test_pointer(pointer, expected):
    assert resolve_pointer(DOCUMENT, pointer) == expected
