"""Lexical analysis of runtime expressions."""
from enum import Enum, unique
from typing import Callable, Generator

import attr


@unique  # pragma: no mutate
class TokenType(Enum):
    VARIABLE = 1  # pragma: no mutate
    STRING = 2  # pragma: no mutate
    POINTER = 3  # pragma: no mutate
    DOT = 4  # pragma: no mutate
    LBRACKET = 5  # pragma: no mutate
    RBRACKET = 6  # pragma: no mutate


@attr.s(slots=True)  # pragma: no mutate
class Token:
    """Lexical token that may occur in a runtime expression."""

    value: str = attr.ib()  # pragma: no mutate
    type_: TokenType = attr.ib()  # pragma: no mutate

    # Helpers for cleaner instantiation

    @classmethod
    def variable(cls, value: str) -> "Token":
        return cls(value, TokenType.VARIABLE)

    @classmethod
    def string(cls, value: str) -> "Token":
        return cls(value, TokenType.STRING)

    @classmethod
    def pointer(cls, value: str) -> "Token":
        return cls(value, TokenType.POINTER)

    @classmethod
    def lbracket(cls) -> "Token":
        return cls("{", TokenType.LBRACKET)

    @classmethod
    def rbracket(cls) -> "Token":
        return cls("}", TokenType.RBRACKET)

    @classmethod
    def dot(cls) -> "Token":
        return cls(".", TokenType.DOT)

    # Helpers for simpler type comparison

    @property
    def is_string(self) -> bool:
        return self.type_ == TokenType.STRING

    @property
    def is_variable(self) -> bool:
        return self.type_ == TokenType.VARIABLE

    @property
    def is_dot(self) -> bool:
        return self.type_ == TokenType.DOT

    @property
    def is_pointer(self) -> bool:
        return self.type_ == TokenType.POINTER

    @property
    def is_left_bracket(self) -> bool:
        return self.type_ == TokenType.LBRACKET

    @property
    def is_right_bracket(self) -> bool:
        return self.type_ == TokenType.RBRACKET


TokenGenerator = Generator[Token, None, None]


def tokenize(expression: str) -> TokenGenerator:
    """Do lexical analysis of the expression and return a list of tokens."""
    cursor = 0

    def is_eol() -> bool:
        return cursor == len(expression)

    def current_symbol() -> str:
        return expression[cursor]

    def move() -> None:
        nonlocal cursor
        cursor += 1

    def move_until(predicate: Callable[[], bool]) -> None:
        move()
        while not predicate():
            move()

    stop_symbols = {"$", ".", "{", "}", "#"}

    while not is_eol():
        if current_symbol() == "$":
            start = cursor
            move_until(lambda: is_eol() or current_symbol() in stop_symbols)
            yield Token.variable(expression[start:cursor])
        elif current_symbol() == ".":
            yield Token.dot()
            move()
        elif current_symbol() == "{":
            yield Token.lbracket()
            move()
        elif current_symbol() == "}":
            yield Token.rbracket()
            move()
        elif current_symbol() == "#":
            start = cursor
            # Symbol '}' is valid inside a JSON pointer, but also denotes closing of an embedded runtime expression
            # This is an ambiguous situation, for example:
            # Expression: `ID_{$request.body#/foo}}`
            # Body: `{"foo}": 1, "foo": 2}`
            # It could be evaluated differently:
            #   - `ID_1` if we take the last bracket as the closing one
            #   - `ID_2}` if we take the first bracket as the closing one
            # In this situation we take the second approach, to support cases like this:
            # `ID_{$response.body#/foo}_{$response.body#/bar}`
            # Which is much easier if we treat `}` as a closing bracket of an embedded runtime expression
            move_until(lambda: is_eol() or current_symbol() == "}")
            yield Token.pointer(expression[start:cursor])
        else:
            start = cursor
            move_until(lambda: is_eol() or current_symbol() in stop_symbols)
            yield Token.string(expression[start:cursor])
