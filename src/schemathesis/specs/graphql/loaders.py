from __future__ import annotations

import json
import pathlib
from functools import lru_cache
from json import JSONDecodeError
from typing import IO, TYPE_CHECKING, Any, Callable, Dict, NoReturn, cast

from ...code_samples import CodeSampleStyle
from ...constants import WAIT_FOR_SCHEMA_INTERVAL
from ...exceptions import SchemaError, SchemaErrorType
from ...generation import (
    DEFAULT_DATA_GENERATION_METHODS,
    DataGenerationMethod,
    DataGenerationMethodInput,
    GenerationConfig,
)
from ...hooks import HookContext, dispatch
from ...internal.output import OutputConfig
from ...internal.validation import require_relative_url
from ...loaders import load_schema_from_url
from ...throttling import build_limiter
from ...transports.headers import setup_default_headers
from ...types import PathLike, Specification

if TYPE_CHECKING:
    from graphql import DocumentNode
    from pyrate_limiter import Limiter

    from ...transports.responses import GenericResponse
    from .schemas import GraphQLSchema


@lru_cache
def get_introspection_query() -> str:
    import graphql

    return graphql.get_introspection_query()


@lru_cache
def get_introspection_query_ast() -> DocumentNode:
    import graphql

    query = get_introspection_query()
    return graphql.parse(query)


def from_path(
    path: PathLike,
    *,
    app: Any = None,
    base_url: str | None = None,
    data_generation_methods: DataGenerationMethodInput = DEFAULT_DATA_GENERATION_METHODS,
    generation_config: GenerationConfig | None = None,
    output_config: OutputConfig | None = None,
    code_sample_style: str = CodeSampleStyle.default().name,
    rate_limit: str | None = None,
    encoding: str = "utf8",
    sanitize_output: bool = True,
) -> GraphQLSchema:
    """Load GraphQL schema via a file from an OS path.

    :param path: A path to the schema file.
    :param encoding: The name of the encoding used to decode the file.
    """
    with open(path, encoding=encoding) as fd:
        return from_file(
            fd,
            app=app,
            base_url=base_url,
            data_generation_methods=data_generation_methods,
            code_sample_style=code_sample_style,
            generation_config=generation_config,
            output_config=output_config,
            location=pathlib.Path(path).absolute().as_uri(),
            rate_limit=rate_limit,
            sanitize_output=sanitize_output,
        )


def extract_schema_from_response(response: GenericResponse) -> dict[str, Any]:
    from requests import Response

    try:
        if isinstance(response, Response):
            decoded = response.json()
        else:
            decoded = response.json
    except JSONDecodeError as exc:
        raise SchemaError(
            SchemaErrorType.UNEXPECTED_CONTENT_TYPE,
            "Received unsupported content while expecting a JSON payload for GraphQL",
        ) from exc
    return decoded


def from_url(
    url: str,
    *,
    app: Any = None,
    base_url: str | None = None,
    port: int | None = None,
    data_generation_methods: DataGenerationMethodInput = DEFAULT_DATA_GENERATION_METHODS,
    generation_config: GenerationConfig | None = None,
    code_sample_style: str = CodeSampleStyle.default().name,
    wait_for_schema: float | None = None,
    rate_limit: str | None = None,
    sanitize_output: bool = True,
    **kwargs: Any,
) -> GraphQLSchema:
    """Load GraphQL schema from the network.

    :param url: Schema URL.
    :param Optional[str] base_url: Base URL to send requests to.
    :param Optional[int] port: An optional port if you don't want to pass the ``base_url`` parameter, but only to change
                               port in ``url``.
    :param app: A WSGI app instance.
    :return: GraphQLSchema
    """
    import backoff
    import requests

    setup_default_headers(kwargs)
    kwargs.setdefault("json", {"query": get_introspection_query()})
    if port:
        from yarl import URL

        url = str(URL(url).with_port(port))
        if not base_url:
            base_url = url

    if wait_for_schema is not None:

        @backoff.on_exception(  # type: ignore
            backoff.constant,
            requests.exceptions.ConnectionError,
            max_time=wait_for_schema,
            interval=WAIT_FOR_SCHEMA_INTERVAL,
        )
        def _load_schema(_uri: str, **_kwargs: Any) -> requests.Response:
            return requests.post(_uri, **kwargs)

    else:
        _load_schema = requests.post

    response = load_schema_from_url(lambda: _load_schema(url, **kwargs))
    raw_schema = extract_schema_from_response(response)
    return from_dict(
        raw_schema=raw_schema,
        location=url,
        base_url=base_url,
        app=app,
        data_generation_methods=data_generation_methods,
        code_sample_style=code_sample_style,
        rate_limit=rate_limit,
        sanitize_output=sanitize_output,
    )


