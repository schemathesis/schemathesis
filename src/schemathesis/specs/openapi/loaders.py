from __future__ import annotations

import io
import json
import pathlib
import re
from typing import IO, TYPE_CHECKING, Any, Callable, cast
from urllib.parse import urljoin

from ... import experimental, fixups
from ...code_samples import CodeSampleStyle
from ...constants import NOT_SET, WAIT_FOR_SCHEMA_INTERVAL
from ...exceptions import SchemaError, SchemaErrorType
from ...filters import filter_set_from_components
from ...generation import (
    DEFAULT_DATA_GENERATION_METHODS,
    DataGenerationMethod,
    DataGenerationMethodInput,
    GenerationConfig,
)
from ...hooks import HookContext, dispatch
from ...internal.deprecation import warn_filtration_arguments
from ...internal.output import OutputConfig
from ...internal.validation import require_relative_url
from ...loaders import load_schema_from_url, load_yaml
from ...throttling import build_limiter
from ...transports.content_types import is_json_media_type, is_yaml_media_type
from ...transports.headers import setup_default_headers
from ...types import Filter, NotSet, PathLike, Specification
from . import definitions, validation

if TYPE_CHECKING:
    import jsonschema
    from pyrate_limiter import Limiter

    from ...lazy import LazySchema
    from ...transports.responses import GenericResponse
    from .schemas import BaseOpenAPISchema


def _is_json_response(response: GenericResponse) -> bool:
    """Guess if the response contains JSON."""
    content_type = response.headers.get("Content-Type")
    if content_type is not None:
        return is_json_media_type(content_type)
    return False


def _has_suffix(path: PathLike, suffix: str) -> bool:
    if isinstance(path, str):
        return path.endswith(suffix)
    return path.suffix == suffix


def _is_json_path(path: PathLike) -> bool:
    return _has_suffix(path, ".json")


def _is_yaml_response(response: GenericResponse) -> bool:
    """Guess if the response contains YAML."""
    content_type = response.headers.get("Content-Type")
    if content_type is not None:
        return is_yaml_media_type(content_type)
    return False


def _is_yaml_path(path: PathLike) -> bool:
    return _has_suffix(path, ".yaml") or _has_suffix(path, ".yml")


def from_path(
    path: PathLike,
    *,
    app: Any = None,
    base_url: str | None = None,
    method: Filter | None = None,
    endpoint: Filter | None = None,
    tag: Filter | None = None,
    operation_id: Filter | None = None,
    skip_deprecated_operations: bool | None = None,
    validate_schema: bool = False,
    force_schema_version: str | None = None,
    data_generation_methods: DataGenerationMethodInput = DEFAULT_DATA_GENERATION_METHODS,
    generation_config: GenerationConfig | None = None,
    output_config: OutputConfig | None = None,
    code_sample_style: str = CodeSampleStyle.default().name,
    rate_limit: str | None = None,
    encoding: str = "utf8",
    sanitize_output: bool = True,
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
            generation_config=generation_config,
            output_config=output_config,
            code_sample_style=code_sample_style,
            location=pathlib.Path(path).absolute().as_uri(),
            rate_limit=rate_limit,
            sanitize_output=sanitize_output,
            __expects_json=_is_json_path(path),
            __expects_yaml=_is_yaml_path(path),
        )


def from_uri(
    uri: str,
    *,
    app: Any = None,
    base_url: str | None = None,
    port: int | None = None,
    method: Filter | None = None,
    endpoint: Filter | None = None,
    tag: Filter | None = None,
    operation_id: Filter | None = None,
    skip_deprecated_operations: bool | None = None,
    validate_schema: bool = False,
    force_schema_version: str | None = None,
    data_generation_methods: DataGenerationMethodInput = DEFAULT_DATA_GENERATION_METHODS,
    generation_config: GenerationConfig | None = None,
    output_config: OutputConfig | None = None,
    code_sample_style: str = CodeSampleStyle.default().name,
    wait_for_schema: float | None = None,
    rate_limit: str | None = None,
    sanitize_output: bool = True,
    **kwargs: Any,
) -> BaseOpenAPISchema:
    """Load Open API schema from the network.

    :param str uri: Schema URL.
    """
    import backoff
    import requests

    setup_default_headers(kwargs)
    if port:
        from yarl import URL

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
        generation_config=generation_config,
        output_config=output_config,
        code_sample_style=code_sample_style,
        location=uri,
        rate_limit=rate_limit,
        sanitize_output=sanitize_output,
        __expects_json=_is_json_response(response),
        __expects_yaml=_is_yaml_response(response),
    )


