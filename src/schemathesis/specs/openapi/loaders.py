import io
import json
import pathlib
import re
from typing import IO, Any, Callable, Dict, List, Optional, Tuple, Union, cast
from urllib.parse import urljoin

import backoff
import jsonschema
import requests
import yaml
from jsonschema import ValidationError
from pyrate_limiter import Limiter
from starlette.applications import Starlette
from starlette_testclient import TestClient as ASGIClient
from werkzeug.test import Client
from yarl import URL

from ... import experimental, fixups
from ...code_samples import CodeSampleStyle
from ...constants import DEFAULT_DATA_GENERATION_METHODS, WAIT_FOR_SCHEMA_INTERVAL
from ...exceptions import SchemaError, SchemaErrorType
from ...hooks import HookContext, dispatch
from ...lazy import LazySchema
from ...loaders import load_schema_from_url
from ...throttling import build_limiter
from ...types import DataGenerationMethodInput, Filter, NotSet, PathLike
from ...utils import (
    NOT_SET,
    GenericResponse,
    StringDatesYAMLLoader,
    WSGIResponse,
    is_json_media_type,
    prepare_data_generation_methods,
    require_relative_url,
    setup_headers,
)
from . import definitions, validation
from .schemas import BaseOpenAPISchema, OpenApi30, SwaggerV20


def _is_json_response(response: GenericResponse) -> bool:
    """Guess if the response contains JSON."""
    content_type = response.headers.get("Content-Type")
    if content_type is not None:
        return is_json_media_type(content_type)
    return False


def _is_json_path(path: PathLike) -> bool:
    if isinstance(path, str):
        return path.endswith(".json")
    return path.suffix == ".json"


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
    rate_limit: Optional[str] = None,
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
            rate_limit=rate_limit,
            __expects_json=_is_json_path(path),
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
    rate_limit: Optional[str] = None,
    **kwargs: Any,
) -> BaseOpenAPISchema:
    """Load Open API schema from the network.

    :param str uri: Schema URL.
    """
    setup_headers(kwargs)
    if port:
        uri = str(URL(uri).with_port(port))
        if not base_url:
            base_url = uri

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

    response = load_schema_from_url(lambda: _load_schema(uri, **kwargs))
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
        rate_limit=rate_limit,
        __expects_json=_is_json_response(response),
    )


SCHEMA_LOADING_ERROR = "Received unsupported content while expecting a JSON or YAML payload for Open API"


def _load_yaml(data: str) -> Dict[str, Any]:
    try:
        return yaml.load(data, StringDatesYAMLLoader)
    except yaml.YAMLError as exc:
        raise SchemaError(SchemaErrorType.UNEXPECTED_CONTENT_TYPE, SCHEMA_LOADING_ERROR) from exc


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
    rate_limit: Optional[str] = None,
    __expects_json: bool = False,
    **kwargs: Any,  # needed in the runner to have compatible API across all loaders
) -> BaseOpenAPISchema:
    """Load Open API schema from a file descriptor, string or bytes.

    :param file: Could be a file descriptor, string or bytes.
    """
    if hasattr(file, "read"):
        data = file.read()  # type: ignore
    else:
        data = file
    if __expects_json:
        try:
            raw = json.loads(data)
        except json.JSONDecodeError:
            # Fallback to a slower YAML loader. This way we'll still load schemas from responses with
            # invalid `Content-Type` headers or YAML files that have the `.json` extension.
            # This is a rare case, and it will be slower but trying JSON first improves a more common use case
            raw = _load_yaml(data)
    else:
        raw = _load_yaml(data)
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
        rate_limit=rate_limit,
    )


