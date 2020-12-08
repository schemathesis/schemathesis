import re
from base64 import b64encode
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import quote_plus

from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema
from requests.auth import _basic_auth_str

from ... import utils
from ...constants import DataGenerationMethod
from ...exceptions import InvalidSchema
from ...hooks import GLOBAL_HOOK_DISPATCHER, HookContext, HookDispatcher
from ...models import Case, Endpoint
from ...stateful import Feedback
from ...utils import NOT_SET
from .parameters import parameters_to_json_schema

PARAMETERS = frozenset(("path_parameters", "headers", "cookies", "query", "body"))
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


@st.composite  # type: ignore
def get_case_strategy(  # pylint: disable=too-many-locals
    draw: Callable,
    endpoint: Endpoint,
    hooks: Optional[HookDispatcher] = None,
    feedback: Optional[Feedback] = None,
    data_generation_method: DataGenerationMethod = DataGenerationMethod.default(),
    path_parameters: Any = NOT_SET,
    headers: Any = NOT_SET,
    cookies: Any = NOT_SET,
    query: Any = NOT_SET,
    body: Any = NOT_SET,
) -> Any:
    to_strategy = {DataGenerationMethod.positive: make_positive_strategy}[data_generation_method]

    context = HookContext(endpoint)

    if path_parameters is NOT_SET:
        strategy = get_parameters_strategy(endpoint, to_strategy, "path_parameters")
        strategy = apply_hooks(endpoint, context, hooks, strategy, "path_parameters")
        path_parameters = draw(strategy)
    if headers is NOT_SET:
        strategy = get_parameters_strategy(endpoint, to_strategy, "headers")
        strategy = apply_hooks(endpoint, context, hooks, strategy, "headers")
        headers = draw(strategy)
    if cookies is NOT_SET:
        strategy = get_parameters_strategy(endpoint, to_strategy, "cookies")
        strategy = apply_hooks(endpoint, context, hooks, strategy, "cookies")
        cookies = draw(strategy)
    if query is NOT_SET:
        strategy = get_parameters_strategy(endpoint, to_strategy, "query")
        strategy = apply_hooks(endpoint, context, hooks, strategy, "query")
        query = draw(strategy)

    media_type = None
    if body is NOT_SET:
        if endpoint.body:
            parameter = draw(st.sampled_from(endpoint.body))
            schema = parameter.as_json_schema()
            strategy = to_strategy(schema)
            media_type = parameter.media_type
            body = draw(strategy)
        else:
            body = None
    else:
        # TODO. detect the proper one
        media_type = "application/json"
    if endpoint.schema.validate_schema and endpoint.method.upper() == "GET" and endpoint.body:
        raise InvalidSchema("Body parameters are defined for GET request.")
    return Case(
        endpoint=endpoint,
        feedback=feedback,
        media_type=media_type,
        path_parameters=path_parameters,
        headers=headers,
        cookies=cookies,
        query=query,
        body=body,
    )


def get_parameters_strategy(
    endpoint: Endpoint, to_strategy: Callable[[Dict[str, Any]], st.SearchStrategy], name: str
) -> st.SearchStrategy:
    parameters = getattr(endpoint, name)
    if parameters:
        schema = parameters_to_json_schema(parameters)
        strategy = to_strategy(schema)
        location = {"headers": "header", "cookies": "cookie", "path_parameters": "path"}.get(name, name)
        serialize = endpoint.get_hypothesis_conversions(location)
        if serialize is not None:
            strategy = strategy.map(serialize)
        if name == "path_parameters":
            strategy = strategy.filter(filter_path_parameters).map(quote_all)
        elif name in ("headers", "cookies"):
            strategy = strategy.filter(is_valid_header)
        elif name == "query":
            strategy = strategy.filter(is_valid_query)
        return strategy
    return st.just(None)


def prepare_strategy(
    parameter: str,
    schema: Dict[str, Any],
    map_func: Optional[Callable],
    data_generation_method: DataGenerationMethod = DataGenerationMethod.default(),
) -> st.SearchStrategy:
    """Create a strategy for a schema and add location-specific filters & maps."""
    to_strategy = {DataGenerationMethod.positive: make_positive_strategy}[data_generation_method]
    strategy = to_strategy(schema)
    if map_func is not None:
        strategy = strategy.map(map_func)
    if parameter == "path_parameters":
        strategy = strategy.filter(filter_path_parameters).map(quote_all)  # type: ignore
    elif parameter in ("headers", "cookies"):
        strategy = strategy.filter(is_valid_header)  # type: ignore
    elif parameter == "query":
        strategy = strategy.filter(is_valid_query)  # type: ignore
    return strategy


def make_positive_strategy(schema: Dict[str, Any]) -> st.SearchStrategy:
    return from_schema(schema, custom_formats=STRING_FORMATS)


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


def apply_hooks(
    endpoint: Endpoint, context: HookContext, hooks: Optional[HookDispatcher], strategy: st.SearchStrategy, key: str
) -> st.SearchStrategy:
    strategy = __apply_hooks(context, GLOBAL_HOOK_DISPATCHER, strategy, key)
    strategy = __apply_hooks(context, endpoint.schema.hooks, strategy, key)
    if hooks is not None:
        strategy = __apply_hooks(context, hooks, strategy, key)
    return strategy


def __apply_hooks(
    context: HookContext, hooks: HookDispatcher, strategy: st.SearchStrategy, key: str
) -> st.SearchStrategy:
    for hook in hooks.get_all_by_name(f"before_generate_{key}"):
        strategy = hook(context, strategy)
    return strategy