SCHEMA_INVALID_ERROR = "The provided API schema does not appear to be a valid OpenAPI schema"
SCHEMA_LOADING_ERROR = "Received unsupported content while expecting a JSON or YAML payload for Open API"
SCHEMA_SYNTAX_ERROR = "API schema does not appear syntactically valid"


def _load_yaml(data: str, include_details_on_error: bool = False) -> dict[str, Any]:
    import yaml

    try:
        return load_yaml(data)
    except yaml.YAMLError as exc:
        if include_details_on_error:
            type_ = SchemaErrorType.SYNTAX_ERROR
            message = SCHEMA_SYNTAX_ERROR
            extras = [entry for entry in str(exc).splitlines() if entry]
        else:
            type_ = SchemaErrorType.UNEXPECTED_CONTENT_TYPE
            message = SCHEMA_LOADING_ERROR
            extras = []
        raise SchemaError(type_, message, extras=extras) from exc


def from_file(
    file: IO[str] | str,
    *,
    app: Any = None,
    base_url: str | None = None,
    method: Filter | None = None,
    endpoint: Filter | None = None,
    tag: Filter | None = None,
    operation_id: Filter | None = None,
    skip_deprecated_operations: bool | None = None,
    validate_schema: bool = False,
    force_schema_version: str | None = None,
    data_generation_methods: DataGenerationMethodInput = DEFAULT_DATA_GENERATION_METHODS,
    generation_config: GenerationConfig | None = None,
    output_config: OutputConfig | None = None,
    code_sample_style: str = CodeSampleStyle.default().name,
    location: str | None = None,
    rate_limit: str | None = None,
    sanitize_output: bool = True,
    __expects_json: bool = False,
    __expects_yaml: bool = False,
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
        except json.JSONDecodeError as exc:
            # Fallback to a slower YAML loader. This way we'll still load schemas from responses with
            # invalid `Content-Type` headers or YAML files that have the `.json` extension.
            # This is a rare case, and it will be slower but trying JSON first improves a more common use case
            try:
                raw = _load_yaml(data)
            except SchemaError:
                raise SchemaError(
                    SchemaErrorType.SYNTAX_ERROR,
                    SCHEMA_SYNTAX_ERROR,
                    extras=[entry for entry in str(exc).splitlines() if entry],
                ) from exc
    else:
        raw = _load_yaml(data, include_details_on_error=__expects_yaml)
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
        generation_config=generation_config,
        output_config=output_config,
        code_sample_style=code_sample_style,
        location=location,
        rate_limit=rate_limit,
        sanitize_output=sanitize_output,
    )


def _is_fast_api(app: Any) -> bool:
    for cls in app.__class__.__mro__:
        if f"{cls.__module__}.{cls.__qualname__}" == "fastapi.applications.FastAPI":
            return True
    return False


