import re
import string
from base64 import b64encode
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Union
from urllib.parse import quote_plus
from weakref import WeakKeyDictionary

from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema
from requests.auth import _basic_auth_str
from requests.structures import CaseInsensitiveDict

from ... import auths, serializers, utils
from ...constants import DataGenerationMethod
from ...exceptions import OperationSchemaError, SerializationNotPossible
from ...hooks import HookContext, HookDispatcher, apply_to_all_dispatchers
from ...models import APIOperation, Case, cant_serialize
from ...types import NotSet
from ...utils import NOT_SET, compose, fast_deepcopy, skip
from .constants import LOCATION_TO_CONTAINER
from .negative import negative_schema
from .negative.utils import can_negate
from .parameters import OpenAPIBody, parameters_to_json_schema
from .utils import is_header_location

HEADER_FORMAT = "_header_value"
PARAMETERS = frozenset(("path_parameters", "headers", "cookies", "query", "body"))
SLASH = "/"
STRING_FORMATS: Dict[str, st.SearchStrategy] = {}
StrategyFactory = Callable[[Dict[str, Any], str, str, Optional[str]], st.SearchStrategy]


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


def unregister_string_format(name: str) -> None:
    """Remove format strategy from the registry."""
    try:
        del STRING_FORMATS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown Open API format: {name}") from exc


def init_default_strategies() -> None:
    """Register all default "format" strategies."""
    register_string_format("binary", st.binary())
    register_string_format("byte", st.binary().map(lambda x: b64encode(x).decode()))

    def make_basic_auth_str(item: Tuple[str, str]) -> str:
        return _basic_auth_str(*item)

    latin1_text = st.text(alphabet=st.characters(min_codepoint=0, max_codepoint=255))

    # RFC 7230, Section 3.2.6
    register_string_format(
        "_header_name",
        st.text(min_size=1, alphabet=st.sampled_from("!#$%&'*+-.^_`|~" + string.digits + string.ascii_letters)),
    )
    # Define valid characters here to avoid filtering them out in `is_valid_header` later
    header_value = st.text(alphabet=st.characters(min_codepoint=0, max_codepoint=255, blacklist_characters="\n\r"))
    # Header values with leading non-visible chars can't be sent with `requests`
    register_string_format(HEADER_FORMAT, header_value.map(str.lstrip))
    register_string_format("_basic_auth", st.tuples(latin1_text, latin1_text).map(make_basic_auth_str))  # type: ignore
    register_string_format(
        "_bearer_auth",
        header_value.map("Bearer {}".format),
    )


def is_valid_header(headers: Dict[str, Any]) -> bool:
    """Verify if the generated headers are valid."""
    for name, value in headers.items():
        if not utils.is_latin_1_encodable(value):
            return False
        if utils.has_invalid_characters(name, value):
            return False
    return True


SURROGATE_PAIR_RE = re.compile(r"[\ud800-\udfff]")
has_surrogate_pair = SURROGATE_PAIR_RE.search


def is_illegal_surrogate(item: Any) -> bool:
    if isinstance(item, list):
        return any(isinstance(item_, str) and bool(has_surrogate_pair(item_)) for item_ in item)
    return isinstance(item, str) and bool(has_surrogate_pair(item))


def is_valid_query(query: Dict[str, Any]) -> bool:
    """Surrogates are not allowed in a query string.

    `requests` and `werkzeug` will fail to send it to the application.
    """
    for name, value in query.items():
        if is_illegal_surrogate(name) or is_illegal_surrogate(value):
            return False
    return True


