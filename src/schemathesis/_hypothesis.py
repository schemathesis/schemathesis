"""Provide strategies for given endpoint(s) definition."""
import asyncio
import re
from base64 import b64encode
from functools import partial
from typing import Any, Callable, Dict, Generator, Optional, Union
from urllib.parse import quote_plus

import hypothesis
import hypothesis.strategies as st
from hypothesis_jsonschema import from_schema
from requests.exceptions import InvalidHeader  # type: ignore
from requests.utils import check_header_validity  # type: ignore

from ._compat import handle_warnings
from .exceptions import InvalidSchema
from .hooks import get_hook
from .models import Case, Endpoint
from .types import Hook

PARAMETERS = frozenset(("path_parameters", "headers", "cookies", "query", "body", "form_data"))


def create_test(
    endpoint: Endpoint, test: Callable, settings: Optional[hypothesis.settings] = None, seed: Optional[int] = None
) -> Callable:
    """Create a Hypothesis test."""
    strategy = endpoint.as_strategy()
    wrapped_test = hypothesis.given(case=strategy)(test)
    if seed is not None:
        wrapped_test = hypothesis.seed(seed)(wrapped_test)
    original_test = get_original_test(test)
    if asyncio.iscoroutinefunction(original_test):
        wrapped_test.hypothesis.inner_test = make_async_test(original_test)  # type: ignore
    if settings is not None:
        wrapped_test = settings(wrapped_test)
    return add_examples(wrapped_test, endpoint)


def make_test_or_exception(
    endpoint: Endpoint, func: Callable, settings: Optional[hypothesis.settings] = None, seed: Optional[int] = None
) -> Union[Callable, InvalidSchema]:
    try:
        return create_test(endpoint, func, settings, seed=seed)
    except InvalidSchema as exc:
        return exc


def get_original_test(test: Callable) -> Callable:
    """Get the original test function even if it is wrapped by `hypothesis.settings` decorator.

    Applies only to Hypothesis pre 4.42.4 versions.
    """
    # `settings` decorator is applied
    if getattr(test, "_hypothesis_internal_settings_applied", False) and hypothesis.__version_info__ < (4, 42, 4):
        # This behavior was changed due to a bug - https://github.com/HypothesisWorks/hypothesis/issues/2160
        # And since Hypothesis 4.42.4 is no longer required
        return test._hypothesis_internal_test_function_without_warning  # type: ignore
    return test


def make_async_test(test: Callable) -> Callable:
    def async_run(*args: Any, **kwargs: Any) -> None:
        loop = asyncio.get_event_loop()
        coro = test(*args, **kwargs)
        future = asyncio.ensure_future(coro, loop=loop)
        loop.run_until_complete(future)

    return async_run


def get_examples(endpoint: Endpoint) -> Generator[Case, None, None]:
    for name in PARAMETERS:
        parameter = getattr(endpoint, name)
        if parameter is not None and "example" in parameter:
            with handle_warnings():
                strategies = {
                    other: from_schema(getattr(endpoint, other))
                    for other in PARAMETERS - {name}
                    if getattr(endpoint, other) is not None
                }
                static_parameters = {name: parameter["example"]}
                yield _get_case_strategy(endpoint, static_parameters, strategies).example()


def add_examples(test: Callable, endpoint: Endpoint) -> Callable:
    """Add examples to the Hypothesis test, if they are specified in the schema."""
    for case in get_examples(endpoint):
        test = hypothesis.example(case)(test)
    return test


# Adapted from http.client._is_illegal_header_value
INVALID_HEADER_RE = re.compile(r"\n(?![ \t])|\r(?![ \t\n])")  # pragma: no mutate


def _is_latin_1_encodable(value: str) -> bool:
    """Header values are encoded to latin-1 before sending.

    We need to generate valid payload.
    """
    try:
        value.encode("latin-1")
        return True
    except UnicodeEncodeError:
        return False


