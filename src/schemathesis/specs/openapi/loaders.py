import io
import pathlib
from typing import IO, Any, Callable, Dict, List, Optional, Tuple, Union, cast
from urllib.parse import urljoin

import backoff
import jsonschema
import requests
import yaml
from jsonschema import ValidationError
from starlette.applications import Starlette
from starlette.testclient import TestClient as ASGIClient
from werkzeug.test import Client
from yarl import URL

from ...constants import DEFAULT_DATA_GENERATION_METHODS, WAIT_FOR_SCHEMA_INTERVAL, CodeSampleStyle
from ...exceptions import HTTPError, SchemaLoadingError
from ...hooks import HookContext, dispatch
from ...lazy import LazySchema
from ...types import DataGenerationMethodInput, Filter, NotSet, PathLike
from ...utils import (
    NOT_SET,
    StringDatesYAMLLoader,
    WSGIResponse,
    prepare_data_generation_methods,
    require_relative_url,
    setup_headers,
)
from . import definitions, validation
from .schemas import BaseOpenAPISchema, OpenApi30, SwaggerV20


def from_path(
    path: PathLike,
    *,
    app: Any = None,
    base_url: Optional[str] = None,
    method: Optional[Filter] = None,
    endpoint: Optional[Filter] = None,
    tag: Optional[Filter] = None,
    operation_id: Optional[Filter] = None,
    skip_deprecated_operations: bool = False,
    validate_schema: bool = False,
    force_schema_version: Optional[str] = None,
    data_generation_methods: DataGenerationMethodInput = DEFAULT_DATA_GENERATION_METHODS,
    code_sample_style: str = CodeSampleStyle.default().name,
    encoding: str = "utf8",
) -> BaseOpenAPISchema:
    """Load Open API schema via a file from an OS path.

    :param path: A path to the schema file.
    :param encoding: The name of the encoding used to decode the file.
    """
    with open(path, encoding=encoding) as fd:
        return from_file(
            fd,
            app=app,
            base_url=base_url,
            method=method,
            endpoint=endpoint,
            tag=tag,
            operation_id=operation_id,
            skip_deprecated_operations=skip_deprecated_operations,
            validate_schema=validate_schema,
            force_schema_version=force_schema_version,
            data_generation_methods=data_generation_methods,
            code_sample_style=code_sample_style,
            location=pathlib.Path(path).absolute().as_uri(),
        )


def from_uri(
    uri: str,
    *,
    app: Any = None,
    base_url: Optional[str] = None,
    port: Optional[int] = None,
    method: Optional[Filter] = None,
    endpoint: Optional[Filter] = None,
    tag: Optional[Filter] = None,
    operation_id: Optional[Filter] = None,
    skip_deprecated_operations: bool = False,
    validate_schema: bool = False,
    force_schema_version: Optional[str] = None,
    data_generation_methods: DataGenerationMethodInput = DEFAULT_DATA_GENERATION_METHODS,
    code_sample_style: str = CodeSampleStyle.default().name,
    wait_for_schema: Optional[float] = None,
    **kwargs: Any,
) -> BaseOpenAPISchema:
    """Load Open API schema from the network.

    :param str uri: Schema URL.
    """
    setup_headers(kwargs)
    if not base_url and port:
        base_url = str(URL(uri).with_port(port))

    if wait_for_schema is not None:

        @backoff.on_exception(  # type: ignore
            backoff.constant,
            requests.exceptions.ConnectionError,
            max_time=wait_for_schema,
            interval=WAIT_FOR_SCHEMA_INTERVAL,
        )
        def _load_schema(_uri: str, **_kwargs: Any) -> requests.Response:
            return requests.get(_uri, **kwargs)

    else:
        _load_schema = requests.get

    response = _load_schema(uri, **kwargs)
    HTTPError.raise_for_status(response)
    try:
        return from_file(
            response.text,
            app=app,
            base_url=base_url,
            method=method,
            endpoint=endpoint,
            tag=tag,
            operation_id=operation_id,
            skip_deprecated_operations=skip_deprecated_operations,
            validate_schema=validate_schema,
            force_schema_version=force_schema_version,
            data_generation_methods=data_generation_methods,
            code_sample_style=code_sample_style,
            location=uri,
        )
    except SchemaLoadingError as exc:
        content_type = response.headers.get("Content-Type")
        if content_type is not None:
            raise SchemaLoadingError(f"{exc.args[0]}. The actual response has `{content_type}` Content-Type") from exc
        raise


