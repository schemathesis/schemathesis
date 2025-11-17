"""Integrating `hypothesis.given` into Schemathesis."""

from __future__ import annotations

from collections.abc import Callable
from inspect import getfullargspec
from typing import TYPE_CHECKING, Any, NoReturn, Union

from schemathesis.core.errors import IncorrectUsage
from schemathesis.core.marks import Mark

if TYPE_CHECKING:
    from hypothesis.strategies import SearchStrategy


__all__ = [
    "is_given_applied",
    "given_proxy",
    "merge_given_args",
    "GivenInput",
    "GivenArgsMark",
    "GivenKwargsMark",
    "GIVEN_TARGET_ATTR",
    "GIVEN_REFRESH_ATTR",
    "GIVEN_AND_EXPLICIT_EXAMPLE_ERROR_MESSAGE",
    "format_given_and_schema_examples_error",
]

EllipsisType = type(...)
GivenInput = Union["SearchStrategy", EllipsisType]  # type: ignore[valid-type]

GivenArgsMark = Mark[tuple](attr_name="given_args", default=())
GivenKwargsMark = Mark[dict[str, Any]](attr_name="given_kwargs", default=dict)
GIVEN_TARGET_ATTR = "_schemathesis_given_target"
GIVEN_REFRESH_ATTR = "_schemathesis_given_refresh"

# Error messages for incompatible @schema.given() usage
GIVEN_AND_EXPLICIT_EXAMPLE_ERROR_MESSAGE = (
    "Cannot combine `@schema.given()` with explicit `@example()` decorators.\n\n"
    "When you use `@schema.given(param=...)`, your test function gains additional parameters beyond 'case'. "
    "However, `@example()` decorators only provide values for a different set of parameters. "
    "This parameter mismatch prevents the test from running.\n\n"
    "Solution: Create separate test functions:\n"
    "  1. One with `@example()` for specific test cases\n"
    "  2. One with `@schema.given()` for property-based testing"
)


def format_given_and_schema_examples_error(param_names: str) -> str:
    """Format error message when @schema.given() is used with schema examples."""
    return (
        f"Cannot combine `@schema.given()` with schema examples.\n\n"
        f"Your test uses `@schema.given()` with custom strategies for: {param_names}\n"
        f"This adds extra parameters to your test function that schema examples cannot provide values for. "
        f"Schema examples only work with the standard 'case' parameter.\n\n"
        f"Solution: Create separate test functions:\n"
        f"  1. One without `@schema.given()` to test schema examples\n"
        f"  2. One with `@schema.given()` for custom property-based testing"
    )


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
        target = getattr(func, GIVEN_TARGET_ATTR, None)
        if target is not None:
            GivenArgsMark.set(target, args)
            GivenKwargsMark.set(target, kwargs)
        refresh = getattr(func, GIVEN_REFRESH_ATTR, None)
        if refresh is not None:
            refresh()
        return func

    return wrapper


def merge_given_args(func: Callable, args: tuple, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Merge positional arguments to ``@schema.given`` into a dictionary with keyword arguments.

    Kwargs are modified inplace.
    """
    if args:
        argspec = getfullargspec(func)
        for name, strategy in zip(
            reversed([arg for arg in argspec.args if arg != "case"]), reversed(args), strict=False
        ):
            kwargs[name] = strategy
    return kwargs


def validate_given_args(func: Callable, args: tuple, kwargs: dict[str, Any]) -> Callable | None:
    from hypothesis.core import is_invalid_test
    from hypothesis.internal.reflection import get_signature

    signature = get_signature(func)
    return is_invalid_test(func, signature, args, kwargs)