def from_dict(
    raw_schema: dict[str, Any],
    *,
    app: Any = None,
    base_url: str | None = None,
    method: Filter | None = None,
    endpoint: Filter | None = None,
    tag: Filter | None = None,
    operation_id: Filter | None = None,
    skip_deprecated_operations: bool | None = None,
    validate_schema: bool = False,
    force_schema_version: str | None = None,
    data_generation_methods: DataGenerationMethodInput = DEFAULT_DATA_GENERATION_METHODS,
    generation_config: GenerationConfig | None = None,
    output_config: OutputConfig | None = None,
    code_sample_style: str = CodeSampleStyle.default().name,
    location: str | None = None,
    rate_limit: str | None = None,
    sanitize_output: bool = True,
) -> BaseOpenAPISchema:
    """Load Open API schema from a Python dictionary.

    :param dict raw_schema: A schema to load.
    """
    from ... import transports
    from .schemas import OpenApi30, SwaggerV20

    if not isinstance(raw_schema, dict):
        raise SchemaError(SchemaErrorType.OPEN_API_INVALID_SCHEMA, SCHEMA_INVALID_ERROR)
    _code_sample_style = CodeSampleStyle.from_str(code_sample_style)
    hook_context = HookContext()
    is_openapi_31 = raw_schema.get("openapi", "").startswith("3.1")
    is_fast_api_fixup_installed = fixups.is_installed("fast_api")
    if is_fast_api_fixup_installed and is_openapi_31:
        fixups.fast_api.uninstall()
    elif _is_fast_api(app):
        fixups.fast_api.adjust_schema(raw_schema)
    dispatch("before_load_schema", hook_context, raw_schema)
    rate_limiter: Limiter | None = None
    if rate_limit is not None:
        rate_limiter = build_limiter(rate_limit)

    for name in ("method", "endpoint", "tag", "operation_id", "skip_deprecated_operations"):
        value = locals()[name]
        if value is not None:
            warn_filtration_arguments(name)
    filter_set = filter_set_from_components(
        include=True,
        method=method,
        endpoint=endpoint,
        tag=tag,
        operation_id=operation_id,
        skip_deprecated_operations=skip_deprecated_operations,
    )

    def init_openapi_2() -> SwaggerV20:
        _maybe_validate_schema(raw_schema, definitions.SWAGGER_20_VALIDATOR, validate_schema)
        instance = SwaggerV20(
            raw_schema,
            specification=Specification.OPENAPI,
            app=app,
            base_url=base_url,
            filter_set=filter_set,
            validate_schema=validate_schema,
            data_generation_methods=DataGenerationMethod.ensure_list(data_generation_methods),
            generation_config=generation_config or GenerationConfig(),
            output_config=output_config or OutputConfig(),
            code_sample_style=_code_sample_style,
            location=location,
            rate_limiter=rate_limiter,
            sanitize_output=sanitize_output,
            transport=transports.get(app),
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
            if is_openapi_31:
                raise SchemaError(
                    SchemaErrorType.OPEN_API_EXPERIMENTAL_VERSION,
                    f"The provided schema uses Open API {version}, which is currently not fully supported.",
                )
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
            specification=Specification.OPENAPI,
            app=app,
            base_url=base_url,
            filter_set=filter_set,
            validate_schema=validate_schema,
            data_generation_methods=DataGenerationMethod.ensure_list(data_generation_methods),
            generation_config=generation_config or GenerationConfig(),
            output_config=output_config or OutputConfig(),
            code_sample_style=_code_sample_style,
            location=location,
            rate_limiter=rate_limiter,
            sanitize_output=sanitize_output,
            transport=transports.get(app),
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


def _format_status_codes(status_codes: list[tuple[int, list[str | int]]]) -> str:
    buffer = io.StringIO()
    for status_code, path in status_codes:
        buffer.write(f" - {status_code} at schema['paths']")
        for chunk in path:
            buffer.write(f"[{repr(chunk)}]")
        buffer.write("['responses']\n")
    return buffer.getvalue().rstrip()


def _maybe_validate_schema(
    instance: dict[str, Any], validator: jsonschema.validators.Draft4Validator, validate_schema: bool
) -> None:
    from jsonschema import ValidationError

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
                SCHEMA_INVALID_ERROR,
                extras=[entry for entry in str(exc).splitlines() if entry],
            ) from exc


def from_pytest_fixture(
    fixture_name: str,
    *,
    app: Any = NOT_SET,
    base_url: str | None | NotSet = NOT_SET,
    method: Filter | None = NOT_SET,
    endpoint: Filter | None = NOT_SET,
    tag: Filter | None = NOT_SET,
    operation_id: Filter | None = NOT_SET,
    skip_deprecated_operations: bool | None = None,
    validate_schema: bool = False,
    data_generation_methods: DataGenerationMethodInput | NotSet = NOT_SET,
    generation_config: GenerationConfig | NotSet = NOT_SET,
    output_config: OutputConfig | NotSet = NOT_SET,
    code_sample_style: str = CodeSampleStyle.default().name,
    rate_limit: str | None = None,
    sanitize_output: bool = True,
) -> LazySchema:
    """Load schema from a ``pytest`` fixture.

    It is useful if you don't want to make network requests during module loading. With this loader you can defer it
    to a fixture.

    Note, the fixture should return a ``BaseSchema`` instance loaded with another loader.

    :param str fixture_name: The name of a fixture to load.
    """
    from ...lazy import LazySchema

    _code_sample_style = CodeSampleStyle.from_str(code_sample_style)
    _data_generation_methods: DataGenerationMethodInput | NotSet
    if data_generation_methods is not NOT_SET:
        data_generation_methods = cast(DataGenerationMethodInput, data_generation_methods)
        _data_generation_methods = DataGenerationMethod.ensure_list(data_generation_methods)
    else:
        _data_generation_methods = data_generation_methods
    rate_limiter: Limiter | None = None
    if rate_limit is not None:
        rate_limiter = build_limiter(rate_limit)
    for name in ("method", "endpoint", "tag", "operation_id", "skip_deprecated_operations"):
        value = locals()[name]
        if value is not None:
            warn_filtration_arguments(name)
    filter_set = filter_set_from_components(
        include=True,
        method=method,
        endpoint=endpoint,
        tag=tag,
        operation_id=operation_id,
        skip_deprecated_operations=skip_deprecated_operations,
    )
    return LazySchema(
        fixture_name,
        app=app,
        base_url=base_url,
        filter_set=filter_set,
        validate_schema=validate_schema,
        data_generation_methods=_data_generation_methods,
        generation_config=generation_config,
        output_config=output_config,
        code_sample_style=_code_sample_style,
        rate_limiter=rate_limiter,
        sanitize_output=sanitize_output,
    )