YAML_LOADING_ERROR = (
    "It seems like the schema you are trying to load is malformed. "
    "Schemathesis expects API schemas in JSON or YAML formats"
)


def from_file(
    file: Union[IO[str], str],
    *,
    app: Any = None,
    base_url: Optional[str] = None,
    method: Optional[Filter] = None,
    endpoint: Optional[Filter] = None,
    tag: Optional[Filter] = None,
    operation_id: Optional[Filter] = None,
    skip_deprecated_operations: bool = False,
    validate_schema: bool = False,
    force_schema_version: Optional[str] = None,
    data_generation_methods: DataGenerationMethodInput = DEFAULT_DATA_GENERATION_METHODS,
    code_sample_style: str = CodeSampleStyle.default().name,
    location: Optional[str] = None,
    **kwargs: Any,  # needed in the runner to have compatible API across all loaders
) -> BaseOpenAPISchema:
    """Load Open API schema from a file descriptor, string or bytes.

    :param file: Could be a file descriptor, string or bytes.
    """
    try:
        raw = yaml.load(file, StringDatesYAMLLoader)
        return from_dict(
            raw,
            app=app,
            base_url=base_url,
            method=method,
            endpoint=endpoint,
            tag=tag,
            operation_id=operation_id,
            skip_deprecated_operations=skip_deprecated_operations,
            validate_schema=validate_schema,
            force_schema_version=force_schema_version,
            data_generation_methods=data_generation_methods,
            code_sample_style=code_sample_style,
            location=location,
        )
    except yaml.YAMLError as exc:
        raise SchemaLoadingError(YAML_LOADING_ERROR) from exc


def from_dict(
    raw_schema: Dict[str, Any],
    *,
    app: Any = None,
    base_url: Optional[str] = None,
    method: Optional[Filter] = None,
    endpoint: Optional[Filter] = None,
    tag: Optional[Filter] = None,
    operation_id: Optional[Filter] = None,
    skip_deprecated_operations: bool = False,
    validate_schema: bool = False,
    force_schema_version: Optional[str] = None,
    data_generation_methods: DataGenerationMethodInput = DEFAULT_DATA_GENERATION_METHODS,
    code_sample_style: str = CodeSampleStyle.default().name,
    location: Optional[str] = None,
) -> BaseOpenAPISchema:
    """Load Open API schema from a Python dictionary.

    :param dict raw_schema: A schema to load.
    """
    _code_sample_style = CodeSampleStyle.from_str(code_sample_style)
    hook_context = HookContext()
    dispatch("before_load_schema", hook_context, raw_schema)

    def init_openapi_2() -> SwaggerV20:
        _maybe_validate_schema(raw_schema, definitions.SWAGGER_20_VALIDATOR, validate_schema)
        instance = SwaggerV20(
            raw_schema,
            app=app,
            base_url=base_url,
            method=method,
            endpoint=endpoint,
            tag=tag,
            operation_id=operation_id,
            skip_deprecated_operations=skip_deprecated_operations,
            validate_schema=validate_schema,
            data_generation_methods=prepare_data_generation_methods(data_generation_methods),
            code_sample_style=_code_sample_style,
            location=location,
        )
        dispatch("after_load_schema", hook_context, instance)
        return instance

    def init_openapi_3() -> OpenApi30:
        _maybe_validate_schema(raw_schema, definitions.OPENAPI_30_VALIDATOR, validate_schema)
        instance = OpenApi30(
            raw_schema,
            app=app,
            base_url=base_url,
            method=method,
            endpoint=endpoint,
            tag=tag,
            operation_id=operation_id,
            skip_deprecated_operations=skip_deprecated_operations,
            validate_schema=validate_schema,
            data_generation_methods=prepare_data_generation_methods(data_generation_methods),
            code_sample_style=_code_sample_style,
            location=location,
        )
        dispatch("after_load_schema", hook_context, instance)
        return instance

    if force_schema_version == "20":
        return init_openapi_2()
    if force_schema_version == "30":
        return init_openapi_3()
    if "swagger" in raw_schema:
        return init_openapi_2()
    if "openapi" in raw_schema:
        return init_openapi_3()
    raise SchemaLoadingError("Unsupported schema type")


