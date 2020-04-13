"""Provide strategies for given endpoint(s) definition."""
import asyncio
import re
from base64 import b64encode
from functools import partial
from typing import Any, Callable, Dict, List, Optional, Union
from urllib.parse import quote_plus

import hypothesis
import hypothesis.strategies as st
from hypothesis_jsonschema import from_schema

from . import utils
from ._compat import handle_warnings
from .exceptions import InvalidSchema
from .hooks import get_hook
from .models import Case, Endpoint, Requirement
from .types import Hook

PARAMETERS = frozenset(
    ("path_parameters", "headers", "cookies", "query", "body", "form_data", "modified_path_parameters", "modified_body")
)
SCHEMA_DATATYPE = {"string": (str,), "number": (int, float), "object": (dict,), "array": (list,), "boolean": (bool,)}
SLASH = "/"


def create_test(
    endpoint: Endpoint,
    test: Callable,
    settings: Optional[hypothesis.settings] = None,
    seed: Optional[int] = None,
    stateful: bool = False,
) -> Callable:
    """Create a Hypothesis test."""
    hooks = getattr(test, "_schemathesis_hooks", None)
    strategy = (  # pylint: disable=consider-using-ternary
        stateful and _get_example_from_dependency(endpoint, True) or endpoint.as_strategy(hooks=hooks)
    )
    wrapped_test = hypothesis.given(case=strategy)(test)
    if seed is not None:
        wrapped_test = hypothesis.seed(seed)(wrapped_test)
    original_test = get_original_test(test)
    if asyncio.iscoroutinefunction(original_test):
        wrapped_test.hypothesis.inner_test = make_async_test(original_test)  # type: ignore
    if settings is not None:
        wrapped_test = settings(wrapped_test)
    if stateful:
        return wrapped_test
    wrapped_test = add_examples(wrapped_test, endpoint, get_example)
    return add_examples(wrapped_test, endpoint, get_example_from_dependency)


def make_test_or_exception(
    endpoint: Endpoint,
    func: Callable,
    settings: Optional[hypothesis.settings] = None,
    seed: Optional[int] = None,
    stateful: bool = False,
) -> Union[Callable, InvalidSchema]:
    try:
        return create_test(endpoint, func, settings, seed=seed, stateful=stateful)
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


def get_example(endpoint: Endpoint, modify_schema: bool = False) -> Optional[Case]:
    with handle_warnings():
        example = _get_example(endpoint, modify_schema)
        return example.example() if example else None


def get_example_from_dependency(endpoint: Endpoint, modify_schema: bool = False) -> Optional[Case]:
    with handle_warnings():
        example = _get_example_from_dependency(endpoint, modify_schema)
        return example.example() if example else None


def strategy_with_example(
    endpoint: Endpoint, parameters: Dict[str, Any], modify_schema: bool = False
) -> Optional[st.SearchStrategy]:
    strategies = {
        other: from_schema(getattr(endpoint, other))
        for other in PARAMETERS - set(parameters)
        if getattr(endpoint, other) is not None
    }
    if modify_schema:
        if "modified_body" in parameters:
            parameters["body"] = parameters["modified_body"]
        if "modified_path_parameters" in parameters:
            parameters["path_parameters"] = parameters["modified_path_parameters"]
        if "modified_body" in strategies:
            strategies["body"] = strategies["modified_body"]
        if "modified_path_parameters" in strategies:
            strategies["path_parameters"] = strategies["modified_path_parameters"]
    parameters.pop("modified_body", None)
    parameters.pop("modified_path_parameters", None)
    strategies.pop("modified_body", None)
    strategies.pop("modified_path_parameters", None)
    return _get_case_strategy(endpoint, parameters, strategies)


