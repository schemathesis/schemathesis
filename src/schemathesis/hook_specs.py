"""Hook specifications — the names, signatures, and docs of every supported hook."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from schemathesis.hooks import HookContext, HookDispatcher, HookScope

if TYPE_CHECKING:
    import requests
    from hypothesis.strategies import SearchStrategy

    from schemathesis.checks import CheckResult
    from schemathesis.core import Body
    from schemathesis.core.jsonschema.types import JsonSchemaObject
    from schemathesis.core.transport import Response
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation, BaseSchema


all_scopes = HookDispatcher.register_spec(list(HookScope))


@all_scopes
def filter_path_parameters(context: HookContext, path_parameters: dict[str, Any]) -> bool:
    """Drop generated `path_parameters` values that fail the predicate."""
    raise NotImplementedError


@all_scopes
def filter_query(context: HookContext, query: dict[str, Any]) -> bool:
    """Drop generated `query` values that fail the predicate."""
    raise NotImplementedError


@all_scopes
def filter_headers(context: HookContext, headers: dict[str, Any]) -> bool:
    """Drop generated `headers` values that fail the predicate."""
    raise NotImplementedError


@all_scopes
def filter_cookies(context: HookContext, cookies: dict[str, Any]) -> bool:
    """Drop generated `cookies` values that fail the predicate."""
    raise NotImplementedError


@all_scopes
def filter_body(context: HookContext, body: Body) -> bool:
    """Drop generated `body` values that fail the predicate."""
    raise NotImplementedError


@all_scopes
def filter_case(context: HookContext, case: Case) -> bool:
    """Drop generated `Case` instances that fail the predicate."""
    raise NotImplementedError


@all_scopes
def map_path_parameters(context: HookContext, path_parameters: dict[str, Any]) -> dict[str, Any]:
    """Transform each generated `path_parameters` value."""
    raise NotImplementedError


@all_scopes
def map_query(context: HookContext, query: dict[str, Any]) -> dict[str, Any]:
    """Transform each generated `query` value."""
    raise NotImplementedError


@all_scopes
def map_headers(context: HookContext, headers: dict[str, Any]) -> dict[str, Any]:
    """Transform each generated `headers` value."""
    raise NotImplementedError


@all_scopes
def map_cookies(context: HookContext, cookies: dict[str, Any]) -> dict[str, Any]:
    """Transform each generated `cookies` value."""
    raise NotImplementedError


@all_scopes
def map_body(context: HookContext, body: Body) -> Body:
    """Transform each generated `body` value."""
    raise NotImplementedError


@all_scopes
def map_case(context: HookContext, case: Case) -> Case:
    """Transform each generated `Case` instance."""
    raise NotImplementedError


@all_scopes
def flatmap_path_parameters(context: HookContext, path_parameters: dict[str, Any]) -> SearchStrategy:
    """Replace the strategy for `path_parameters` with another strategy derived from each value."""
    raise NotImplementedError


@all_scopes
def flatmap_query(context: HookContext, query: dict[str, Any]) -> SearchStrategy:
    """Replace the strategy for `query` with another strategy derived from each value."""
    raise NotImplementedError


@all_scopes
def flatmap_headers(context: HookContext, headers: dict[str, Any]) -> SearchStrategy:
    """Replace the strategy for `headers` with another strategy derived from each value."""
    raise NotImplementedError


@all_scopes
def flatmap_cookies(context: HookContext, cookies: dict[str, Any]) -> SearchStrategy:
    """Replace the strategy for `cookies` with another strategy derived from each value."""
    raise NotImplementedError


@all_scopes
def flatmap_body(context: HookContext, body: Body) -> SearchStrategy:
    """Replace the strategy for `body` with another strategy derived from each value."""
    raise NotImplementedError


@all_scopes
def flatmap_case(context: HookContext, case: Case) -> SearchStrategy:
    """Replace the strategy for `Case` with another strategy derived from each instance."""
    raise NotImplementedError


@all_scopes
def before_generate_path_parameters(context: HookContext, strategy: SearchStrategy) -> SearchStrategy:
    """Called on a strategy that generates values for ``path_parameters``."""


@all_scopes
def before_generate_headers(context: HookContext, strategy: SearchStrategy) -> SearchStrategy:
    """Called on a strategy that generates values for ``headers``."""


@all_scopes
def before_generate_cookies(context: HookContext, strategy: SearchStrategy) -> SearchStrategy:
    """Called on a strategy that generates values for ``cookies``."""


@all_scopes
def before_generate_query(context: HookContext, strategy: SearchStrategy) -> SearchStrategy:
    """Called on a strategy that generates values for ``query``."""


@all_scopes
def before_generate_body(context: HookContext, strategy: SearchStrategy) -> SearchStrategy:
    """Called on a strategy that generates values for ``body``."""


@all_scopes
def before_generate_case(context: HookContext, strategy: SearchStrategy[Case]) -> SearchStrategy[Case]:
    """Called on a strategy that generates ``Case`` instances."""


@all_scopes
def before_process_path(context: HookContext, path: str, methods: dict[str, Any]) -> None:
    """Called before API path is processed."""


@HookDispatcher.register_spec([HookScope.GLOBAL])
def before_load_schema(context: HookContext, raw_schema: JsonSchemaObject) -> None:
    """Called before schema instance is created."""


@HookDispatcher.register_spec([HookScope.GLOBAL])
def after_load_schema(context: HookContext, schema: BaseSchema) -> None:
    """Called after schema instance is created."""


@all_scopes
def before_add_examples(context: HookContext, examples: list[Case]) -> None:
    """Called before explicit examples are added to a test via `@example` decorator.

    `examples` is a list that could be extended with examples provided by the user.
    """


@all_scopes
def before_init_operation(context: HookContext, operation: APIOperation) -> None:
    """Allows you to customize a newly created API operation."""


@HookDispatcher.register_spec([HookScope.GLOBAL])
def before_call(context: HookContext, case: Case, kwargs: dict[str, Any]) -> None:
    """Called before every network call in CLI tests.

    Use cases:
     - Modification of `case`. For example, adding some pre-determined value to its query string.
     - Logging
    """


@HookDispatcher.register_spec([HookScope.GLOBAL, HookScope.SCHEMA])
def after_call(context: HookContext, case: Case, response: Response) -> None:
    """Called after every network call in CLI tests.

    Note that you need to modify the response in-place.

    Use cases:
     - Response post-processing, like modifying its payload.
     - Logging
    """


@HookDispatcher.register_spec([HookScope.GLOBAL, HookScope.SCHEMA])
def after_network_error(context: HookContext, case: Case, request: requests.PreparedRequest) -> None:
    """Called when a network-level error (timeout, connection failure) occurs during case.call().

    The prepared request that was attempted is available for logging or recording.

    Use cases:
     - Recording failed network interactions alongside successful ones.
     - Logging connection failures.
    """


@all_scopes
def after_validate(context: HookContext, case: Case, response: Response, results: list[CheckResult]) -> None:
    """Called after all validation checks run on a response.

    `results` contains one entry per check that was executed — `status` is
    `Status.SUCCESS` when the check passed, `Status.FAILURE` with `failure`
    populated when it did not.

    Use cases:
     - Recording check outcomes for cassette/report writers.
     - Custom observability / logging of validation results.
    """
