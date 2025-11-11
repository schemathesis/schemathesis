from __future__ import annotations

import json
from collections.abc import Callable
from functools import lru_cache
from os import PathLike
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, NoReturn, TypeVar, cast

from schemathesis.config import SchemathesisConfig
from schemathesis.core.errors import LoaderError, LoaderErrorKind
from schemathesis.core.loaders import load_from_url, prepare_request_kwargs, raise_for_status, require_relative_url
from schemathesis.hooks import HookContext, dispatch
from schemathesis.python import asgi, wsgi

if TYPE_CHECKING:
    from graphql import DocumentNode

    from schemathesis.specs.graphql.schemas import GraphQLSchema


def from_asgi(path: str, app: Any, *, config: SchemathesisConfig | None = None, **kwargs: Any) -> GraphQLSchema:
    """Load GraphQL schema from an ASGI application via introspection.

    Args:
        path: Relative URL path to the GraphQL endpoint (e.g., "/graphql")
        app: ASGI application instance
        config: Custom configuration. If `None`, uses auto-discovered config
        **kwargs: Additional request parameters passed to the ASGI test client.

    Example:
        ```python
        from fastapi import FastAPI
        import schemathesis

        app = FastAPI()
        schema = schemathesis.graphql.from_asgi("/graphql", app)
        ```

    """
    require_relative_url(path)
    kwargs.setdefault("json", {"query": get_introspection_query()})
    client = asgi.get_client(app)
    response = load_from_url(client.post, url=path, **kwargs)
    schema = extract_schema_from_response(response, lambda r: r.json())
    loaded = from_dict(schema=schema, config=config)
    loaded.app = app
    loaded.location = path
    return loaded


def from_wsgi(path: str, app: Any, *, config: SchemathesisConfig | None = None, **kwargs: Any) -> GraphQLSchema:
    """Load GraphQL schema from a WSGI application via introspection.

    Args:
        path: Relative URL path to the GraphQL endpoint (e.g., "/graphql")
        app: WSGI application instance
        config: Custom configuration. If `None`, uses auto-discovered config
        **kwargs: Additional request parameters passed to the WSGI test client.

    Example:
        ```python
        from flask import Flask
        import schemathesis

        app = Flask(__name__)
        schema = schemathesis.graphql.from_wsgi("/graphql", app)
        ```

    """
    require_relative_url(path)
    prepare_request_kwargs(kwargs)
    kwargs.setdefault("json", {"query": get_introspection_query()})
    client = wsgi.get_client(app)
    response = client.post(path=path, **kwargs)
    raise_for_status(response)
    schema = extract_schema_from_response(response, lambda r: r.json)
    loaded = from_dict(schema=schema, config=config)
    loaded.app = app
    loaded.location = path
    return loaded


def from_url(
    url: str, *, config: SchemathesisConfig | None = None, wait_for_schema: float | None = None, **kwargs: Any
) -> GraphQLSchema:
    """Load GraphQL schema from a URL via introspection query.

    Args:
        url: Full URL to the GraphQL endpoint
        config: Custom configuration. If `None`, uses auto-discovered config
        wait_for_schema: Maximum time in seconds to wait for schema availability
        **kwargs: Additional parameters passed to `requests.post()` (headers, timeout, auth, etc.).

    Example:
        ```python
        import schemathesis

        # Basic usage
        schema = schemathesis.graphql.from_url("https://api.example.com/graphql")

        # With authentication and timeout
        schema = schemathesis.graphql.from_url(
            "https://api.example.com/graphql",
            headers={"Authorization": "Bearer token"},
            timeout=30,
            wait_for_schema=10.0
        )
        ```

    """
    import requests

    kwargs.setdefault("json", {"query": get_introspection_query()})
    response = load_from_url(requests.post, url=url, wait_for_schema=wait_for_schema, **kwargs)
    schema = extract_schema_from_response(response, lambda r: r.json())
    loaded = from_dict(schema, config=config)
    loaded.location = url
    return loaded


def from_path(
    path: PathLike | str, *, config: SchemathesisConfig | None = None, encoding: str = "utf-8"
) -> GraphQLSchema:
    """Load GraphQL schema from a filesystem path.

    Args:
        path: File path to the GraphQL schema file (.graphql, .gql)
        config: Custom configuration. If `None`, uses auto-discovered config
        encoding: Text encoding for reading the file

    Example:
        ```python
        import schemathesis

        # Load from GraphQL SDL file
        schema = schemathesis.graphql.from_path("./schema.graphql")
        ```

    """
    with open(path, encoding=encoding) as file:
        loaded = from_file(file=file, config=config)
    loaded.location = Path(path).absolute().as_uri()
    return loaded


def from_file(file: IO[str] | str, *, config: SchemathesisConfig | None = None) -> GraphQLSchema:
    """Load GraphQL schema from a file-like object or string.

    Args:
        file: File-like object or raw string containing GraphQL SDL
        config: Custom configuration. If `None`, uses auto-discovered config

    Example:
        ```python
        import schemathesis

        # From GraphQL SDL string
        schema_sdl = '''
            type Query {
                user(id: ID!): User
            }
            type User {
                id: ID!
                name: String!
            }
        '''
        schema = schemathesis.graphql.from_file(schema_sdl)

        # From file object
        with open("schema.graphql") as f:
            schema = schemathesis.graphql.from_file(f)
        ```

    """
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
        schema = cast(dict[str, Any], result.data)
    except Exception as exc:
        try:
            schema = json.loads(data)
            if not isinstance(schema, dict) or "__schema" not in schema:
                _on_invalid_schema(exc)
        except json.JSONDecodeError:
            _on_invalid_schema(exc, extras=[entry for entry in str(exc).splitlines() if entry])
    return from_dict(schema, config=config)


def from_dict(schema: dict[str, Any], *, config: SchemathesisConfig | None = None) -> GraphQLSchema:
    """Load GraphQL schema from a dictionary containing introspection result.

    Args:
        schema: Dictionary containing GraphQL introspection result or wrapped in 'data' key
        config: Custom configuration. If `None`, uses auto-discovered config

    Example:
        ```python
        import schemathesis

        # From introspection result
        introspection = {
            "__schema": {
                "types": [...],
                "queryType": {"name": "Query"},
                # ... rest of introspection result
            }
        }
        schema = schemathesis.graphql.from_dict(introspection)

        # From GraphQL response format (with 'data' wrapper)
        response_data = {
            "data": {
                "__schema": {
                    "types": [...],
                    "queryType": {"name": "Query"}
                }
            }
        }
        schema = schemathesis.graphql.from_dict(response_data)
        ```

    """
    from schemathesis.specs.graphql.schemas import GraphQLSchema

    if "data" in schema:
        schema = schema["data"]
    hook_context = HookContext()
    dispatch("before_load_schema", hook_context, schema)

    if config is None:
        config = SchemathesisConfig.discover()
    project_config = config.projects.get(schema)
    instance = GraphQLSchema(schema, config=project_config)
    instance.filter_set = project_config.operations.filter_set_with(include=instance.filter_set)
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