@st.composite  # type: ignore
def get_case_strategy(
    draw: Callable,
    operation: APIOperation,
    hooks: Optional[HookDispatcher] = None,
    auth_storage: Optional[auths.AuthStorage] = None,
    generator: DataGenerationMethod = DataGenerationMethod.default(),
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
    strategy_factory = DATA_GENERATION_METHOD_TO_STRATEGY_FACTORY[generator]

    context = HookContext(operation)

    path_parameters_ = generate_parameter("path", path_parameters, operation, draw, context, hooks, generator)
    headers_ = generate_parameter("header", headers, operation, draw, context, hooks, generator)
    cookies_ = generate_parameter("cookie", cookies, operation, draw, context, hooks, generator)
    query_ = generate_parameter("query", query, operation, draw, context, hooks, generator)

    media_type = None
    if body is NOT_SET:
        if operation.body:
            body_generator = generator
            if generator.is_negative:
                # Consider only schemas that are possible to negate
                candidates = [item for item in operation.body.items if can_negate(item.as_json_schema(operation))]
                # Not possible to negate body, fallback to positive data generation
                if not candidates:
                    candidates = operation.body.items
                    strategy_factory = make_positive_strategy
                    body_generator = DataGenerationMethod.positive
            else:
                candidates = operation.body.items
            parameter = draw(st.sampled_from(candidates))
            strategy = _get_body_strategy(parameter, strategy_factory, operation)
            strategy = apply_hooks(operation, context, hooks, strategy, "body")
            # Parameter may have a wildcard media type. In this case, choose any supported one
            possible_media_types = sorted(serializers.get_matching_media_types(parameter.media_type))
            if not possible_media_types:
                all_media_types = operation.get_request_payload_content_types()
                if all(serializers.get_first_matching_media_type(media_type) is None for media_type in all_media_types):
                    # None of media types defined for this operation are not supported
                    raise SerializationNotPossible.from_media_types(*all_media_types)
                # Other media types are possible - avoid choosing this media type in the future
                cant_serialize(parameter.media_type)
            media_type = draw(st.sampled_from(possible_media_types))
            body_ = ValueContainer(value=draw(strategy), location="body", generator=body_generator)
        else:
            body_ = ValueContainer(value=body, location="body", generator=None)
    else:
        media_types = operation.get_request_payload_content_types() or ["application/json"]
        # Take the first available media type.
        # POSSIBLE IMPROVEMENT:
        #   - Test examples for each available media type on Open API 2.0;
        #   - On Open API 3.0, media types are explicit, and each example has it.
        #     We can pass `OpenAPIBody.media_type` here from the examples handling code.
        media_type = media_types[0]
        body_ = ValueContainer(value=body, location="body", generator=None)

    if operation.schema.validate_schema and operation.method.upper() == "GET" and operation.body:
        raise OperationSchemaError("Body parameters are defined for GET request.")
    # If we need to generate negative cases but no generated values were negated, then skip the whole test
    if generator.is_negative and not any_negated_values([query_, cookies_, headers_, path_parameters_, body_]):
        skip(operation.verbose_name)
    instance = Case(
        operation=operation,
        media_type=media_type,
        path_parameters=path_parameters_.value,
        headers=CaseInsensitiveDict(headers_.value) if headers_.value is not None else headers_.value,
        cookies=cookies_.value,
        query=query_.value,
        body=body_.value,
        data_generation_method=generator,
    )
    auth_context = auths.AuthContext(
        operation=operation,
        app=operation.app,
    )
    auths.set_on_case(instance, auth_context, auth_storage)
    return instance


_BODY_STRATEGIES_CACHE: WeakKeyDictionary = WeakKeyDictionary()


def _get_body_strategy(
    parameter: OpenAPIBody,
    strategy_factory: StrategyFactory,
    operation: APIOperation,
) -> st.SearchStrategy:
    # The cache key relies on object ids, which means that the parameter should not be mutated
    # Note, the parent schema is not included as each parameter belong only to one schema
    if parameter in _BODY_STRATEGIES_CACHE and strategy_factory in _BODY_STRATEGIES_CACHE[parameter]:
        return _BODY_STRATEGIES_CACHE[parameter][strategy_factory]
    schema = parameter.as_json_schema(operation)
    schema = operation.schema.prepare_schema(schema)
    strategy = strategy_factory(schema, operation.verbose_name, "body", parameter.media_type)
    if not parameter.is_required:
        strategy |= st.just(NOT_SET)
    _BODY_STRATEGIES_CACHE.setdefault(parameter, {})[strategy_factory] = strategy
    return strategy


def get_parameters_value(
    value: Union[NotSet, Dict[str, Any]],
    location: str,
    draw: Callable,
    operation: APIOperation,
    context: HookContext,
    hooks: Optional[HookDispatcher],
    strategy_factory: StrategyFactory,
) -> Optional[Dict[str, Any]]:
    """Get the final value for the specified location.

    If the value is not set, then generate it from the relevant strategy. Otherwise, check what is missing in it and
    generate those parts.
    """
    if isinstance(value, NotSet) or not value:
        strategy = get_parameters_strategy(operation, strategy_factory, location)
        strategy = apply_hooks(operation, context, hooks, strategy, location)
        return draw(strategy)
    strategy = get_parameters_strategy(operation, strategy_factory, location, exclude=value.keys())
    strategy = apply_hooks(operation, context, hooks, strategy, location)
    new = draw(strategy)
    if new is not None:
        copied = fast_deepcopy(value)
        copied.update(new)
        return copied
    return value


_PARAMETER_STRATEGIES_CACHE: WeakKeyDictionary = WeakKeyDictionary()


@dataclass
class ValueContainer:
    """Container for a value generated by a data generator or explicitly provided."""

    value: Any
    location: str
    generator: Optional[DataGenerationMethod]

    @property
    def is_generated(self) -> bool:
        """If value was generated."""
        return self.generator is not None and (self.location == "body" or self.value is not None)


def any_negated_values(values: List[ValueContainer]) -> bool:
    """Check if any generated values are negated."""
    return any(value.generator == DataGenerationMethod.negative for value in values if value.is_generated)


def generate_parameter(
    location: str,
    explicit: Union[NotSet, Dict[str, Any]],
    operation: APIOperation,
    draw: Callable,
    context: HookContext,
    hooks: Optional[HookDispatcher],
    generator: DataGenerationMethod,
) -> ValueContainer:
    """Generate a value for a parameter.

    Fallback to positive data generator if parameter can not be negated.
    """
    if generator.is_negative and (
        (location == "path" and not can_negate_path_parameters(operation))
        or (is_header_location(location) and not can_negate_headers(operation, location))
    ):
        # If we can't negate any parameter, generate positive ones
        # If nothing else will be negated, then skip the test completely
        strategy_factory = make_positive_strategy
        generator = DataGenerationMethod.positive
    else:
        strategy_factory = DATA_GENERATION_METHOD_TO_STRATEGY_FACTORY[generator]
    value = get_parameters_value(explicit, location, draw, operation, context, hooks, strategy_factory)
    used_generator: Optional[DataGenerationMethod] = generator
    if value == explicit:
        # When we pass `explicit`, then its parts are excluded from generation of the final value
        # If the final value is the same, then other parameters were generated at all
        used_generator = None
    return ValueContainer(value=value, location=location, generator=used_generator)


def can_negate_path_parameters(operation: APIOperation) -> bool:
    """Check if any path parameter can be negated."""
    schema = parameters_to_json_schema(operation, operation.path_parameters)
    # No path parameters to negate
    parameters = schema["properties"]
    if not parameters:
        return True
    return any(can_negate(parameter) for parameter in parameters.values())


def can_negate_headers(operation: APIOperation, location: str) -> bool:
    """Check if any header can be negated."""
    parameters = getattr(operation, LOCATION_TO_CONTAINER[location])
    schema = parameters_to_json_schema(operation, parameters)
    # No headers to negate
    headers = schema["properties"]
    if not headers:
        return True
    return any(header != {"type": "string"} for header in headers.values())


def get_parameters_strategy(
    operation: APIOperation,
    strategy_factory: StrategyFactory,
    location: str,
    exclude: Iterable[str] = (),
) -> st.SearchStrategy:
    """Create a new strategy for the case's component from the API operation parameters."""
    parameters = getattr(operation, LOCATION_TO_CONTAINER[location])
    if parameters:
        # The cache key relies on object ids, which means that the parameter should not be mutated
        nested_cache_key = (strategy_factory, location, tuple(sorted(exclude)))
        if operation in _PARAMETER_STRATEGIES_CACHE and nested_cache_key in _PARAMETER_STRATEGIES_CACHE[operation]:
            return _PARAMETER_STRATEGIES_CACHE[operation][nested_cache_key]
        schema = parameters_to_json_schema(operation, parameters)
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
        if not schema["properties"] and strategy_factory is make_negative_strategy:
            # Nothing to negate - all properties were excluded
            strategy = st.none()
        else:
            strategy = strategy_factory(schema, operation.verbose_name, location, None)
            serialize = operation.get_parameter_serializer(location)
            if serialize is not None:
                strategy = strategy.map(serialize)
            filter_func = {
                "path": is_valid_path,
                "header": is_valid_header,
                "cookie": is_valid_header,
                "query": is_valid_query,
            }[location]
            # Headers with special format do not need filtration
            if not (is_header_location(location) and _can_skip_header_filter(schema)):
                strategy = strategy.filter(filter_func)
            # Path & query parameters will be cast to string anyway, but having their JSON equivalents for
            # `True` / `False` / `None` improves chances of them passing validation in apps
            # that expect boolean / null types
            # and not aware of Python-specific representation of those types
            map_func = {
                "path": compose(quote_all, jsonify_python_specific_types),
                "query": jsonify_python_specific_types,
            }.get(location)
            if map_func:
                strategy = strategy.map(map_func)  # type: ignore
        _PARAMETER_STRATEGIES_CACHE.setdefault(operation, {})[nested_cache_key] = strategy
        return strategy
    # No parameters defined for this location
    return st.none()


def jsonify_python_specific_types(value: Dict[str, Any]) -> Dict[str, Any]:
    """Convert Python-specific values to their JSON equivalents."""
    stack: list = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, sub_item in item.items():
                if isinstance(sub_item, bool):
                    item[key] = "true" if sub_item else "false"
                elif sub_item is None:
                    item[key] = "null"
                elif isinstance(sub_item, dict):
                    stack.append(sub_item)
                elif isinstance(sub_item, list):
                    stack.extend(item)
        elif isinstance(item, list):
            stack.extend(item)
    return value


def make_positive_strategy(
    schema: Dict[str, Any],
    operation_name: str,
    location: str,
    media_type: Optional[str],
    custom_formats: Optional[Dict[str, st.SearchStrategy]] = None,
) -> st.SearchStrategy:
    """Strategy for generating values that fit the schema."""
    if is_header_location(location):
        # We try to enforce the right header values via "format"
        # This way, only allowed values will be used during data generation, which reduces the amount of filtering later
        # If a property schema contains `pattern` it leads to heavy filtering and worse performance - therefore, skip it
        for sub_schema in schema.get("properties", {}).values():
            if list(sub_schema) == ["type"] and sub_schema["type"] == "string":
                sub_schema.setdefault("format", HEADER_FORMAT)
    return from_schema(schema, custom_formats={**STRING_FORMATS, **(custom_formats or {})})


def _can_skip_header_filter(schema: Dict[str, Any]) -> bool:
    # All headers should contain HEADER_FORMAT in order to avoid header filter
    return all(sub_schema.get("format") == HEADER_FORMAT for sub_schema in schema.get("properties", {}).values())


def make_negative_strategy(
    schema: Dict[str, Any],
    operation_name: str,
    location: str,
    media_type: Optional[str],
    custom_formats: Optional[Dict[str, st.SearchStrategy]] = None,
) -> st.SearchStrategy:
    return negative_schema(
        schema,
        operation_name=operation_name,
        location=location,
        media_type=media_type,
        custom_formats={**STRING_FORMATS, **(custom_formats or {})},
    )


DATA_GENERATION_METHOD_TO_STRATEGY_FACTORY = {
    DataGenerationMethod.positive: make_positive_strategy,
    DataGenerationMethod.negative: make_negative_strategy,
}


def is_valid_path(parameters: Dict[str, Any]) -> bool:
    """Empty strings ("") are excluded from path by urllib3.

    A path containing to "/" or "%2F" will lead to ambiguous path resolution in
    many frameworks and libraries, such behaviour have been observed in both
    WSGI and ASGI applications.

    In this case one variable in the path template will be empty, which will lead to 404 in most of the cases.
    Because of it this case doesn't bring much value and might lead to false positives results of Schemathesis runs.
    """
    disallowed_values = (SLASH, "")

    return not any(
        (value in disallowed_values or is_illegal_surrogate(value) or isinstance(value, str) and SLASH in value)
        for value in parameters.values()
    )


def quote_all(parameters: Dict[str, Any]) -> Dict[str, Any]:
    """Apply URL quotation for all values in a dictionary."""
    # Even though, "." is an unreserved character, it has a special meaning in "." and ".." strings.
    # It will change the path:
    #   - http://localhost/foo/./ -> http://localhost/foo/
    #   - http://localhost/foo/../ -> http://localhost/
    # Which is not desired as we need to test precisely the original path structure.

    for key, value in parameters.items():
        if isinstance(value, str):
            if value == ".":
                parameters[key] = "%2E"
            elif value == "..":
                parameters[key] = "%2E%2E"
            else:
                parameters[key] = quote_plus(value)
    return parameters


def apply_hooks(
    operation: APIOperation,
    context: HookContext,
    hooks: Optional[HookDispatcher],
    strategy: st.SearchStrategy,
    location: str,
) -> st.SearchStrategy:
    """Apply all hooks related to the given location."""
    container = LOCATION_TO_CONTAINER[location]
    return apply_to_all_dispatchers(operation, context, hooks, strategy, container)


def clear_cache() -> None:
    _PARAMETER_STRATEGIES_CACHE.clear()
    _BODY_STRATEGIES_CACHE.clear()
