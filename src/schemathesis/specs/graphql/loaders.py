import pathlib
from typing import IO, Any, Callable, Dict, Optional, Union, cast

import backoff
import graphql
import requests
from graphql import ExecutionResult
from starlette.applications import Starlette
from starlette.testclient import TestClient as ASGIClient
from werkzeug import Client
from yarl import URL

from ...constants import DEFAULT_DATA_GENERATION_METHODS, WAIT_FOR_SCHEMA_INTERVAL, CodeSampleStyle
from ...exceptions import HTTPError
from ...hooks import HookContext, dispatch
from ...types import DataGenerationMethodInput, PathLike
from ...utils import WSGIResponse, prepare_data_generation_methods, require_relative_url, setup_headers
from .schemas import GraphQLSchema

INTROSPECTION_QUERY = graphql.get_introspection_query()
INTROSPECTION_QUERY_AST = graphql.parse(INTROSPECTION_QUERY)


def from_path(
    path: PathLike,
    *,
    app: Any = None,
    base_url: Optional[str] = None,
    data_generation_methods: DataGenerationMethodInput = DEFAULT_DATA_GENERATION_METHODS,
    code_sample_style: str = CodeSampleStyle.default().name,
    encoding: str = "utf8",
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
            location=pathlib.Path(path).absolute().as_uri(),
        )


def from_url(
    url: str,
    *,
    app: Any = None,
    base_url: Optional[str] = None,
    port: Optional[int] = None,
    data_generation_methods: DataGenerationMethodInput = DEFAULT_DATA_GENERATION_METHODS,
    code_sample_style: str = CodeSampleStyle.default().name,
    wait_for_schema: Optional[float] = None,
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
    setup_headers(kwargs)
    kwargs.setdefault("json", {"query": INTROSPECTION_QUERY})
    if not base_url and port:
        base_url = str(URL(url).with_port(port))

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
    response = _load_schema(url, **kwargs)
    HTTPError.raise_for_status(response)
    decoded = response.json()
    return from_dict(
        raw_schema=decoded["data"],
        location=url,
        base_url=base_url,
        app=app,
        data_generation_methods=data_generation_methods,
        code_sample_style=code_sample_style,
    )


def from_file(
    file: Union[IO[str], str],
    *,
    app: Any = None,
    base_url: Optional[str] = None,
    data_generation_methods: DataGenerationMethodInput = DEFAULT_DATA_GENERATION_METHODS,
    code_sample_style: str = CodeSampleStyle.default().name,
    location: Optional[str] = None,
) -> GraphQLSchema:
    """Load GraphQL schema from a file descriptor or a string.

    :param file: Could be a file descriptor, string or bytes.
    """
    if isinstance(file, str):
        data = file
    else:
        data = file.read()
    document = graphql.build_schema(data)
    result = graphql.execute(document, INTROSPECTION_QUERY_AST)
    # TYPES: We don't pass `is_awaitable` above, therefore `result` is of the `ExecutionResult` type
    result = cast(ExecutionResult, result)
    # TYPES:
    #  - `document` is a valid schema, because otherwise `build_schema` will rise an error;
    #  - `INTROSPECTION_QUERY` is a valid query - it is known upfront;
    # Therefore the execution result is always valid at this point and `result.data` is not `None`
    raw_schema = cast(Dict[str, Any], result.data)
    return from_dict(
        raw_schema,
        app=app,
        base_url=base_url,
        data_generation_methods=data_generation_methods,
        code_sample_style=code_sample_style,
        location=location,
    )


def from_dict(
    raw_schema: Dict[str, Any],
    *,
    app: Any = None,
    base_url: Optional[str] = None,
    location: Optional[str] = None,
    data_generation_methods: DataGenerationMethodInput = DEFAULT_DATA_GENERATION_METHODS,
    code_sample_style: str = CodeSampleStyle.default().name,
) -> GraphQLSchema:
    """Load GraphQL schema from a Python dictionary.

    :param dict raw_schema: A schema to load.
    :param Optional[str] location: Optional schema location. Either a full URL or a filesystem path.
    :param Optional[str] base_url: Base URL to send requests to.
    :param app: A WSGI app instance.
    :return: GraphQLSchema
    """
    _code_sample_style = CodeSampleStyle.from_str(code_sample_style)
    hook_context = HookContext()
    dispatch("before_load_schema", hook_context, raw_schema)
    instance = GraphQLSchema(
        raw_schema,
        location=location,
        base_url=base_url,
        app=app,
        data_generation_methods=prepare_data_generation_methods(data_generation_methods),
        code_sample_style=_code_sample_style,
    )  # type: ignore
    dispatch("after_load_schema", hook_context, instance)
    return instance


def from_wsgi(
    schema_path: str,
    app: Any,
    *,
    base_url: Optional[str] = None,
    data_generation_methods: DataGenerationMethodInput = DEFAULT_DATA_GENERATION_METHODS,
    code_sample_style: str = CodeSampleStyle.default().name,
    **kwargs: Any,
) -> GraphQLSchema:
    """Load GraphQL schema from a WSGI app.

    :param str schema_path: An in-app relative URL to the schema.
    :param app: A WSGI app instance.
    :param Optional[str] base_url: Base URL to send requests to.
    :return: GraphQLSchema
    """
    require_relative_url(schema_path)
    setup_headers(kwargs)
    kwargs.setdefault("json", {"query": INTROSPECTION_QUERY})
    client = Client(app, WSGIResponse)
    response = client.post(schema_path, **kwargs)
    HTTPError.check_response(response, schema_path)
    return from_dict(
        raw_schema=response.json["data"],
        location=schema_path,
        base_url=base_url,
        app=app,
        data_generation_methods=data_generation_methods,
        code_sample_style=code_sample_style,
    )


def from_asgi(
    schema_path: str,
    app: Any,
    *,
    base_url: Optional[str] = None,
    data_generation_methods: DataGenerationMethodInput = DEFAULT_DATA_GENERATION_METHODS,
    code_sample_style: str = CodeSampleStyle.default().name,
    **kwargs: Any,
) -> GraphQLSchema:
    """Load GraphQL schema from an ASGI app.

    :param str schema_path: An in-app relative URL to the schema.
    :param app: An ASGI app instance.
    :param Optional[str] base_url: Base URL to send requests to.
    """
    require_relative_url(schema_path)
    setup_headers(kwargs)
    kwargs.setdefault("json", {"query": INTROSPECTION_QUERY})
    client = ASGIClient(app)
    response = client.post(schema_path, **kwargs)
    HTTPError.check_response(response, schema_path)
    return from_dict(
        response.json()["data"],
        location=schema_path,
        base_url=base_url,
        app=app,
        data_generation_methods=data_generation_methods,
        code_sample_style=code_sample_style,
    )


def get_loader_for_app(app: Any) -> Callable:
    if isinstance(app, Starlette):
        return from_asgi
    return from_wsgi
