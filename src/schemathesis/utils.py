import cgi
import functools
import operator
import pathlib
import random
import re
import sys
import traceback
import warnings
from contextlib import contextmanager
from copy import copy, deepcopy
from datetime import datetime, timezone
from inspect import getfullargspec
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

import pytest
import requests
import yaml
import yarl
from hypothesis import strategies as st
from hypothesis.core import is_invalid_test
from hypothesis.reporting import with_reporter
from hypothesis.strategies import SearchStrategy
from requests.auth import HTTPDigestAuth
from requests.exceptions import InvalidHeader  # type: ignore
from requests.utils import check_header_validity
from werkzeug.wrappers import Response as BaseResponse

from ._compat import InferType, JSONMixin, get_signature
from .constants import USER_AGENT, DataGenerationMethod
from .exceptions import SkipTest, UsageError
from .types import DataGenerationMethodInput, Filter, GenericTest, NotSet, RawAuth

try:
    from yaml import CSafeLoader as SafeLoader
except ImportError:
    from yaml import SafeLoader  # type: ignore


NOT_SET = NotSet()


def file_exists(path: str) -> bool:
    try:
        return pathlib.Path(path).is_file()
    except OSError:
        # For example, path could be too long
        return False


def is_filename(value: str) -> bool:
    """Detect if the input string is a filename by checking its extension."""
    return bool(pathlib.Path(value).suffix)


def is_latin_1_encodable(value: str) -> bool:
    """Header values are encoded to latin-1 before sending."""
    try:
        value.encode("latin-1")
        return True
    except UnicodeEncodeError:
        return False


# Adapted from http.client._is_illegal_header_value
INVALID_HEADER_RE = re.compile(r"\n(?![ \t])|\r(?![ \t\n])")


def has_invalid_characters(name: str, value: str) -> bool:
    try:
        check_header_validity((name, value))
        return bool(INVALID_HEADER_RE.search(value))
    except InvalidHeader:
        return True


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
    "Falsifying explicit example: ",
    "You can add @seed",
    "Failed to reproduce exception. Expected:",
    "Flaky example!",
    "Traceback (most recent call last):",
    "You can reproduce this example by temporarily",
    "Unreliable test timings",
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
    """Format exception as text."""
    error_type = type(error)
    if include_traceback:
        lines = traceback.format_exception(error_type, error, error.__traceback__)
    else:
        lines = traceback.format_exception_only(error_type, error)
    return "".join(lines).strip()


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


def is_xml_media_type(value: str) -> bool:
    """Detect variations of the ``application/xml`` media type."""
    _, sub = parse_content_type(value)
    return sub == "xml" or sub.endswith("+xml")


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

    def construct_mapping(self: SafeLoader, node: yaml.Node, deep: bool = False) -> Dict[str, Any]:
        if isinstance(node, yaml.MappingNode):
            self.flatten_mapping(node)  # type: ignore
        mapping = {}
        for key_node, value_node in node.value:
            # If the key has a tag different from `str` - use its string value.
            # With this change all integer keys or YAML 1.1 boolean-ish values like "on" / "off" will not be cast to
            # a different type
            if key_node.tag != "tag:yaml.org,2002:str":
                key = key_node.value
            else:
                key = self.construct_object(key_node, deep)  # type: ignore
            mapping[key] = self.construct_object(value_node, deep)  # type: ignore
        return mapping

    cls.construct_mapping = construct_mapping  # type: ignore
    return cls


StringDatesYAMLLoader = make_loader("tag:yaml.org,2002:timestamp")


class WSGIResponse(BaseResponse, JSONMixin):
    # We store "requests" request to build a reproduction code
    request: requests.PreparedRequest

    def on_json_loading_failed(self, e: JSONDecodeError) -> NoReturn:
        # We don't need a werkzeug-specific exception when JSON parsing error happens
        raise e


def get_requests_auth(auth: Optional[RawAuth], auth_type: Optional[str]) -> Optional[Union[HTTPDigestAuth, RawAuth]]:
    if auth and auth_type == "digest":
        return HTTPDigestAuth(*auth)
    return auth


GenericResponse = Union[requests.Response, WSGIResponse]


def copy_response(response: GenericResponse) -> GenericResponse:
    """Create a copy of the given response as far as it makes sense."""
    if isinstance(response, requests.Response):
        # Hooks are not copyable. Keep them out and copy the rest
        hooks = None
        if response.request is not None:
            hooks = response.request.hooks["response"]
            response.request.hooks["response"] = []
        copied_response = deepcopy(response)
        if hooks is not None:
            copied_response.request.hooks["response"] = hooks
        copied_response.raw = response.raw
        copied_response.verify = getattr(response, "verify", True)  # type: ignore[union-attr]
        return copied_response
    # Can't deepcopy WSGI response due to generators inside (`response.freeze` doesn't completely help)
    response.freeze()
    copied_response = copy(response)
    copied_response.request = deepcopy(response.request)
    return copied_response


def get_response_payload(response: GenericResponse) -> str:
    if isinstance(response, requests.Response):
        return response.text
    return response.get_data(as_text=True)


