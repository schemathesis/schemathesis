from typing import Generic, TypeVar

T = TypeVar("T")
E = TypeVar("E", bound=Exception)


class Ok(Generic[T]):
    __slots__ = ("_value",)

    def __init__(self, value: T):
        self._value = value

    def ok(self) -> T:
        return self._value


class Err(Generic[E]):
    __slots__ = ("_error",)

    def __init__(self, error: E):
        self._error = error

    def err(self) -> E:
        return self._error


Result = Ok[T] | Err[E]