def from_wsgi(
    schema_path: str,
    app: Any,
    *,
    base_url: str | None = None,
    method: Filter | None = None,
    endpoint: Filter | None = None,
    tag: Filter | None = None,
    operation_id: Filter | None = None,
    skip_deprecated_operations: bool | None = None,
    validate_schema: bool = False,
    force_schema_version: str | None = None,
    data_generation_methods: DataGenerationMethodInput = DEFAULT_DATA_GENERATION_METHODS,
    generation_config: GenerationConfig | None = None,
    output_config: OutputConfig | None = None,
    code_sample_style: str = CodeSampleStyle.default().name,
    rate_limit: str | None = None,
    sanitize_output: bool = True,
    **kwargs: Any,
) -> BaseOpenAPISchema:
    """Load Open API schema from a WSGI app.

    :param str schema_path: An in-app relative URL to the schema.
    :param app: A WSGI app instance.
    """
    from werkzeug.test import Client

    from ...transports.responses import WSGIResponse

    require_relative_url(schema_path)
    setup_default_headers(kwargs)
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
        generation_config=generation_config,
        output_config=output_config,
        code_sample_style=code_sample_style,
        location=schema_path,
        rate_limit=rate_limit,
        sanitize_output=sanitize_output,
        __expects_json=_is_json_response(response),
    )


def get_loader_for_app(app: Any) -> Callable:
    from starlette.applications import Starlette

    if isinstance(app, Starlette):
        return from_asgi
    if app.__class__.__module__.startswith("aiohttp."):
        return from_aiohttp
    return from_wsgi


def from_aiohttp(
    schema_path: str,
    app: Any,
    *,
    base_url: str | None = None,
    method: Filter | None = None,
    endpoint: Filter | None = None,
    tag: Filter | None = None,
    operation_id: Filter | None = None,
    skip_deprecated_operations: bool | None = None,
    validate_schema: bool = False,
    force_schema_version: str | None = None,
    data_generation_methods: DataGenerationMethodInput = DEFAULT_DATA_GENERATION_METHODS,
    generation_config: GenerationConfig | None = None,
    output_config: OutputConfig | None = None,
    code_sample_style: str = CodeSampleStyle.default().name,
    rate_limit: str | None = None,
    sanitize_output: bool = True,
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
        generation_config=generation_config,
        output_config=output_config,
        code_sample_style=code_sample_style,
        rate_limit=rate_limit,
        sanitize_output=sanitize_output,
        **kwargs,
    )


def from_asgi(
    schema_path: str,
    app: Any,
    *,
    base_url: str | None = None,
    method: Filter | None = None,
    endpoint: Filter | None = None,
    tag: Filter | None = None,
    operation_id: Filter | None = None,
    skip_deprecated_operations: bool | None = None,
    validate_schema: bool = False,
    force_schema_version: str | None = None,
    data_generation_methods: DataGenerationMethodInput = DEFAULT_DATA_GENERATION_METHODS,
    generation_config: GenerationConfig | None = None,
    output_config: OutputConfig | None = None,
    code_sample_style: str = CodeSampleStyle.default().name,
    rate_limit: str | None = None,
    sanitize_output: bool = True,
    **kwargs: Any,
) -> BaseOpenAPISchema:
    """Load Open API schema from an ASGI app.

    :param str schema_path: An in-app relative URL to the schema.
    :param app: An ASGI app instance.
    """
    from starlette_testclient import TestClient as ASGIClient

    require_relative_url(schema_path)
    setup_default_headers(kwargs)
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
        generation_config=generation_config,
        output_config=output_config,
        code_sample_style=code_sample_style,
        location=schema_path,
        rate_limit=rate_limit,
        sanitize_output=sanitize_output,
        __expects_json=_is_json_response(response),
    )
