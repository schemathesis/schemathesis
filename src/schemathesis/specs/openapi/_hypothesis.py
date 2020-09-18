import re
from base64 import b64encode
from functools import partial
from typing import Any, Callable, Dict, Optional, Tuple, Union
from urllib.parse import quote_plus

from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema
from requests.auth import _basic_auth_str

from ... import utils
from ...exceptions import InvalidSchema
from ...hooks import GLOBAL_HOOK_DISPATCHER, HookContext, HookDispatcher
from ...models import Case, Endpoint
from ...stateful import Feedback

PARAMETERS = frozenset(("path_parameters", "headers", "cookies", "query", "body", "form_data"))
SLASH = "/"
STRING_FORMATS = {}


def register_string_format(name: str, strategy: st.SearchStrategy) -> None:
    """Register a new strategy for generating data for specific string "format"."""
    if not isinstance(name, str):
        raise TypeError(f"name must be of type {str}, not {type(name)}")
    if not isinstance(strategy, st.SearchStrategy):
        raise TypeError(f"strategy must be of type {st.SearchStrategy}, not {type(strategy)}")

    STRING_FORMATS[name] = strategy


def init_default_strategies() -> None:
    """Register all default "format" strategies."""
    register_string_format("binary", st.binary())
    register_string_format("byte", st.binary().map(lambda x: b64encode(x).decode()))

    def make_basic_auth_str(item: Tuple[str, str]) -> str:
        return _basic_auth_str(*item)

    latin1_text = st.text(alphabet=st.characters(min_codepoint=0, max_codepoint=255))

    register_string_format("_basic_auth", st.tuples(latin1_text, latin1_text).map(make_basic_auth_str))  # type: ignore
    register_string_format("_bearer_auth", st.text().map("Bearer {}".format))


def is_valid_header(headers: Dict[str, Any]) -> bool:
    """Verify if the generated headers are valid."""
    for name, value in headers.items():
        if not isinstance(value, str):
            return False
        if not utils.is_latin_1_encodable(value):
            return False
        if utils.has_invalid_characters(name, value):
            return False
    return True


def is_illegal_surrogate(item: Any) -> bool:
    return isinstance(item, str) and bool(re.search(r"[\ud800-\udfff]", item))


def is_valid_query(query: Dict[str, Any]) -> bool:
    """Surrogates are not allowed in a query string.

    `requests` and `werkzeug` will fail to send it to the application.
    """
    for name, value in query.items():
        if is_illegal_surrogate(name) or is_illegal_surrogate(value):
            return False
    return True


def get_case_strategy(
    endpoint: Endpoint, hooks: Optional[HookDispatcher] = None, feedback: Optional[Feedback] = None
) -> st.SearchStrategy:
    """Create a strategy for a complete test case.

    Path & endpoint are static, the others are JSON schemas.
    """
    strategies = {}
    static_kwargs: Dict[str, Any] = {"feedback": feedback}
    for parameter in PARAMETERS:
        value = getattr(endpoint, parameter)
        if value is not None:
            location = {"headers": "header", "cookies": "cookie", "path_parameters": "path"}.get(parameter, parameter)
            strategies[parameter] = prepare_strategy(parameter, value, endpoint.get_hypothesis_conversions(location))
        else:
            static_kwargs[parameter] = None
    return _get_case_strategy(endpoint, static_kwargs, strategies, hooks)


def to_bytes(value: Union[str, bytes, int, bool, float]) -> bytes:
    return str(value).encode(errors="ignore")


def is_valid_form_data(form_data: Any) -> bool:
    return isinstance(form_data, dict)


def prepare_form_data(form_data: Dict[str, Any]) -> Dict[str, Any]:
    for name, value in form_data.items():
        if isinstance(value, list):
            form_data[name] = [to_bytes(item) if not isinstance(item, (bytes, str, int)) else item for item in value]
        elif not isinstance(value, (bytes, str, int)):
            form_data[name] = to_bytes(value)
    return form_data


def prepare_strategy(parameter: str, value: Dict[str, Any], map_func: Optional[Callable]) -> st.SearchStrategy:
    """Create a strategy for a schema and add location-specific filters & maps."""
    strategy = from_schema(value, custom_formats=STRING_FORMATS)
    if map_func is not None:
        strategy = strategy.map(map_func)
    if parameter == "path_parameters":
        strategy = strategy.filter(filter_path_parameters).map(quote_all)  # type: ignore
    elif parameter in ("headers", "cookies"):
        strategy = strategy.filter(is_valid_header)  # type: ignore
    elif parameter == "query":
        strategy = strategy.filter(is_valid_query)  # type: ignore
    elif parameter == "form_data":
        strategy = strategy.filter(is_valid_form_data).map(prepare_form_data)  # type: ignore
    return strategy


def filter_path_parameters(parameters: Dict[str, Any]) -> bool:
    """Single "." chars and empty strings "" are excluded from path by urllib3.

    A path containing to "/" or "%2F" will lead to ambiguous path resolution in
    many frameworks and libraries, such behaviour have been observed in both
    WSGI and ASGI applications.

    In this case one variable in the path template will be empty, which will lead to 404 in most of the cases.
    Because of it this case doesn't bring much value and might lead to false positives results of Schemathesis runs.
    """

    path_parameter_blacklist = (".", SLASH, "")

    return not any(
        (value in path_parameter_blacklist or is_illegal_surrogate(value) or isinstance(value, str) and SLASH in value)
        for value in parameters.values()
    )


def quote_all(parameters: Dict[str, Any]) -> Dict[str, Any]:
    """Apply URL quotation for all values in a dictionary."""
    return {key: quote_plus(value) if isinstance(value, str) else value for key, value in parameters.items()}


def _get_case_strategy(
    endpoint: Endpoint,
    extra_static_parameters: Dict[str, Any],
    strategies: Dict[str, st.SearchStrategy],
    hook_dispatcher: Optional[HookDispatcher] = None,
) -> st.SearchStrategy[Case]:
    static_parameters: Dict[str, Any] = {"endpoint": endpoint, **extra_static_parameters}
    if endpoint.schema.validate_schema and endpoint.method == "GET":
        if endpoint.body is not None:
            raise InvalidSchema("Body parameters are defined for GET request.")
        static_parameters["body"] = None
        strategies.pop("body", None)
    context = HookContext(endpoint)
    _apply_hooks(strategies, GLOBAL_HOOK_DISPATCHER, context)
    _apply_hooks(strategies, endpoint.schema.hooks, context)
    if hook_dispatcher is not None:
        _apply_hooks(strategies, hook_dispatcher, context)
    return st.builds(partial(Case, **static_parameters), **strategies)


def _apply_hooks(strategies: Dict[str, st.SearchStrategy], dispatcher: HookDispatcher, context: HookContext) -> None:
    for key in strategies:
        for hook in dispatcher.get_all_by_name(f"before_generate_{key}"):
            # Get the strategy on each hook to pass the first hook output as an input to the next one
            strategy = strategies[key]
            strategies[key] = hook(context, strategy)