def _is_fast_api(app: Any) -> bool:
    for cls in app.__class__.__mro__:
        if f"{cls.__module__}.{cls.__qualname__}" == "fastapi.applications.FastAPI":
            return True
    return False


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
    rate_limit: Optional[str] = None,
) -> BaseOpenAPISchema:
    """Load Open API schema from a Python dictionary.

    :param dict raw_schema: A schema to load.
    """
    _code_sample_style = CodeSampleStyle.from_str(code_sample_style)
    hook_context = HookContext()
    is_openapi_31 = raw_schema.get("openapi", "").startswith("3.1")
    is_fast_api_fixup_installed = fixups.is_installed("fast_api")
    if is_fast_api_fixup_installed and is_openapi_31:
        fixups.fast_api.uninstall()
    elif _is_fast_api(app):
        fixups.fast_api.adjust_schema(raw_schema)
    dispatch("before_load_schema", hook_context, raw_schema)
    rate_limiter: Optional[Limiter] = None
    if rate_limit is not None:
        rate_limiter = build_limiter(rate_limit)

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
            rate_limiter=rate_limiter,
        )
        dispatch("after_load_schema", hook_context, instance)
        return instance

    def init_openapi_3(forced: bool) -> OpenApi30:
        version = raw_schema["openapi"]
        if (
            not (is_openapi_31 and experimental.OPEN_API_3_1.is_enabled)
            and not forced
            and not OPENAPI_30_VERSION_RE.match(version)
        ):
            raise SchemaError(
                SchemaErrorType.OPEN_API_UNSUPPORTED_VERSION,
                f"The provided schema uses Open API {version}, which is currently not supported.",
            )
        if is_openapi_31:
            validator = definitions.OPENAPI_31_VALIDATOR
        else:
            validator = definitions.OPENAPI_30_VALIDATOR
        _maybe_validate_schema(raw_schema, validator, validate_schema)
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
            rate_limiter=rate_limiter,
        )
        dispatch("after_load_schema", hook_context, instance)
        return instance

    if force_schema_version == "20":
        return init_openapi_2()
    if force_schema_version == "30":
        return init_openapi_3(forced=True)
    if "swagger" in raw_schema:
        return init_openapi_2()
    if "openapi" in raw_schema:
        return init_openapi_3(forced=False)
    raise SchemaError(
        SchemaErrorType.OPEN_API_UNSPECIFIED_VERSION,
        "Unable to determine the Open API version as it's not specified in the document.",
    )


OPENAPI_30_VERSION_RE = re.compile(r"^3\.0\.\d(-.+)?$")

# It is a common case when API schemas are stored in the YAML format and HTTP status codes are numbers
# The Open API spec requires HTTP status codes as strings
DOC_ENTRY = "https://github.com/OAI/OpenAPI-Specification/blob/main/versions/3.0.3.md#patterned-fields-1"
NUMERIC_STATUS_CODES_MESSAGE = f"""Numeric HTTP status codes detected in your YAML schema.
According to the Open API specification, status codes must be strings, not numbers.
For more details, check the Open API documentation: {DOC_ENTRY}

Please, stringify the following status codes:"""
NON_STRING_OBJECT_KEY_MESSAGE = (
    "The Open API specification requires all keys in the schema to be strings. "
    "You have some keys that are not strings."
)


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
                    raise SchemaError(
                        SchemaErrorType.YAML_NUMERIC_STATUS_CODES, f"{NUMERIC_STATUS_CODES_MESSAGE}\n{message}"
                    ) from exc
                # Some other pattern error
                raise SchemaError(SchemaErrorType.YAML_NON_STRING_KEYS, NON_STRING_OBJECT_KEY_MESSAGE) from exc
            raise SchemaError(SchemaErrorType.UNCLASSIFIED, "Unknown error") from exc
        except ValidationError as exc:
            raise SchemaError(
                SchemaErrorType.OPEN_API_INVALID_SCHEMA,
                "The provided API schema does not appear to be a valid OpenAPI schema",
                extras=[entry for entry in str(exc).splitlines() if entry],
            ) from exc


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
    rate_limit: Optional[str] = None,
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
    rate_limiter: Optional[Limiter] = None
    if rate_limit is not None:
        rate_limiter = build_limiter(rate_limit)
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
        rate_limiter=rate_limiter,
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
    rate_limit: Optional[str] = None,
    **kwargs: Any,
) -> BaseOpenAPISchema:
    """Load Open API schema from a WSGI app.

    :param str schema_path: An in-app relative URL to the schema.
    :param app: A WSGI app instance.
    """
    require_relative_url(schema_path)
    setup_headers(kwargs)
    client = Client(app, WSGIResponse)
    response = load_schema_from_url(lambda: client.get(schema_path, **kwargs))
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
        rate_limit=rate_limit,
        __expects_json=_is_json_response(response),
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
    rate_limit: Optional[str] = None,
    **kwargs: Any,
) -> BaseOpenAPISchema:
    """Load Open API schema from an AioHTTP app.

    :param str schema_path: An in-app relative URL to the schema.
    :param app: An AioHTTP app instance.
    """
    from ...extra._aiohttp import run_server

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
        rate_limit=rate_limit,
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
    rate_limit: Optional[str] = None,
    **kwargs: Any,
) -> BaseOpenAPISchema:
    """Load Open API schema from an ASGI app.

    :param str schema_path: An in-app relative URL to the schema.
    :param app: An ASGI app instance.
    """
    require_relative_url(schema_path)
    setup_headers(kwargs)
    client = ASGIClient(app)
    response = load_schema_from_url(lambda: client.get(schema_path, **kwargs))
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
        rate_limit=rate_limit,
        __expects_json=_is_json_response(response),
    )
