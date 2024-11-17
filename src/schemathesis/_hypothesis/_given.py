"""Integrating `hypothesis.given` into Schemathesis."""

from __future__ import annotations

from inspect import getfullargspec
from typing import TYPE_CHECKING, Any, Callable, NoReturn, Union

from schemathesis.core.errors import IncorrectUsage
from schemathesis.core.marks import Mark

if TYPE_CHECKING:
    from hypothesis.strategies import SearchStrategy


__all__ = ["is_given_applied", "given_proxy", "merge_given_args", "GivenInput", "GivenArgsMark", "GivenKwargsMark"]

EllipsisType = type(...)
GivenInput = Union["SearchStrategy", EllipsisType]  # type: ignore[valid-type]

GivenArgsMark = Mark[tuple](attr_name="given_args", default=())
GivenKwargsMark = Mark[dict[str, Any]](attr_name="given_kwargs", default=dict)


def is_given_applied(func: Callable) -> bool:
    return GivenArgsMark.is_set(func) or GivenKwargsMark.is_set(func)


def given_proxy(*args: GivenInput, **kwargs: GivenInput) -> Callable[[Callable], Callable]:
    """Proxy Hypothesis strategies to ``hypothesis.given``."""

    def wrapper(func: Callable) -> Callable:
        if is_given_applied(func):

            def wrapped_test(*_: Any, **__: Any) -> NoReturn:
                raise IncorrectUsage(
                    f"You have applied `given` to the `{func.__name__}` test more than once, which "
                    "overrides the previous decorator. You need to pass all arguments to the same `given` call."
                )

            return wrapped_test

        GivenArgsMark.set(func, args)
        GivenKwargsMark.set(func, kwargs)
        return func

    return wrapper


def merge_given_args(func: Callable, args: tuple, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Merge positional arguments to ``@schema.given`` into a dictionary with keyword arguments.

    Kwargs are modified inplace.
    """
    if args:
        argspec = getfullargspec(func)
        for name, strategy in zip(reversed([arg for arg in argspec.args if arg != "case"]), reversed(args)):
            kwargs[name] = strategy
    return kwargs


def validate_given_args(func: Callable, args: tuple, kwargs: dict[str, Any]) -> Callable | None:
    from hypothesis.core import is_invalid_test
    from hypothesis.internal.reflection import get_signature

    signature = get_signature(func)
    return is_invalid_test(func, signature, args, kwargs)  # type: ignore
