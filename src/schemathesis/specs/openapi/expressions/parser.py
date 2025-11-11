from __future__ import annotations

import re
from collections.abc import Generator
from functools import lru_cache

from . import extractors, lexer, nodes
from .errors import RuntimeExpressionError, UnknownToken


@lru_cache
def parse(expr: str) -> list[nodes.Node]:
    """Parse lexical tokens into concrete expression nodes."""
    return list(_parse(expr))


def _parse(expr: str) -> Generator[nodes.Node, None, None]:
    tokens = lexer.tokenize(expr)
    brackets_stack: list[str] = []
    for token in tokens:
        if token.is_string or token.is_dot:
            yield nodes.String(token.value)
        elif token.is_variable:
            yield from _parse_variable(tokens, token, expr)
        elif token.is_left_bracket:
            if brackets_stack:
                raise RuntimeExpressionError("Nested embedded expressions are not allowed")
            brackets_stack.append("{")
        elif token.is_right_bracket:
            if not brackets_stack:
                raise RuntimeExpressionError("Unmatched bracket")
            brackets_stack.pop()
    if brackets_stack:
        raise RuntimeExpressionError("Unmatched bracket")


def _parse_variable(tokens: lexer.TokenGenerator, token: lexer.Token, expr: str) -> Generator[nodes.Node, None, None]:
    if token.value == nodes.NodeType.URL.value:
        yield nodes.URL()
    elif token.value == nodes.NodeType.METHOD.value:
        yield nodes.Method()
    elif token.value == nodes.NodeType.STATUS_CODE.value:
        yield nodes.StatusCode()
    elif token.value == nodes.NodeType.REQUEST.value:
        yield _parse_request(tokens, expr)
    elif token.value == nodes.NodeType.RESPONSE.value:
        yield _parse_response(tokens, expr)
    else:
        raise UnknownToken(f"Invalid expression `{expr}`. Unknown token: `{token.value}`")


def _parse_request(tokens: lexer.TokenGenerator, expr: str) -> nodes.BodyRequest | nodes.NonBodyRequest:
    skip_dot(tokens, "$request")
    location = next(tokens)
    if location.value in ("query", "path", "header"):
        skip_dot(tokens, f"$request.{location.value}")
        parameter = take_string(tokens, expr)
        extractor = take_extractor(tokens, expr, parameter.end)
        return nodes.NonBodyRequest(location.value, parameter.value, extractor)
    if location.value == "body":
        try:
            token = next(tokens)
            if token.is_pointer:
                return nodes.BodyRequest(token.value)
        except StopIteration:
            return nodes.BodyRequest()
    raise RuntimeExpressionError(f"Invalid expression: {expr}")


def _parse_response(tokens: lexer.TokenGenerator, expr: str) -> nodes.HeaderResponse | nodes.BodyResponse:
    skip_dot(tokens, "$response")
    location = next(tokens)
    if location.value == "header":
        skip_dot(tokens, f"$response.{location.value}")
        parameter = take_string(tokens, expr)
        extractor = take_extractor(tokens, expr, parameter.end)
        return nodes.HeaderResponse(parameter.value, extractor=extractor)
    if location.value == "body":
        try:
            token = next(tokens)
            if token.is_pointer:
                return nodes.BodyResponse(token.value)
        except StopIteration:
            return nodes.BodyResponse()
    raise RuntimeExpressionError(f"Invalid expression: {expr}")


def skip_dot(tokens: lexer.TokenGenerator, name: str) -> None:
    token = next(tokens)
    if not token.is_dot:
        raise RuntimeExpressionError(f"`{name}` expression should be followed by a dot (`.`). Got: {token.value}")


def take_string(tokens: lexer.TokenGenerator, expr: str) -> lexer.Token:
    parameter = next(tokens)
    if not parameter.is_string:
        raise RuntimeExpressionError(f"Invalid expression: {expr}")
    return parameter


def take_extractor(tokens: lexer.TokenGenerator, expr: str, current_end: int) -> extractors.Extractor | None:
    rest = expr[current_end + 1 :]
    if not rest or rest.startswith("}"):
        return None
    extractor = next(tokens)
    if not extractor.value.startswith("#regex:"):
        raise RuntimeExpressionError(f"Invalid extractor: {expr}")
    pattern = extractor.value[len("#regex:") :]
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise RuntimeExpressionError(f"Invalid regex extractor: {exc}") from None
    if compiled.groups != 1:
        raise RuntimeExpressionError("Regex extractor should have exactly one capturing group")
    return extractors.RegexExtractor(compiled)
