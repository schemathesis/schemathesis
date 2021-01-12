import cgi
import pathlib
import re
import sys
import traceback
import warnings
from contextlib import contextmanager
from json import JSONDecodeError
from typing import (
    Any,
    Callable,
    Dict,
    Generator,
    Generic,
    List,
    NoReturn,
    Optional,
    Set,
    Tuple,
    Type,
    TypeVar,
    Union,
    overload,
)

import requests
import yaml
from hypothesis.reporting import with_reporter
from requests.auth import HTTPDigestAuth
from requests.exceptions import InvalidHeader  # type: ignore
from requests.utils import check_header_validity  # type: ignore
from werkzeug.wrappers import Response as BaseResponse
from werkzeug.wrappers.json import JSONMixin

from .types import Filter, NotSet, RawAuth

try:
    from yaml import CSafeLoader as SafeLoader
except ImportError:
    # pylint: disable=unused-import
    from yaml import SafeLoader  # type: ignore


NOT_SET = NotSet()


def file_exists(path: str) -> bool:
    try:
        return pathlib.Path(path).is_file()
    except OSError:
        # For example, path could be too long
        return False


def is_latin_1_encodable(value: str) -> bool:
    """Header values are encoded to latin-1 before sending."""
    try:
        value.encode("latin-1")
        return True
    except UnicodeEncodeError:
        return False


# Adapted from http.client._is_illegal_header_value
INVALID_HEADER_RE = re.compile(r"\n(?![ \t])|\r(?![ \t\n])")  # pragma: no mutate


def has_invalid_characters(name: str, value: str) -> bool:
    try:
        check_header_validity((name, value))
        return bool(INVALID_HEADER_RE.search(value))
    except InvalidHeader:
        return True


def is_schemathesis_test(func: Callable) -> bool:
    """Check whether test is parametrized with schemathesis."""
    try:
        from .schemas import BaseSchema  # pylint: disable=import-outside-toplevel

        item = getattr(func, "_schemathesis_test", None)
        # Comparison is needed to avoid false-positives when mocks are collected by pytest
        return isinstance(item, BaseSchema)
    except Exception:
        return False


def force_tuple(item: Filter) -> Union[List, Set, Tuple]:
    if not isinstance(item, (list, set, tuple)):
        return (item,)
    return item


def dict_true_values(**kwargs: Any) -> Dict[str, Any]:
    """Create a dict with given kwargs while skipping items where bool(value) evaluates to False."""
    return {key: value for key, value in kwargs.items() if bool(value)}


def dict_not_none_values(**kwargs: Any) -> Dict[str, Any]:
    return {key: value for key, value in kwargs.items() if value is not None}


IGNORED_PATTERNS = (
    "Falsifying example: ",
    "You can add @seed",
    "Failed to reproduce exception. Expected:",
    "Flaky example!",
    "Traceback (most recent call last):",
    "You can reproduce this example by temporarily",
)


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
        if value.startswith(IGNORED_PATTERNS):
            return
        output.append(value)

    # the following context manager is untyped
    with with_reporter(get_output):  # type: ignore
        yield output


def format_exception(error: Exception, include_traceback: bool = False) -> str:
    if include_traceback:
        return "".join(traceback.format_exception(type(error), error, error.__traceback__))
    return "".join(traceback.format_exception_only(type(error), error))


def parse_content_type(content_type: str) -> Tuple[str, str]:
    """Parse Content Type and return main type and subtype."""
    try:
        content_type, _ = cgi.parse_header(content_type)
        main_type, sub_type = content_type.split("/", 1)
    except ValueError as exc:
        raise ValueError(f"Malformed media type: `{content_type}`") from exc
    return main_type.lower(), sub_type.lower()


def is_json_media_type(value: str) -> bool:
    """Detect whether the content type is JSON-compatible.

    For example - ``application/problem+json`` matches.
    """
    main, sub = parse_content_type(value)
    return main == "application" and (sub == "json" or sub.endswith("+json"))


def is_plain_text_media_type(value: str) -> bool:
    """Detect variations of the ``text/plain`` media type."""
    return parse_content_type(value) == ("text", "plain")