def import_app(path: str) -> Any:
    """Import an application from a string."""
    path, name = (re.split(r":(?![\\/])", path, maxsplit=1) + [""])[:2]
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


def _warn_deprecation(*, thing: str, removed_in: str, replacement: str) -> None:
    warnings.warn(
        f"Property `{thing}` is deprecated and will be removed in Schemathesis {removed_in}. "
        f"Use `{replacement}` instead.",
        DeprecationWarning,
        stacklevel=1,
    )


def deprecated_property(*, removed_in: str, replacement: str) -> Callable:
    def wrapper(prop: Callable) -> Callable:
        @property  # type: ignore
        def inner(self: Any) -> Any:
            _warn_deprecation(thing=prop.__name__, removed_in=removed_in, replacement=replacement)
            return prop(self)

        return inner

    return wrapper


def deprecated(*, removed_in: str, replacement: str) -> Callable:
    def wrapper(func: Callable) -> Callable:
        def inner(*args: Any, **kwargs: Any) -> Any:
            _warn_deprecation(thing=func.__name__, removed_in=removed_in, replacement=replacement)
            return func(*args, **kwargs)

        return inner

    return wrapper


def setup_headers(kwargs: Dict[str, Any]) -> None:
    headers = kwargs.setdefault("headers", {})
    if "user-agent" not in {header.lower() for header in headers}:
        kwargs["headers"]["User-Agent"] = USER_AGENT


def require_relative_url(url: str) -> None:
    """Raise an error if the URL is not relative."""
    if yarl.URL(url).is_absolute():
        raise ValueError("Schema path should be relative for WSGI/ASGI loaders")


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
GivenInput = Union[SearchStrategy, InferType]
PARAMETRIZE_MARKER = "_schemathesis_test"
GIVEN_ARGS_MARKER = "_schemathesis_given_args"
GIVEN_KWARGS_MARKER = "_schemathesis_given_kwargs"


def get_given_args(func: GenericTest) -> Tuple:
    return getattr(func, GIVEN_ARGS_MARKER, ())


def get_given_kwargs(func: GenericTest) -> Dict[str, Any]:
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


def merge_given_args(func: GenericTest, args: Tuple, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Merge positional arguments to ``@schema.given`` into a dictionary with keyword arguments.

    Kwargs are modified inplace.
    """
    if args:
        argspec = getfullargspec(func)
        for name, strategy in zip(reversed([arg for arg in argspec.args if arg != "case"]), reversed(args)):
            kwargs[name] = strategy
    return kwargs


def validate_given_args(func: GenericTest, args: Tuple, kwargs: Dict[str, Any]) -> Optional[Callable]:
    signature = get_signature(func)
    return is_invalid_test(func, signature, args, kwargs)  # type: ignore


def compose(*functions: Callable) -> Callable:
    """Compose multiple functions into a single one."""

    def noop(x: Any) -> Any:
        return x

    return functools.reduce(lambda f, g: lambda x: f(g(x)), functions, noop)


def maybe_set_assertion_message(exc: AssertionError, check_name: str) -> str:
    message = str(exc)
    if not message:
        message = f"Check '{check_name}' failed"
        exc.args = (message,)
    return message


def prepare_data_generation_methods(data_generation_methods: DataGenerationMethodInput) -> List[DataGenerationMethod]:
    if isinstance(data_generation_methods, DataGenerationMethod):
        return [data_generation_methods]
    return list(data_generation_methods)


def merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Merge two dictionaries recursively."""
    for key in b:
        if key in a:
            if isinstance(a[key], dict) and isinstance(b[key], dict):
                merge(a[key], b[key])
            else:
                a[key] = b[key]
        else:
            a[key] = b[key]
    return a


def current_datetime() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def combine_strategies(strategies: List[st.SearchStrategy]) -> st.SearchStrategy:
    """Combine a list of strategies into a single one.

    If the input is `[a, b, c]`, then the result is equivalent to `a | b | c`.
    """
    return functools.reduce(operator.or_, strategies[1:], strategies[0])


def skip(operation_name: str) -> NoReturn:
    raise SkipTest(f"It is not possible to generate negative test cases for `{operation_name}`")


def fast_deepcopy(value: Any) -> Any:
    """A specialized version of `deepcopy` that copies only `dict` and `list`.

    It is on average 3x faster than `deepcopy` and given the amount of calls, it is an important optimization.
    """
    return _fast_deepcopy(value)


def _fast_deepcopy(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _fast_deepcopy(v) for key, v in value.items()}
    if isinstance(value, list):
        return [_fast_deepcopy(v) for v in value]
    return value


CASE_ID_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
BASE = len(CASE_ID_ALPHABET)
# Separate `Random` as Hypothesis might interfere with the default one
RANDOM = random.Random()


def generate_random_case_id(length: int = 6) -> str:
    number = RANDOM.randint(62 ** (length - 1), 62**length - 1)
    output = ""
    while number > 0:
        number, rem = divmod(number, BASE)
        output += CASE_ID_ALPHABET[rem]
    return output
