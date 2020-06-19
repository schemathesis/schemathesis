"""Provide strategies for given endpoint(s) definition."""
import asyncio
import inspect
import re
from base64 import b64encode
from functools import partial
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from urllib.parse import quote_plus

import hypothesis
import hypothesis.strategies as st
from hypothesis_jsonschema import from_schema
from requests.auth import _basic_auth_str

from . import utils
from .exceptions import InvalidSchema
from .hooks import GLOBAL_HOOK_DISPATCHER, HookContext, HookDispatcher
from .models import Case, Endpoint
from .types import Hook

PARAMETERS = frozenset(("path_parameters", "headers", "cookies", "query", "body", "form_data"))
LOCATION_TO_CONTAINER = {
    "path": "path_parameters",
    "query": "query",
    "header": "headers",
    "cookie": "cookies",
    "body": "body",
    "formData": "form_data",
}
SLASH = "/"


def create_test(
    endpoint: Endpoint, test: Callable, settings: Optional[hypothesis.settings] = None, seed: Optional[int] = None
) -> Callable:
    """Create a Hypothesis test."""
    hook_dispatcher = getattr(test, "_schemathesis_hooks", None)
    strategy = endpoint.as_strategy(hooks=hook_dispatcher)
    wrapped_test = hypothesis.given(case=strategy)(test)
    if seed is not None:
        wrapped_test = hypothesis.seed(seed)(wrapped_test)
    if asyncio.iscoroutinefunction(test):
        wrapped_test.hypothesis.inner_test = make_async_test(test)  # type: ignore
    if settings is not None:
        wrapped_test = settings(wrapped_test)
    return add_examples(wrapped_test, endpoint, hook_dispatcher=hook_dispatcher)


def make_test_or_exception(
    endpoint: Endpoint, func: Callable, settings: Optional[hypothesis.settings] = None, seed: Optional[int] = None
) -> Union[Callable, InvalidSchema]:
    try:
        return create_test(endpoint, func, settings, seed=seed)
    except InvalidSchema as exc:
        return exc


def make_async_test(test: Callable) -> Callable:
    def async_run(*args: Any, **kwargs: Any) -> None:
        loop = asyncio.get_event_loop()
        coro = test(*args, **kwargs)
        future = asyncio.ensure_future(coro, loop=loop)
        loop.run_until_complete(future)

    return async_run


def add_examples(test: Callable, endpoint: Endpoint, hook_dispatcher: Optional[HookDispatcher] = None) -> Callable:
    """Add examples to the Hypothesis test, if they are specified in the schema."""
    examples: List[Case] = [get_single_example(strategy) for strategy in endpoint.get_strategies_from_examples()]
    context = HookContext(endpoint)  # context should be passed here instead
    GLOBAL_HOOK_DISPATCHER.dispatch("before_add_examples", context, examples)
    endpoint.schema.hooks.dispatch("before_add_examples", context, examples)
    if hook_dispatcher:
        hook_dispatcher.dispatch("before_add_examples", context, examples)
    for example in examples:
        test = hypothesis.example(case=example)(test)
    return test


def get_single_example(strategy: st.SearchStrategy[Case]) -> Case:
    @hypothesis.given(strategy)  # type: ignore
    @hypothesis.settings(  # type: ignore
        database=None,
        max_examples=1,
        deadline=None,
        verbosity=hypothesis.Verbosity.quiet,
        phases=(hypothesis.Phase.generate,),
        suppress_health_check=hypothesis.HealthCheck.all(),
    )
    def example_generating_inner_function(ex: Case) -> None:
        examples.append(ex)

    examples: List[Case] = []
    example_generating_inner_function()
    return examples[0]


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


def get_case_strategy(endpoint: Endpoint, hooks: Optional[HookDispatcher] = None) -> st.SearchStrategy:
    """Create a strategy for a complete test case.

    Path & endpoint are static, the others are JSON schemas.
    """
    strategies = {}
    static_kwargs: Dict[str, Any] = {"endpoint": endpoint}
    for parameter in PARAMETERS:
        value = getattr(endpoint, parameter)
        if value is not None:
            location = {"headers": "header", "cookies": "cookie", "path_parameters": "path"}.get(parameter, parameter)
            strategies[parameter] = prepare_strategy(parameter, value, endpoint.get_hypothesis_conversions(location))
        else:
            static_kwargs[parameter] = None
    return _get_case_strategy(endpoint, static_kwargs, strategies, hooks)


def prepare_strategy(parameter: str, value: Dict[str, Any], map_func: Optional[Callable]) -> st.SearchStrategy:
    strategy = from_schema(value)
    if map_func is not None:
        strategy = strategy.map(map_func)
    if parameter == "path_parameters":
        strategy = strategy.filter(filter_path_parameters).map(quote_all)  # type: ignore
    elif parameter in ("headers", "cookies"):
        strategy = strategy.filter(is_valid_header)  # type: ignore
    elif parameter == "query":
        strategy = strategy.filter(is_valid_query)  # type: ignore
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
        (value in path_parameter_blacklist or isinstance(value, str) and SLASH in value)
        for value in parameters.values()
    )


def quote_all(parameters: Dict[str, Any]) -> Dict[str, Any]:
    return {key: quote_plus(value) if isinstance(value, str) else value for key, value in parameters.items()}


def _get_case_strategy(
    endpoint: Endpoint,
    extra_static_parameters: Dict[str, Any],
    strategies: Dict[str, st.SearchStrategy],
    hook_dispatcher: Optional[HookDispatcher] = None,
) -> st.SearchStrategy:
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
            args: Union[Tuple[st.SearchStrategy], Tuple[HookContext, st.SearchStrategy]]
            if _accepts_context(hook):
                args = (context, strategy)
            else:
                args = (strategy,)
            strategies[key] = hook(*args)


def _accepts_context(hook: Hook) -> bool:
    # There are no restrictions on the first argument's name and we don't check its name here.
    return len(inspect.signature(hook).parameters) == 2


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

    def make_basic_auth_str(item: Tuple[str, str]) -> str:
        return _basic_auth_str(*item)

    register_string_format("_basic_auth", st.tuples(st.text(), st.text()).map(make_basic_auth_str))  # type: ignore
    register_string_format("_bearer_auth", st.text().map("Bearer {}".format))