def are_content_types_equal(source: str, target: str) -> bool:
    """Check if two content types are the same excluding options."""
    return parse_content_type(source) == parse_content_type(target)


def make_loader(*tags_to_remove: str) -> Type[yaml.SafeLoader]:
    """Create a YAML loader, that doesn't parse specific tokens into Python objects."""
    cls: Type[yaml.SafeLoader] = type("YAMLLoader", (SafeLoader,), {})
    cls.yaml_implicit_resolvers = {
        key: [(tag, regexp) for tag, regexp in mapping if tag not in tags_to_remove]
        for key, mapping in cls.yaml_implicit_resolvers.copy().items()
    }

    # Fix pyyaml scientific notation parse bug
    # See PR: https://github.com/yaml/pyyaml/pull/174 for upstream fix
    cls.add_implicit_resolver(  # type: ignore
        "tag:yaml.org,2002:float",
        re.compile(
            r"""^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+]?[0-9]+)?
                       |[-+]?(?:[0-9][0-9_]*)(?:[eE][-+]?[0-9]+)
                       |\.[0-9_]+(?:[eE][-+]?[0-9]+)?
                       |[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*
                       |[-+]?\.(?:inf|Inf|INF)
                       |\.(?:nan|NaN|NAN))$""",
            re.X,
        ),
        list("-+0123456789."),
    )

    return cls


StringDatesYAMLLoader = make_loader("tag:yaml.org,2002:timestamp")


class WSGIResponse(BaseResponse, JSONMixin):  # pylint: disable=too-many-ancestors
    # We store "requests" request to build a reproduction code
    request: requests.PreparedRequest

    def on_json_loading_failed(self, e: JSONDecodeError) -> NoReturn:
        # We don't need a werkzeug-specific exception when JSON parsing error happens
        raise e


def get_requests_auth(auth: Optional[RawAuth], auth_type: Optional[str]) -> Optional[Union[HTTPDigestAuth, RawAuth]]:
    if auth and auth_type == "digest":
        return HTTPDigestAuth(*auth)
    return auth


GenericResponse = Union[requests.Response, WSGIResponse]  # pragma: no mutate


def get_response_payload(response: GenericResponse) -> str:
    if isinstance(response, requests.Response):
        return response.text
    return response.get_data(as_text=True)


def import_app(path: str) -> Any:
    """Import an application from a string."""
    path, name = (re.split(r":(?![\\/])", path, 1) + [""])[:2]
    __import__(path)
    # accessing the module from sys.modules returns a proper module, while `__import__`
    # may return a parent module (system dependent)
    module = sys.modules[path]
    return getattr(module, name)


Schema = Union[Dict[str, Any], List, str, float, int]


@overload
def traverse_schema(schema: Dict[str, Any], callback: Callable, *args: Any, **kwargs: Any) -> Dict[str, Any]:
    pass


@overload
def traverse_schema(schema: List, callback: Callable, *args: Any, **kwargs: Any) -> List:
    pass


@overload
def traverse_schema(schema: str, callback: Callable, *args: Any, **kwargs: Any) -> str:
    pass


@overload
def traverse_schema(schema: float, callback: Callable, *args: Any, **kwargs: Any) -> float:
    pass


def traverse_schema(schema: Schema, callback: Callable[..., Dict[str, Any]], *args: Any, **kwargs: Any) -> Schema:
    """Apply callback recursively to the given schema."""
    if isinstance(schema, dict):
        schema = callback(schema, *args, **kwargs)
        for key, sub_item in schema.items():
            schema[key] = traverse_schema(sub_item, callback, *args, **kwargs)
    elif isinstance(schema, list):
        schema = [traverse_schema(sub_item, callback, *args, **kwargs) for sub_item in schema]
    return schema


def deprecated_property(*, removed_in: str, replacement: str) -> Callable:
    def wrapper(prop: Callable) -> Callable:
        @property  # type: ignore
        def inner(self: Any) -> Any:
            warnings.warn(
                f"Property `{prop.__name__}` is deprecated and will be removed in Schemathesis {removed_in}. "
                f"Use `{replacement}` instead.",
                DeprecationWarning,
            )
            return prop(self)

        return inner

    return wrapper


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


Result = Union[Ok[T], Err[E]]
