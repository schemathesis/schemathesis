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


@dataclass(slots=True)
class Token:
    value: str
    end: int
    type_: TokenType

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
            # A `}` ends the pointer even though pointers allow it, to support `{$request.body#/a}_{$request.body#/b}`
            move_until(lambda: is_eol() or current_symbol() == "}")
            yield Token.pointer(expression[start:cursor], cursor - 1)
        else:
            start = cursor
            move_until(lambda: is_eol() or current_symbol() in stop_symbols)
            yield Token.string(expression[start:cursor], cursor - 1)
