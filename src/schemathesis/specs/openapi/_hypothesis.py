import json
import re
from base64 import b64encode
from contextlib import contextmanager, suppress
from typing import Any, Callable, Dict, Generator, Iterable, Optional, Tuple, Union
from urllib.parse import quote_plus

from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema
from requests.auth import _basic_auth_str

from ... import utils
from ...constants import DataGenerationMethod
from ...exceptions import InvalidSchema
from ...hooks import GLOBAL_HOOK_DISPATCHER, HookContext, HookDispatcher
from ...models import APIOperation, Case
from ...schemas import BaseSchema
from ...types import NotSet
from ...utils import NOT_SET
from .constants import LOCATION_TO_CONTAINER
from .parameters import OpenAPIParameter, parameters_to_json_schema

PARAMETERS = frozenset(("path_parameters", "headers", "cookies", "query", "body"))
SLASH = "/"
STRING_FORMATS = {}


def register_string_format(name: str, strategy: st.SearchStrategy) -> None:
    """Register a new strategy for generating data for specific string "format".

    :param str name: Format name. It should correspond the one used in the API schema as the "format" keyword value.
    :param strategy: Hypothesis strategy you'd like to use to generate values for this format.
    """
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
    operation: APIOperation,
    hooks: Optional[HookDispatcher] = None,
    data_generation_method: DataGenerationMethod = DataGenerationMethod.default(),
    path_parameters: Union[NotSet, Dict[str, Any]] = NOT_SET,
    headers: Union[NotSet, Dict[str, Any]] = NOT_SET,
    cookies: Union[NotSet, Dict[str, Any]] = NOT_SET,
    query: Union[NotSet, Dict[str, Any]] = NOT_SET,
    body: Any = NOT_SET,
) -> Any:
    """A strategy that creates `Case` instances.

    Explicit `path_parameters`, `headers`, `cookies`, `query`, `body` arguments will be used in the resulting `Case`
    object.

    If such explicit parameters are composite (not `body`) and don't provide the whole set of parameters for that
    location, then we generate what is missing and merge these two parts. Note that if parameters are optional, then
    they may remain absent.

    The primary purpose of this behavior is to prevent sending incomplete explicit examples by generating missing parts
    as it works with `body`.
    """
    to_strategy = {DataGenerationMethod.positive: make_positive_strategy}[data_generation_method]

    context = HookContext(operation)

    with detect_invalid_schema(operation):
        path_parameters = get_parameters_value(path_parameters, "path", draw, operation, context, hooks, to_strategy)
        headers = get_parameters_value(headers, "header", draw, operation, context, hooks, to_strategy)
        cookies = get_parameters_value(cookies, "cookie", draw, operation, context, hooks, to_strategy)
        query = get_parameters_value(query, "query", draw, operation, context, hooks, to_strategy)

        media_type = None
        if body is NOT_SET:
            if operation.body:
                parameter = draw(st.sampled_from(operation.body.items))
                strategy = _get_body_strategy(parameter, to_strategy, operation.schema)
                strategy = apply_hooks(operation, context, hooks, strategy, "body")
                media_type = parameter.media_type
                body = draw(strategy)
        else:
            media_types = operation.get_request_payload_content_types() or ["application/json"]
            # Take the first available media type.
            # POSSIBLE IMPROVEMENT:
            #   - Test examples for each available media type on Open API 2.0;
            #   - On Open API 3.0, media types are explicit, and each example has it.
            #     We can pass `OpenAPIBody.media_type` here from the examples handling code.
            media_type = media_types[0]

    if operation.schema.validate_schema and operation.method.upper() == "GET" and operation.body:
        raise InvalidSchema("Body parameters are defined for GET request.")
    return Case(
        operation=operation,
        media_type=media_type,
        path_parameters=path_parameters,
        headers=headers,
        cookies=cookies,
        query=query,
        body=body,
    )


YAML_PARSING_ISSUE_MESSAGE = (
    "The API schema contains non-string keys. "
    "If you store your schema in YAML, it is likely caused by unquoted keys parsed as "
    "non-strings. For example, `on` is parsed as boolean `true`, "
    "but `'on'` (with quotes) is a string `'on'`. See more information at https://noyaml.com/."
)


@contextmanager
def detect_invalid_schema(operation: APIOperation) -> Generator[None, None, None]:
    """Detect common issues with schemas."""
    try:
        yield
    except TypeError as exc:
        if is_yaml_parsing_issue(operation):
            raise InvalidSchema(YAML_PARSING_ISSUE_MESSAGE) from exc
        raise


