import cgi
import traceback
import warnings
from contextlib import contextmanager
from functools import wraps
from typing import Any, Callable, Generator, List, Mapping, Set, Tuple, Union
from urllib.parse import urlsplit, urlunsplit

from hypothesis.reporting import with_reporter

from .types import Filter

NOT_SET = object()


def deprecated(func: Callable, message: str) -> Callable:
    """Emit a warning if the given function is used."""

    @wraps(func)  # pragma: no mutate
    def inner(*args: Any, **kwargs: Any) -> Any:
        warnings.warn(message, DeprecationWarning)
        return func(*args, **kwargs)

    return inner


def is_schemathesis_test(func: Callable) -> bool:
    """Check whether test is parametrized with schemathesis."""
    try:
        return hasattr(func, "_schemathesis_test")
    except Exception:
        return False


def get_base_url(uri: str) -> str:
    """Remove the path part off the given uri."""
    parts = urlsplit(uri)[:2] + ("", "", "")
    return urlunsplit(parts)


def force_tuple(item: Filter) -> Union[List, Set, Tuple]:
    if not isinstance(item, (list, set, tuple)):
        return (item,)
    return item


def dict_true_values(**kwargs: Any) -> Mapping[str, Any]:
    """Create dict with given kwargs while skipping items where bool(value) evaluates to False."""
    return {key: value for key, value in kwargs.items() if bool(value)}


def dict_not_none_values(**kwargs: Any) -> Mapping[str, Any]:
    return {key: value for key, value in kwargs.items() if value is not None}


@contextmanager
def capture_hypothesis_output() -> Generator[List[str], None, None]:
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
        if value.startswith(
            (
                "Falsifying example: ",
                "You can add @seed",
                "Failed to reproduce exception. Expected:",
                "Flaky example!",
                "Inconsistent test results!",
            )
        ):
            return
        output.append(value)

    # the following context manager is untyped
    with with_reporter(get_output):  # type: ignore
        yield output


def format_exception(error: Exception) -> str:
    return "".join(traceback.format_exception_only(type(error), error))


def parse_content_type(content_type: str) -> Tuple[str, str]:
    """Parse Content Type and return main type and subtype."""
    content_type, _ = cgi.parse_header(content_type)
    main_type, sub_type = content_type.split("/", 1)
    return main_type.lower(), sub_type.lower()


def are_content_types_equal(source: str, target: str) -> bool:
    """Check if two content types are the same excluding options."""
    return parse_content_type(source) == parse_content_type(target)
