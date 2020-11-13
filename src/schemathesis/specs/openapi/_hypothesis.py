import re
from base64 import b64encode
from functools import partial
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar, cast
from urllib.parse import quote_plus

from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema
from requests.auth import _basic_auth_str

from ... import serializers, utils
from ...constants import DataGenerationMethod
from ...hooks import GLOBAL_HOOK_DISPATCHER, HookContext, HookDispatcher
from ...models import Case, Endpoint
from ...stateful import Feedback
from .parameters import OpenAPIParameter

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


T = TypeVar("T")


def get_case_strategy(
    endpoint: Endpoint,
    hooks: Optional[HookDispatcher] = None,
    feedback: Optional[Feedback] = None,
    data_generation_method: DataGenerationMethod = DataGenerationMethod.default(),
) -> st.SearchStrategy:
    """Create a strategy for a complete test case.

    Path & endpoint are static, the others are JSON schemas.
    """

    to_strategy = {DataGenerationMethod.positive: make_positive_strategy}[data_generation_method]

    @st.composite  # type: ignore
    def generate_case(draw: Callable) -> Any:
        kwargs: Dict[str, Any] = {}
        _serializers = {}
        if endpoint.body_alternatives:
            options = [parameter for parameter in endpoint.body_alternatives if serializers.can_serialize(parameter)]
            if options:
                # TODO. what if there is no serializer?
                body = draw(st.sampled_from(options))
                body_schema = body.as_json_schema()
                kwargs["body"] = draw(to_strategy(body_schema))
                _serializers["body"] = cast(Callable[[Any], Dict[str, Any]], serializers.get(body))
        elif endpoint.body:
            body_schema = parameters_to_json_schema(endpoint.body)
            kwargs["body"] = draw(to_strategy(body_schema))
            serializer = serializers.get(endpoint.body[0])
            if serializer:  # TODO. what if there is no serializer?
                _serializers["body"] = serializer
        if endpoint.path_parameters:
            serialize = endpoint.get_hypothesis_conversions("path")
            schema = parameters_to_json_schema(endpoint.path_parameters)
            strategy = to_strategy(schema)
            if serialize is not None:
                strategy = strategy.map(serialize)
            strategy = strategy.filter(filter_path_parameters).map(quote_all)
            kwargs["path_parameters"] = draw(strategy)
        for name in ("path_parameters", "headers", "cookies", "query"):
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
                kwargs[name] = draw(strategy)
        return Case(endpoint=endpoint, serializers=_serializers, **kwargs)

    return generate_case()


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


def parameters_to_json_schema(parameters: List[OpenAPIParameter]) -> Dict[str, Any]:
    """Create an "object" JSON schema from a list of Open API parameters.

    :param List[OpenAPIParameter] parameters: A list of Open API parameters, related to the same location. All of
        them are expected to have the same "in" value.

    For each input parameter there will be a property in the output schema.

    This:

        [
            {
                "in": "query",
                "name": "id",
                "type": "string",
                "required": True
            }
        ]

    Will become:

        {
            "properties": {
                "id": {"type": "string"}
            },
            "additionalProperties": False,
            "type": "object",
            "required": ["id"]
        }

    We need this transformation for locations that imply multiple components with unique name within the same location.
    For example, "query" - first, we generate an object, that contains all defined parameters and then serialize it
    to the proper format.
    """
    properties = {}
    required = []
    for parameter in parameters:
        name = parameter.name
        properties[name] = parameter.as_json_schema()
        if parameter.is_required:
            required.append(name)
    return {"properties": properties, "additionalProperties": False, "type": "object", "required": required}


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


def _get_case_strategy(
    endpoint: Endpoint,
    extra_static_parameters: Dict[str, Any],
    strategies: Dict[str, st.SearchStrategy],
    hook_dispatcher: Optional[HookDispatcher] = None,
) -> st.SearchStrategy[Case]:
    static_parameters: Dict[str, Any] = {"endpoint": endpoint, **extra_static_parameters}
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
