from __future__ import annotations

import json
from functools import lru_cache
from os import PathLike
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Callable, Dict, NoReturn, TypeVar, cast

from schemathesis.core.errors import LoaderError, LoaderErrorKind
from schemathesis.core.loaders import load_from_url, prepare_request_kwargs, raise_for_status, require_relative_url
from schemathesis.hooks import HookContext, dispatch
from schemathesis.python import asgi, wsgi

if TYPE_CHECKING:
    from graphql import DocumentNode

    from schemathesis.specs.graphql.schemas import GraphQLSchema


def from_asgi(path: str, app: Any, **kwargs: Any) -> GraphQLSchema:
    require_relative_url(path)
    kwargs.setdefault("json", {"query": get_introspection_query()})
    client = asgi.get_client(app)
    response = load_from_url(client.post, url=path, **kwargs)
    schema = extract_schema_from_response(response, lambda r: r.json())
    return from_dict(schema=schema).configure(app=app, location=path)


def from_wsgi(path: str, app: Any, **kwargs: Any) -> GraphQLSchema:
    require_relative_url(path)
    prepare_request_kwargs(kwargs)
    kwargs.setdefault("json", {"query": get_introspection_query()})
    client = wsgi.get_client(app)
    response = client.post(path=path, **kwargs)
    raise_for_status(response)
    schema = extract_schema_from_response(response, lambda r: r.json)
    return from_dict(schema=schema).configure(app=app, location=path)


def from_url(url: str, *, wait_for_schema: float | None = None, **kwargs: Any) -> GraphQLSchema:
    """Load from URL."""
    import requests

    kwargs.setdefault("json", {"query": get_introspection_query()})
    response = load_from_url(requests.post, url=url, wait_for_schema=wait_for_schema, **kwargs)
    schema = extract_schema_from_response(response, lambda r: r.json())
    return from_dict(schema).configure(location=url)


def from_path(path: PathLike | str, *, encoding: str = "utf-8") -> GraphQLSchema:
    """Load from a filesystem path."""
    with open(path, encoding=encoding) as file:
        return from_file(file=file).configure(location=Path(path).absolute().as_uri())


def from_file(file: IO[str] | str) -> GraphQLSchema:
    """Load from file-like object or string."""
    import graphql

    if isinstance(file, str):
        data = file
    else:
        data = file.read()
    try:
        document = graphql.build_schema(data)
        result = graphql.execute(document, get_introspection_query_ast())
        # TYPES: We don't pass `is_awaitable` above, therefore `result` is of the `ExecutionResult` type
        result = cast(graphql.ExecutionResult, result)
        # TYPES:
        #  - `document` is a valid schema, because otherwise `build_schema` will rise an error;
        #  - `INTROSPECTION_QUERY` is a valid query - it is known upfront;
        # Therefore the execution result is always valid at this point and `result.data` is not `None`
        schema = cast(Dict[str, Any], result.data)
    except Exception as exc:
        try:
            schema = json.loads(data)
            if not isinstance(schema, dict) or "__schema" not in schema:
                _on_invalid_schema(exc)
        except json.JSONDecodeError:
            _on_invalid_schema(exc, extras=[entry for entry in str(exc).splitlines() if entry])
    return from_dict(schema)


def from_dict(schema: dict[str, Any]) -> GraphQLSchema:
    """Base loader that others build upon."""
    from schemathesis.specs.graphql.schemas import GraphQLSchema

    if "data" in schema:
        schema = schema["data"]
    hook_context = HookContext()
    dispatch("before_load_schema", hook_context, schema)
    instance = GraphQLSchema(schema)
    dispatch("after_load_schema", hook_context, instance)
    return instance


@lru_cache
def get_introspection_query() -> str:
    import graphql

    return graphql.get_introspection_query()


@lru_cache
def get_introspection_query_ast() -> DocumentNode:
    import graphql

    query = get_introspection_query()
    return graphql.parse(query)


R = TypeVar("R")


def extract_schema_from_response(response: R, callback: Callable[[R], Any]) -> dict[str, Any]:
    try:
        decoded = callback(response)
    except json.JSONDecodeError as exc:
        raise LoaderError(
            LoaderErrorKind.UNEXPECTED_CONTENT_TYPE,
            "Received unsupported content while expecting a JSON payload for GraphQL",
        ) from exc
    return decoded


def _on_invalid_schema(exc: Exception, extras: list[str] | None = None) -> NoReturn:
    raise LoaderError(
        LoaderErrorKind.GRAPHQL_INVALID_SCHEMA,
        "The provided API schema does not appear to be a valid GraphQL schema",
        extras=extras or [],
    ) from exc