def _get_example(endpoint: Endpoint, modify_schema: bool = False) -> Optional[st.SearchStrategy]:
    static_parameters = {}
    body_example = False
    for name in PARAMETERS:
        parameter = getattr(endpoint, name)
        if parameter is not None and "example" in parameter:
            static_parameters[name] = parameter["example"]
        elif modify_schema and name.startswith("modified") and parameter is not None and "properties" in parameter:
            properties = parameter.get("properties", {})
            for key, val in properties.items():
                if "example" in val and "enum" not in val:
                    properties[key]["enum"] = [properties[key]["example"]]
                    body_example = True
                if "items" in val and "example" in val["items"] and "enum" not in val["items"]:
                    properties[key]["items"]["enum"] = [properties[key]["items"]["example"]]
                    body_example = True

    if static_parameters or body_example:
        return strategy_with_example(endpoint, static_parameters, modify_schema)
    return None


def _get_example_from_dependency(endpoint: Endpoint, modify_schema: bool = False) -> Optional[st.SearchStrategy]:
    static_parameters = {}
    body = {}
    if not endpoint.schema.state.requirements:
        return _get_example(endpoint, modify_schema)
    for param, endpoint_list in endpoint.dependencies.items():
        requirement = endpoint.schema.state.requirements[param]
        if requirement.values:
            continue
        _update_requirements(requirement, endpoint, param, endpoint_list)
    for name in PARAMETERS:
        parameter = getattr(endpoint, name)
        for key, req in endpoint.schema.state.requirements.items():
            if req.values and parameter is not None and key in parameter:
                static_parameters[name] = req.values[-1]
            if (
                modify_schema
                and name.startswith("modified")
                and req.values
                and parameter is not None
                and key in parameter.get("properties", {})
            ):
                # inject example value in request body
                body = parameter["properties"][key].get("items") or parameter["properties"][key]
                if req.is_fuzzable:
                    _inject_fuzzable(body, req.values)
                else:
                    _inject_non_fuzzable(body, req.values)
                if (  # pylint: disable=unidiomatic-typecheck
                    "type" in body
                    and type(req.values[0]) not in SCHEMA_DATATYPE.get(body["type"], [])
                    and "items" in parameter["properties"][key]
                ):
                    # if type of key param is different from dependency
                    # and requested param is of type list (items),
                    # iter the response from dependency and use first value
                    body["enum"] = [next(iter(req.values[0]))]
    if static_parameters or body:
        return strategy_with_example(endpoint, static_parameters, modify_schema)
    return _get_example(endpoint, modify_schema)


def _update_requirements(
    requirement: Requirement, target_endpoint: Endpoint, param: str, endpoint_list: List[Endpoint]
) -> None:
    if not target_endpoint.schema.state.requirements:
        return
    for dependency in endpoint_list:
        if requirement.values:
            break
        example_st = _get_example(dependency)
        random_st = get_case_strategy(dependency)
        with handle_warnings():
            example = (example_st or random_st).example()
        try:
            response = example.call()
        except ValueError:
            # missing base_url
            response = None
        if response and (response.status_code - 200) < 100 and response.json():
            for key, value in utils.json_traverse(response.json()):
                if value and key == param:
                    target_endpoint.schema.state.requirements[key].append(value)
                    break


def _inject_non_fuzzable(body: dict, items: List) -> None:
    if "type" in body or "enum" in body:
        body["enum"] = items
    if "oneOf" in body:
        enum_in_body = any(["enum" in (x.get("items") or x) for x in body["oneOf"]])
        for oneof in body["oneOf"]:
            body_oneof = oneof.get("items") or oneof
            if ("enum" in body_oneof) == enum_in_body:
                body_oneof["enum"] = items
            elif enum_in_body and "enum" not in body_oneof:
                body["oneOf"].remove(oneof)


