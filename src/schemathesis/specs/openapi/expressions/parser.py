from functools import lru_cache
from typing import Generator, List, Union

from . import lexer, nodes
from .errors import RuntimeExpressionError, UnknownToken


@lru_cache()  # pragma: no mutate
def parse(expr: str) -> List[nodes.Node]:
    """Parse lexical tokens into concrete expression nodes."""
    return list(_parse(expr))


def _parse(expr: str) -> Generator[nodes.Node, None, None]:
    tokens = lexer.tokenize(expr)
    brackets_stack: List[str] = []
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
        raise UnknownToken(token.value)


def _parse_request(tokens: lexer.TokenGenerator, expr: str) -> Union[nodes.BodyRequest, nodes.NonBodyRequest]:
    skip_dot(tokens, "$request")
    location = next(tokens)
    if location.value in ("query", "path", "header"):
        skip_dot(tokens, f"$request.{location.value}")
        parameter = take_string(tokens, expr)
        return nodes.NonBodyRequest(location.value, parameter)
    if location.value == "body":
        try:
            token = next(tokens)
            if token.is_pointer:
                return nodes.BodyRequest(token.value)
        except StopIteration:
            return nodes.BodyRequest()
    raise RuntimeExpressionError(f"Invalid expression: {expr}")


def _parse_response(tokens: lexer.TokenGenerator, expr: str) -> Union[nodes.HeaderResponse, nodes.BodyResponse]:
    skip_dot(tokens, "$response")
    location = next(tokens)
    if location.value == "header":
        skip_dot(tokens, f"$response.{location.value}")
        parameter = take_string(tokens, expr)
        return nodes.HeaderResponse(parameter)
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


def take_string(tokens: lexer.TokenGenerator, expr: str) -> str:
    parameter = next(tokens)
    if not parameter.is_string:
        raise RuntimeExpressionError(f"Invalid expression: {expr}")
    return parameter.value