def is_yaml_parsing_issue(operation: APIOperation) -> bool:
    """Detect whether the API operation has problems because of YAML syntax.

    For example, unquoted 'on' is parsed as `True`.
    """
    try:
        # Sorting keys involves their comparison, when there is a non-string value, it leads to a TypeError
        json.dumps(operation.schema.raw_schema, sort_keys=True)
    except TypeError:
        return True
    return False


def _get_body_strategy(
    parameter: OpenAPIParameter, to_strategy: Callable[[Dict[str, Any]], st.SearchStrategy], parent_schema: BaseSchema
) -> st.SearchStrategy:
    schema = parameter.as_json_schema()
    schema = parent_schema.prepare_schema(schema)
    strategy = to_strategy(schema)
    if not parameter.is_required:
        strategy |= st.just(NOT_SET)
    return strategy


def get_parameters_value(
    value: Union[NotSet, Dict[str, Any]],
    location: str,
    draw: Callable,
    operation: APIOperation,
    context: HookContext,
    hooks: Optional[HookDispatcher],
    to_strategy: Callable[[Dict[str, Any]], st.SearchStrategy],
) -> Dict[str, Any]:
    """Get the final value for the specified location.

    If the value is not set, then generate it from the relevant strategy. Otherwise, check what is missing in it and
    generate those parts.
    """
    if isinstance(value, NotSet):
        strategy = get_parameters_strategy(operation, to_strategy, location)
        strategy = apply_hooks(operation, context, hooks, strategy, location)
        return draw(strategy)
    strategy = get_parameters_strategy(operation, to_strategy, location, exclude=value.keys())
    strategy = apply_hooks(operation, context, hooks, strategy, location)
    value.update(draw(strategy))
    return value


def get_parameters_strategy(
    operation: APIOperation,
    to_strategy: Callable[[Dict[str, Any]], st.SearchStrategy],
    location: str,
    exclude: Iterable[str] = (),
) -> st.SearchStrategy:
    """Create a new strategy for the case's component from the API operation parameters."""
    parameters = getattr(operation, LOCATION_TO_CONTAINER[location])
    if parameters:
        schema = parameters_to_json_schema(parameters)
        if not operation.schema.validate_schema and location == "path":
            # If schema validation is disabled, we try to generate data even if the parameter definition
            # contains errors.
            # In this case, we know that the `required` keyword should always be `True`.
            schema["required"] = list(schema["properties"])
        schema = operation.schema.prepare_schema(schema)
        for name in exclude:
            # Values from `exclude` are not necessarily valid for the schema - they come from user-defined examples
            # that may be invalid
            schema["properties"].pop(name, None)
            with suppress(ValueError):
                schema["required"].remove(name)
        strategy = to_strategy(schema)
        serialize = operation.get_parameter_serializer(location)
        if serialize is not None:
            strategy = strategy.map(serialize)
        filter_func = {
            "path": is_valid_path,
            "header": is_valid_header,
            "cookie": is_valid_header,
            "query": is_valid_query,
        }[location]
        strategy = strategy.filter(filter_func)
        map_func = {"path": quote_all}.get(location)
        if map_func:
            strategy = strategy.map(map_func)
        return strategy
    # No parameters defined for this location
    return st.none()


def make_positive_strategy(schema: Dict[str, Any]) -> st.SearchStrategy:
    return from_schema(schema, custom_formats=STRING_FORMATS)


def is_valid_path(parameters: Dict[str, Any]) -> bool:
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
    operation: APIOperation,
    context: HookContext,
    hooks: Optional[HookDispatcher],
    strategy: st.SearchStrategy[Case],
    location: str,
) -> st.SearchStrategy[Case]:
    """Apply all `before_generate_` hooks related to the given location."""
    strategy = _apply_hooks(context, GLOBAL_HOOK_DISPATCHER, strategy, location)
    strategy = _apply_hooks(context, operation.schema.hooks, strategy, location)
    if hooks is not None:
        strategy = _apply_hooks(context, hooks, strategy, location)
    return strategy


def _apply_hooks(
    context: HookContext, hooks: HookDispatcher, strategy: st.SearchStrategy[Case], location: str
) -> st.SearchStrategy[Case]:
    """Apply all `before_generate_` hooks related to the given location & dispatcher."""
    container = LOCATION_TO_CONTAINER[location]
    for hook in hooks.get_all_by_name(f"before_generate_{container}"):
        strategy = hook(context, strategy)
    return strategy
