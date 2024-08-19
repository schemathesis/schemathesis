from __future__ import annotations

import functools
from contextlib import contextmanager
from inspect import getfullargspec
from pathlib import Path
from typing import (
    Any,
    Callable,
    Generator,
    NoReturn,
    Union,
)

import pytest
from hypothesis.core import is_invalid_test
from hypothesis.reporting import with_reporter
from hypothesis.strategies import SearchStrategy

from ._compat import InferType, get_signature

# Backward-compat
from .constants import NOT_SET  # noqa: F401
from .exceptions import SkipTest, UsageError
from .types import GenericTest, PathLike


def is_schemathesis_test(func: Callable) -> bool:
    """Check whether test is parametrized with schemathesis."""
    try:
        from .schemas import BaseSchema

        item = getattr(func, PARAMETRIZE_MARKER, None)
        # Comparison is needed to avoid false-positives when mocks are collected by pytest
        return isinstance(item, BaseSchema)
    except Exception:
        return False


def fail_on_no_matches(node_id: str) -> NoReturn:  # type: ignore
    pytest.fail(f"Test function {node_id} does not match any API operations and therefore has no effect")


IGNORED_PATTERNS = (
    "Falsifying example: ",
    "Falsifying explicit example: ",
    "You can add @seed",
    "Failed to reproduce exception. Expected:",
    "Flaky example!",
    "Traceback (most recent call last):",
    "You can reproduce this example by temporarily",
    "Unreliable test timings",
)


@contextmanager
def capture_hypothesis_output() -> Generator[list[str], None, None]:
    """Capture all output of Hypothesis into a list of strings.

    It allows us to have more granular control over Schemathesis output.

    Usage::

        @given(i=st.integers())
        def test(i):
            assert 0

        with capture_hypothesis_output() as output:
            test()  # hypothesis test
            # output == ["Falsifying example: test(i=0)"]
    """
    output = []

    def get_output(value: str) -> None:
        # Drop messages that could be confusing in the Schemathesis context
        if value.startswith(IGNORED_PATTERNS):
            return
        output.append(value)

    # the following context manager is untyped
    with with_reporter(get_output):  # type: ignore
        yield output


GivenInput = Union[SearchStrategy, InferType]
PARAMETRIZE_MARKER = "_schemathesis_test"
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
    signature = get_signature(func)
    return is_invalid_test(func, signature, args, kwargs)  # type: ignore


def compose(*functions: Callable) -> Callable:
    """Compose multiple functions into a single one."""

    def noop(x: Any) -> Any:
        return x

    return functools.reduce(lambda f, g: lambda x: f(g(x)), functions, noop)


def skip(operation_name: str) -> NoReturn:
    raise SkipTest(f"It is not possible to generate negative test cases for `{operation_name}`")


def _ensure_parent(path: PathLike, fail_silently: bool = True) -> None:
    # Try to create the parent dir
    try:
        Path(path).parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    except OSError:
        if not fail_silently:
            raise