def _has_invalid_characters(name: str, value: str) -> bool:
    try:
        check_header_validity((name, value))
        return bool(INVALID_HEADER_RE.search(value))
    except InvalidHeader:
        return True


def is_valid_header(headers: Dict[str, str]) -> bool:
    """Verify if the generated headers are valid."""
    for name, value in headers.items():
        if not _is_latin_1_encodable(value):
            return False
        if _has_invalid_characters(name, value):
            return False
    return True


def is_surrogate(item: Any) -> bool:
    return isinstance(item, str) and bool(re.search(r"[\ud800-\udfff]", item))


def is_valid_query(query: Dict[str, Any]) -> bool:
    """Surrogates are not allowed in a query string.

    `requests` and `werkzeug` will fail to send it to the application.
    """
    for name, value in query.items():
        if is_surrogate(name) or is_surrogate(value):
            return False
    return True


def get_case_strategy(endpoint: Endpoint) -> st.SearchStrategy:
    """Create a strategy for a complete test case.

    Path & endpoint are static, the others are JSON schemas.
    """
    strategies = {}
    static_kwargs: Dict[str, Any] = {"endpoint": endpoint}
    try:
        for parameter in PARAMETERS:
            value = getattr(endpoint, parameter)
            if value is not None:
                if parameter == "path_parameters":
                    strategies[parameter] = (
                        from_schema(value).filter(filter_path_parameters).map(quote_all)  # type: ignore
                    )
                elif parameter == "headers":
                    strategies[parameter] = from_schema(value).filter(is_valid_header)  # type: ignore
                elif parameter == "query":
                    strategies[parameter] = from_schema(value).filter(is_valid_query)  # type: ignore
                else:
                    strategies[parameter] = from_schema(value)  # type: ignore
            else:
                static_kwargs[parameter] = None
        return _get_case_strategy(endpoint, static_kwargs, strategies)
    except AssertionError:
        raise InvalidSchema("Invalid schema for this endpoint")


def filter_path_parameters(parameters: Dict[str, Any]) -> bool:
    """Single "." chars are excluded from path by urllib3.

    In this case one variable in the path template will be empty, which will lead to 404 in most of the cases.
    Because of it this case doesn't bring much value and might lead to false positives results of Schemathesis runs.
    """
    return not any(value == "." for value in parameters.values())


def quote_all(parameters: Dict[str, Any]) -> Dict[str, Any]:
    return {key: quote_plus(value) if isinstance(value, str) else value for key, value in parameters.items()}


def _get_case_strategy(
    endpoint: Endpoint, extra_static_parameters: Dict[str, Any], strategies: Dict[str, st.SearchStrategy]
) -> st.SearchStrategy:
    static_parameters: Dict[str, Any] = {"endpoint": endpoint, **extra_static_parameters}
    if endpoint.method == "GET":
        if endpoint.body is not None:
            raise InvalidSchema("Body parameters are defined for GET request.")
        static_parameters["body"] = None
        strategies.pop("body", None)
    _apply_hooks(strategies, get_hook)
    _apply_hooks(strategies, endpoint.schema.get_hook)
    return st.builds(partial(Case, **static_parameters), **strategies)


def _apply_hooks(strategies: Dict[str, st.SearchStrategy], getter: Callable[[str], Optional[Hook]]) -> None:
    for key, strategy in strategies.items():
        hook = getter(key)
        if hook is not None:
            strategies[key] = hook(strategy)


def register_string_format(name: str, strategy: st.SearchStrategy) -> None:
    if not isinstance(name, str):
        raise TypeError(f"name must be of type {str}, not {type(name)}")
    if not isinstance(strategy, st.SearchStrategy):
        raise TypeError(f"strategy must be of type {st.SearchStrategy}, not {type(strategy)}")
    from hypothesis_jsonschema._from_schema import STRING_FORMATS  # pylint: disable=import-outside-toplevel

    STRING_FORMATS[name] = strategy


def init_default_strategies() -> None:
    register_string_format("binary", st.binary())
    register_string_format("byte", st.binary().map(lambda x: b64encode(x).decode()))