# It is a common case when API schemas are stored in the YAML format and HTTP status codes are numbers
# The Open API spec requires HTTP status codes as strings
DOC_ENTRY = "https://github.com/OAI/OpenAPI-Specification/blob/main/versions/3.0.3.md#patterned-fields-1"
NUMERIC_STATUS_CODES_MESSAGE = f"""The input schema contains HTTP status codes as numbers.
The Open API spec requires them to be strings:
{DOC_ENTRY}
Please, stringify the following status codes:"""
NON_STRING_OBJECT_KEY = "The input schema contains non-string keys in sub-schemas"


def _format_status_codes(status_codes: List[Tuple[int, List[Union[str, int]]]]) -> str:
    buffer = io.StringIO()
    for status_code, path in status_codes:
        buffer.write(f" - {status_code} at schema['paths']")
        for chunk in path:
            buffer.write(f"[{repr(chunk)}]")
        buffer.write("['responses']\n")
    return buffer.getvalue().rstrip()


def _maybe_validate_schema(
    instance: Dict[str, Any], validator: jsonschema.validators.Draft4Validator, validate_schema: bool
) -> None:
    if validate_schema:
        try:
            validator.validate(instance)
        except TypeError as exc:
            if validation.is_pattern_error(exc):
                status_codes = validation.find_numeric_http_status_codes(instance)
                if status_codes:
                    message = _format_status_codes(status_codes)
                    raise SchemaLoadingError(f"{NUMERIC_STATUS_CODES_MESSAGE}\n{message}") from exc
                # Some other pattern error
                raise SchemaLoadingError(NON_STRING_OBJECT_KEY) from exc
            raise SchemaLoadingError("Invalid schema") from exc
        except ValidationError as exc:
            raise SchemaLoadingError("The input schema is not a valid Open API schema") from exc


def from_pytest_fixture(
    fixture_name: str,
    *,
    app: Any = NOT_SET,
    base_url: Union[Optional[str], NotSet] = NOT_SET,
    method: Optional[Filter] = NOT_SET,
    endpoint: Optional[Filter] = NOT_SET,
    tag: Optional[Filter] = NOT_SET,
    operation_id: Optional[Filter] = NOT_SET,
    skip_deprecated_operations: bool = False,
    validate_schema: bool = False,
    data_generation_methods: Union[DataGenerationMethodInput, NotSet] = NOT_SET,
    code_sample_style: str = CodeSampleStyle.default().name,
) -> LazySchema:
    """Load schema from a ``pytest`` fixture.

    It is useful if you don't want to make network requests during module loading. With this loader you can defer it
    to a fixture.

    Note, the fixture should return a ``BaseSchema`` instance loaded with another loader.

    :param str fixture_name: The name of a fixture to load.
    """
    _code_sample_style = CodeSampleStyle.from_str(code_sample_style)
    _data_generation_methods: Union[DataGenerationMethodInput, NotSet]
    if data_generation_methods is not NOT_SET:
        data_generation_methods = cast(DataGenerationMethodInput, data_generation_methods)
        _data_generation_methods = prepare_data_generation_methods(data_generation_methods)
    else:
        _data_generation_methods = data_generation_methods
    return LazySchema(
        fixture_name,
        app=app,
        base_url=base_url,
        method=method,
        endpoint=endpoint,
        tag=tag,
        operation_id=operation_id,
        skip_deprecated_operations=skip_deprecated_operations,
        validate_schema=validate_schema,
        data_generation_methods=_data_generation_methods,
        code_sample_style=_code_sample_style,
    )