def from_file(
    file: IO[str] | str,
    *,
    app: Any = None,
    base_url: str | None = None,
    data_generation_methods: DataGenerationMethodInput = DEFAULT_DATA_GENERATION_METHODS,
    generation_config: GenerationConfig | None = None,
    output_config: OutputConfig | None = None,
    code_sample_style: str = CodeSampleStyle.default().name,
    location: str | None = None,
    rate_limit: str | None = None,
    sanitize_output: bool = True,
) -> GraphQLSchema:
    """Load GraphQL schema from a file descriptor or a string.

    :param file: Could be a file descriptor, string or bytes.
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
        raw_schema = cast(Dict[str, Any], result.data)
    except Exception as exc:
        try:
            raw_schema = json.loads(data)
            if not isinstance(raw_schema, dict) or "__schema" not in raw_schema:
                _on_invalid_schema(exc)
        except json.JSONDecodeError:
            _on_invalid_schema(exc, extras=[entry for entry in str(exc).splitlines() if entry])
    return from_dict(
        raw_schema,
        app=app,
        base_url=base_url,
        data_generation_methods=data_generation_methods,
        generation_config=generation_config,
        output_config=output_config,
        code_sample_style=code_sample_style,
        location=location,
        rate_limit=rate_limit,
        sanitize_output=sanitize_output,
    )


def _on_invalid_schema(exc: Exception, extras: list[str] | None = None) -> NoReturn:
    raise SchemaError(
        SchemaErrorType.GRAPHQL_INVALID_SCHEMA,
        "The provided API schema does not appear to be a valid GraphQL schema",
        extras=extras or [],
    ) from exc


def from_dict(
    raw_schema: dict[str, Any],
    *,
    app: Any = None,
    base_url: str | None = None,
    location: str | None = None,
    data_generation_methods: DataGenerationMethodInput = DEFAULT_DATA_GENERATION_METHODS,
    generation_config: GenerationConfig | None = None,
    output_config: OutputConfig | None = None,
    code_sample_style: str = CodeSampleStyle.default().name,
    rate_limit: str | None = None,
    sanitize_output: bool = True,
) -> GraphQLSchema:
    """Load GraphQL schema from a Python dictionary.

    :param dict raw_schema: A schema to load.
    :param Optional[str] location: Optional schema location. Either a full URL or a filesystem path.
    :param Optional[str] base_url: Base URL to send requests to.
    :param app: A WSGI app instance.
    :return: GraphQLSchema
    """
    from ... import transports
    from .schemas import GraphQLSchema

    _code_sample_style = CodeSampleStyle.from_str(code_sample_style)
    hook_context = HookContext()
    if "data" in raw_schema:
        raw_schema = raw_schema["data"]
    dispatch("before_load_schema", hook_context, raw_schema)
    rate_limiter: Limiter | None = None
    if rate_limit is not None:
        rate_limiter = build_limiter(rate_limit)
    instance = GraphQLSchema(
        raw_schema,
        specification=Specification.GRAPHQL,
        location=location,
        base_url=base_url,
        app=app,
        data_generation_methods=DataGenerationMethod.ensure_list(data_generation_methods),
        generation_config=generation_config or GenerationConfig(),
        output_config=output_config or OutputConfig(),
        code_sample_style=_code_sample_style,
        rate_limiter=rate_limiter,
        sanitize_output=sanitize_output,
        transport=transports.get(app),
    )  # type: ignore
    dispatch("after_load_schema", hook_context, instance)
    return instance


def from_wsgi(
    schema_path: str,
    app: Any,
    *,
    base_url: str | None = None,
    data_generation_methods: DataGenerationMethodInput = DEFAULT_DATA_GENERATION_METHODS,
    generation_config: GenerationConfig | None = None,
    output_config: OutputConfig | None = None,
    code_sample_style: str = CodeSampleStyle.default().name,
    rate_limit: str | None = None,
    sanitize_output: bool = True,
    **kwargs: Any,
) -> GraphQLSchema:
    """Load GraphQL schema from a WSGI app.

    :param str schema_path: An in-app relative URL to the schema.
    :param app: A WSGI app instance.
    :param Optional[str] base_url: Base URL to send requests to.
    :return: GraphQLSchema
    """
    from werkzeug import Client

    from ...transports.responses import WSGIResponse

    require_relative_url(schema_path)
    setup_default_headers(kwargs)
    kwargs.setdefault("json", {"query": get_introspection_query()})
    client = Client(app, WSGIResponse)
    response = load_schema_from_url(lambda: client.post(schema_path, **kwargs))
    raw_schema = extract_schema_from_response(response)
    return from_dict(
        raw_schema=raw_schema,
        location=schema_path,
        base_url=base_url,
        app=app,
        data_generation_methods=data_generation_methods,
        generation_config=generation_config,
        output_config=output_config,
        code_sample_style=code_sample_style,
        rate_limit=rate_limit,
        sanitize_output=sanitize_output,
    )


def from_asgi(
    schema_path: str,
    app: Any,
    *,
    base_url: str | None = None,
    data_generation_methods: DataGenerationMethodInput = DEFAULT_DATA_GENERATION_METHODS,
    generation_config: GenerationConfig | None = None,
    output_config: OutputConfig | None = None,
    code_sample_style: str = CodeSampleStyle.default().name,
    rate_limit: str | None = None,
    sanitize_output: bool = True,
    **kwargs: Any,
) -> GraphQLSchema:
    """Load GraphQL schema from an ASGI app.

    :param str schema_path: An in-app relative URL to the schema.
    :param app: An ASGI app instance.
    :param Optional[str] base_url: Base URL to send requests to.
    """
    from starlette_testclient import TestClient as ASGIClient

    require_relative_url(schema_path)
    setup_default_headers(kwargs)
    kwargs.setdefault("json", {"query": get_introspection_query()})
    client = ASGIClient(app)
    response = load_schema_from_url(lambda: client.post(schema_path, **kwargs))
    raw_schema = extract_schema_from_response(response)
    return from_dict(
        raw_schema=raw_schema,
        location=schema_path,
        base_url=base_url,
        app=app,
        data_generation_methods=data_generation_methods,
        generation_config=generation_config,
        output_config=output_config,
        code_sample_style=code_sample_style,
        rate_limit=rate_limit,
        sanitize_output=sanitize_output,
    )


def get_loader_for_app(app: Any) -> Callable:
    from starlette.applications import Starlette

    if isinstance(app, Starlette):
        return from_asgi
    return from_wsgi
