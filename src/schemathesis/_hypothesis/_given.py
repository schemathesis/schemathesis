"""Integrating `hypothesis.given` into Schemathesis."""

from __future__ import annotations

from inspect import getfullargspec
from typing import TYPE_CHECKING, Any, Callable, NoReturn, Union

from ..exceptions import UsageError

if TYPE_CHECKING:
    from hypothesis.strategies import SearchStrategy

    from ..types import GenericTest

__all__ = ["get_given_args", "get_given_kwargs", "is_given_applied", "given_proxy", "merge_given_args", "GivenInput"]

EllipsisType = type(...)
GivenInput = Union["SearchStrategy", EllipsisType]  # type: ignore[valid-type]
GIVEN_ARGS_MARKER = "_schemathesis_given_args"
GIVEN_KWARGS_MARKER = "_schemathesis_given_kwargs"


def get_given_args(func: GenericTest) -> tuple:
    return getattr(func, GIVEN_ARGS_MARKER, ())


def get_given_kwargs(func: GenericTest) -> dict[str, Any]:
    return getattr(func, GIVEN_KWARGS_MARKER, {})


def is_given_applied(func: GenericTest) -> bool:
    return hasattr(func, GIVEN_ARGS_MARKER) or hasattr(func, GIVEN_KWARGS_MARKER)


def given_proxy(*args: GivenInput, **kwargs: GivenInput) -> Callable[[GenericTest], GenericTest]:
    """Proxy Hypothesis strategies to ``hypothesis.given``."""

    def wrapper(func: GenericTest) -> GenericTest:
        if hasattr(func, GIVEN_ARGS_MARKER):

            def wrapped_test(*_: Any, **__: Any) -> NoReturn:
                raise UsageError(
                    f"You have applied `given` to the `{func.__name__}` test more than once, which "
                    "overrides the previous decorator. You need to pass all arguments to the same `given` call."
                )

            return wrapped_test

        setattr(func, GIVEN_ARGS_MARKER, args)
        setattr(func, GIVEN_KWARGS_MARKER, kwargs)
        return func

    return wrapper


def merge_given_args(func: GenericTest, args: tuple, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Merge positional arguments to ``@schema.given`` into a dictionary with keyword arguments.

    Kwargs are modified inplace.
    """
    if args:
        argspec = getfullargspec(func)
        for name, strategy in zip(reversed([arg for arg in argspec.args if arg != "case"]), reversed(args)):
            kwargs[name] = strategy
    return kwargs


def validate_given_args(func: GenericTest, args: tuple, kwargs: dict[str, Any]) -> Callable | None:
    from hypothesis.core import is_invalid_test
    from hypothesis.internal.reflection import get_signature

    signature = get_signature(func)
    return is_invalid_test(func, signature, args, kwargs)  # type: ignore
