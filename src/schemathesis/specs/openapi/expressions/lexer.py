"""Lexical analysis of runtime expressions."""

from collections.abc import Callable, Generator
from dataclasses import dataclass
from enum import Enum, unique


@unique
class TokenType(int, Enum):
    VARIABLE = 1
    STRING = 2
    POINTER = 3
    DOT = 4
    LBRACKET = 5
    RBRACKET = 6


@dataclass
class Token:
    """Lexical token that may occur in a runtime expression."""

    value: str
    end: int
    type_: TokenType

    __slots__ = ("value", "end", "type_")

    # Helpers for cleaner instantiation

    @classmethod
    def variable(cls, value: str, end: int) -> "Token":
        return cls(value, end, TokenType.VARIABLE)

    @classmethod
    def string(cls, value: str, end: int) -> "Token":
        return cls(value, end, TokenType.STRING)

    @classmethod
    def pointer(cls, value: str, end: int) -> "Token":
        return cls(value, end, TokenType.POINTER)

    @classmethod
    def lbracket(cls, end: int) -> "Token":
        return cls("{", end, TokenType.LBRACKET)

    @classmethod
    def rbracket(cls, end: int) -> "Token":
        return cls("}", end, TokenType.RBRACKET)

    @classmethod
    def dot(cls, end: int) -> "Token":
        return cls(".", end, TokenType.DOT)

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
            yield Token.variable(expression[start:cursor], cursor - 1)
        elif current_symbol() == ".":
            yield Token.dot(cursor)
            move()
        elif current_symbol() == "{":
            yield Token.lbracket(cursor)
            move()
        elif current_symbol() == "}":
            yield Token.rbracket(cursor)
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
            yield Token.pointer(expression[start:cursor], cursor - 1)
        else:
            start = cursor
            move_until(lambda: is_eol() or current_symbol() in stop_symbols)
            yield Token.string(expression[start:cursor], cursor - 1)