def _inject_fuzzable(body: dict, items: List) -> None:
    if "type" in body:
        body["oneOf"] = [{"type": body["type"]}, {"enum": "NOT_SET"}]
        del body["type"]
        body["oneOf"][1]["enum"] = items
        if "enum" in body:
            del body["enum"]
    elif "oneOf" in body:
        if any(["enum" in (x.get("items") or x) for x in body["oneOf"]]):
            # update enum
            # make sure it is still fuzzable
            type_present = False  # [{"type": type}, ...]
            needed_type = None
            for oneof in body["oneOf"]:
                body_oneof = oneof.get("items") or oneof
                if "enum" in body_oneof:
                    body_oneof["enum"] = items
                    needed_type = body_oneof.get("enum")
                elif "type" in body_oneof:
                    type_present = True
            if not type_present:
                body["oneOf"].append({"type": needed_type or "string"})
        else:
            for oneof in body["oneOf"]:
                body_oneof = oneof.get("items") or oneof
                if "enum" not in body_oneof:
                    body_oneof["oneOf"] = [{"type": body_oneof["type"]}, {"enum": "NOT_SET"}]
                    del body_oneof["type"]
                    body_oneof = body_oneof["oneOf"][1]
                body_oneof["enum"] = items


def add_examples(test: Callable, endpoint: Endpoint, get_example_func: Callable) -> Callable:
    """Add examples to the Hypothesis test, if they are specified in the schema."""
    example = get_example_func(endpoint)
    if example:
        test = hypothesis.example(case=example)(test)
    return test


def is_valid_header(headers: Dict[str, str]) -> bool:
    """Verify if the generated headers are valid."""
    for name, value in headers.items():
        if not utils.is_latin_1_encodable(value):
            return False
        if utils.has_invalid_characters(name, value):
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


def get_case_strategy(endpoint: Endpoint, hooks: Optional[Dict[str, Hook]] = None) -> st.SearchStrategy:
    """Create a strategy for a complete test case.

    Path & endpoint are static, the others are JSON schemas.
    """
    strategies = {}
    static_kwargs: Dict[str, Any] = {"endpoint": endpoint}
    try:
        for parameter in PARAMETERS:
            if parameter.startswith("modified"):
                continue
            value = getattr(endpoint, parameter)
            if value is not None:
                if parameter == "path_parameters":
                    strategies[parameter] = (
                        from_schema(value).filter(filter_path_parameters).map(quote_all)  # type: ignore
                    )
                elif parameter in ("headers", "cookies"):
                    strategies[parameter] = from_schema(value).filter(is_valid_header)  # type: ignore
                elif parameter == "query":
                    strategies[parameter] = from_schema(value).filter(is_valid_query)  # type: ignore
                else:
                    strategies[parameter] = from_schema(value)  # type: ignore
            else:
                static_kwargs[parameter] = None
        return _get_case_strategy(endpoint, static_kwargs, strategies, hooks)
    except AssertionError:
        raise InvalidSchema("Invalid schema for this endpoint")


def filter_path_parameters(parameters: Dict[str, Any]) -> bool:
    """Single "." chars and empty strings "" are excluded from path by urllib3.

    A path containing to "/" or "%2F" will lead to ambiguous path resolution in
    many frameworks and libraries, such behaviour have been observed in both
    WSGI and ASGI applications.

    In this case one variable in the path template will be empty, which will lead to 404 in most of the cases.
    Because of it this case doesn't bring much value and might lead to false positives results of Schemathesis runs.
    """

    path_parameter_blacklist = (
        ".",
        SLASH,
        "",
    )

    return not any(
        (value in path_parameter_blacklist or isinstance(value, str) and SLASH in value)
        for value in parameters.values()
    )


def quote_all(parameters: Dict[str, Any]) -> Dict[str, Any]:
    return {key: quote_plus(value) if isinstance(value, str) else value for key, value in parameters.items()}


def _get_case_strategy(
    endpoint: Endpoint,
    extra_static_parameters: Dict[str, Any],
    strategies: Dict[str, st.SearchStrategy],
    hooks: Optional[Dict[str, Hook]] = None,
) -> st.SearchStrategy:
    static_parameters: Dict[str, Any] = {"endpoint": endpoint, **extra_static_parameters}
    if endpoint.schema.validate_schema and endpoint.method == "GET":
        if endpoint.body is not None:
            raise InvalidSchema("Body parameters are defined for GET request.")
        static_parameters["body"] = None
        strategies.pop("body", None)
    _apply_hooks(strategies, get_hook)
    _apply_hooks(strategies, endpoint.schema.get_hook)
    if hooks is not None:
        _apply_hooks(strategies, hooks.get)
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