def from_wsgi(
    schema_path: str,
    app: Any,
    *,
    base_url: Optional[str] = None,
    method: Optional[Filter] = None,
    endpoint: Optional[Filter] = None,
    tag: Optional[Filter] = None,
    operation_id: Optional[Filter] = None,
    skip_deprecated_operations: bool = False,
    validate_schema: bool = False,
    force_schema_version: Optional[str] = None,
    data_generation_methods: DataGenerationMethodInput = DEFAULT_DATA_GENERATION_METHODS,
    code_sample_style: str = CodeSampleStyle.default().name,
    **kwargs: Any,
) -> BaseOpenAPISchema:
    """Load Open API schema from a WSGI app.

    :param str schema_path: An in-app relative URL to the schema.
    :param app: A WSGI app instance.
    """
    require_relative_url(schema_path)
    setup_headers(kwargs)
    client = Client(app, WSGIResponse)
    response = client.get(schema_path, **kwargs)
    HTTPError.check_response(response, schema_path)
    return from_file(
        response.data,
        app=app,
        base_url=base_url,
        method=method,
        endpoint=endpoint,
        tag=tag,
        operation_id=operation_id,
        skip_deprecated_operations=skip_deprecated_operations,
        validate_schema=validate_schema,
        force_schema_version=force_schema_version,
        data_generation_methods=data_generation_methods,
        code_sample_style=code_sample_style,
        location=schema_path,
    )


def get_loader_for_app(app: Any) -> Callable:
    if isinstance(app, Starlette):
        return from_asgi
    if app.__class__.__module__.startswith("aiohttp."):
        return from_aiohttp
    return from_wsgi


def from_aiohttp(
    schema_path: str,
    app: Any,
    *,
    base_url: Optional[str] = None,
    method: Optional[Filter] = None,
    endpoint: Optional[Filter] = None,
    tag: Optional[Filter] = None,
    operation_id: Optional[Filter] = None,
    skip_deprecated_operations: bool = False,
    validate_schema: bool = False,
    force_schema_version: Optional[str] = None,
    data_generation_methods: DataGenerationMethodInput = DEFAULT_DATA_GENERATION_METHODS,
    code_sample_style: str = CodeSampleStyle.default().name,
    **kwargs: Any,
) -> BaseOpenAPISchema:
    """Load Open API schema from an AioHTTP app.

    :param str schema_path: An in-app relative URL to the schema.
    :param app: An AioHTTP app instance.
    """
    from ...extra._aiohttp import run_server  # pylint: disable=import-outside-toplevel

    port = run_server(app)
    app_url = f"http://127.0.0.1:{port}/"
    url = urljoin(app_url, schema_path)
    return from_uri(
        url,
        base_url=base_url,
        method=method,
        endpoint=endpoint,
        tag=tag,
        operation_id=operation_id,
        skip_deprecated_operations=skip_deprecated_operations,
        validate_schema=validate_schema,
        force_schema_version=force_schema_version,
        data_generation_methods=data_generation_methods,
        code_sample_style=code_sample_style,
        **kwargs,
    )


def from_asgi(
    schema_path: str,
    app: Any,
    *,
    base_url: Optional[str] = None,
    method: Optional[Filter] = None,
    endpoint: Optional[Filter] = None,
    tag: Optional[Filter] = None,
    operation_id: Optional[Filter] = None,
    skip_deprecated_operations: bool = False,
    validate_schema: bool = False,
    force_schema_version: Optional[str] = None,
    data_generation_methods: DataGenerationMethodInput = DEFAULT_DATA_GENERATION_METHODS,
    code_sample_style: str = CodeSampleStyle.default().name,
    **kwargs: Any,
) -> BaseOpenAPISchema:
    """Load Open API schema from an ASGI app.

    :param str schema_path: An in-app relative URL to the schema.
    :param app: An ASGI app instance.
    """
    require_relative_url(schema_path)
    setup_headers(kwargs)
    client = ASGIClient(app)
    response = client.get(schema_path, **kwargs)
    HTTPError.check_response(response, schema_path)
    return from_file(
        response.text,
        app=app,
        base_url=base_url,
        method=method,
        endpoint=endpoint,
        tag=tag,
        operation_id=operation_id,
        skip_deprecated_operations=skip_deprecated_operations,
        validate_schema=validate_schema,
        force_schema_version=force_schema_version,
        data_generation_methods=data_generation_methods,
        code_sample_style=code_sample_style,
        location=schema_path,
    )
