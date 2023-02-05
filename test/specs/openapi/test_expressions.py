import json

import pytest
import requests
from hypothesis import given
from hypothesis import strategies as st

from schemathesis import Case
from schemathesis.models import APIOperation
from schemathesis.specs.openapi import expressions
from schemathesis.specs.openapi.expressions.errors import RuntimeExpressionError
from schemathesis.specs.openapi.expressions.lexer import Token
from schemathesis.specs.openapi.references import resolve_pointer

DOCUMENT = {"foo": ["bar", "baz"], "": 0, "a/b": 1, "c%d": 2, "e^f": 3, "g|h": 4, "i\\j": 5, 'k"l': 6, " ": 7, "m~n": 8}


@pytest.fixture(scope="module")
def operation():
    return APIOperation(
        "/users/{user_id}", "GET", None, None, verbose_name="GET /users/{user_id}", base_url="http://127.0.0.1:8080/api"
    )


@pytest.fixture(scope="module")
def case(operation):
    return Case(
        operation,
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


@pytest.fixture(scope="module")
def context(case, response):
    return expressions.ExpressionContext(response=response, case=case)


@pytest.mark.parametrize(
    "expr, expected",
    (
        ("", ""),
        ("foo", "foo"),
        ("$url", "http://127.0.0.1:8080/api/users/5?username=foo"),
        ("$method", "GET"),
        ("$statusCode", "200"),
        ("ID_{$method}", "ID_GET"),
        ("$request.query.username", "foo"),
        ("spam_{$request.query.username}_baz_{$request.query.username}", "spam_foo_baz_foo"),
        ("spam_{$request.path.user_id}", "spam_5"),
        ("spam_{$request.header.X-Token}", "spam_secret"),
        ("spam_{$request.header.x-token}", "spam_secret"),
        ("$request.path.user_id", 5),
        ("$request.body", {"a": 1}),
        ("$request.body#/a", 1),
        ("spam_{$response.header.X-Response}", "spam_Y"),
        ("spam_{$response.header.x-response}", "spam_Y"),
        ("$response.body#/foo/0", "bar"),
        ("$response.body#/g|h", 4),
        (42, 42),
        ("$response.body", DOCUMENT),
        ("ID_{$response.body#/g|h}", "ID_4"),
        ("ID_{$response.body#/g|h}_{$response.body#/a~1b}", "ID_4_1"),
        ("eq.{$response.body#/g|h}", "eq.4"),
    ),
)
def test_evaluate(context, expr, expected):
    assert expressions.evaluate(expr, context) == expected


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
        "$response}",
    ),
)
def test_invalid_expression(context, expr):
    with pytest.raises(RuntimeExpressionError):
        expressions.evaluate(expr, context)


@given(expr=(st.text() | (st.lists(st.sampled_from([".", "}", "{", "$"]) | st.text()).map("".join))))
def test_random_expression(expr):
    try:
        expressions.evaluate(expr, context)
    except RuntimeExpressionError:
        pass


@pytest.mark.parametrize(
    "expr, expected",
    (
        ("$url", [Token.variable("$url")]),
        ("foo", [Token.string("foo")]),
        ("foo1", [Token.string("foo1")]),
        ("{}", [Token.lbracket(), Token.rbracket()]),
        ("{foo}", [Token.lbracket(), Token.string("foo"), Token.rbracket()]),
        ("{$foo}", [Token.lbracket(), Token.variable("$foo"), Token.rbracket()]),
        (
            "foo{$bar}spam",
            [Token.string("foo"), Token.lbracket(), Token.variable("$bar"), Token.rbracket(), Token.string("spam")],
        ),
        ("$foo.bar", [Token.variable("$foo"), Token.dot(), Token.string("bar")]),
        ("$foo.$bar", [Token.variable("$foo"), Token.dot(), Token.variable("$bar")]),
        (
            "{$foo.$bar}",
            [Token.lbracket(), Token.variable("$foo"), Token.dot(), Token.variable("$bar"), Token.rbracket()],
        ),
        (
            "$request.body#/foo/bar",
            [Token.variable("$request"), Token.dot(), Token.string("body"), Token.pointer("#/foo/bar")],
        ),
    ),
)
def test_lexer(expr, expected):
    assert list(expressions.lexer.tokenize(expr)) == expected


@pytest.mark.parametrize(
    "pointer, expected",
    (
        ("", DOCUMENT),
        ("abc", None),
        ("/foo/123", None),
        ("/foo", ["bar", "baz"]),
        ("/foo/0", "bar"),
        ("/", 0),
        ("/a~1b", 1),
        ("/c%d", 2),
        ("/c%d/foo", None),
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
