"""Automatic schema loading.

This module handles the automatic detection and loading of API schemas,
supporting both GraphQL and OpenAPI specifications.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Union

from ..exceptions import SchemaError, SchemaErrorType
from ..generation import DataGenerationMethod, GenerationConfig
from ..internal.output import OutputConfig
from ..internal.validation import file_exists
from ..specs import graphql, openapi
from ..transports.auth import get_requests_auth

if TYPE_CHECKING:
    from ..runner.config import NetworkConfig
    from ..schemas import BaseSchema
    from ..specs.graphql.schemas import GraphQLSchema

SchemaLocation = Union[str, dict[str, Any]]
Loader = Callable[["LoaderConfig"], "BaseSchema"]


@dataclass
class LoaderConfig:
    """Container for API loader parameters.

    The main goal is to avoid too many parameters in function signatures.
    """

    schema_or_location: SchemaLocation
    base_url: str | None
    validate_schema: bool
    data_generation_methods: tuple[DataGenerationMethod, ...]
    force_schema_version: str | None
    network: NetworkConfig
    wait_for_schema: float | None
    rate_limit: str | None
    output_config: OutputConfig
    sanitize_output: bool
    generation_config: GenerationConfig


def load_schema(config: LoaderConfig) -> BaseSchema:
    """Load API schema automatically based on the provided configuration."""
    first: Callable[[LoaderConfig], BaseSchema]
    second: Callable[[LoaderConfig], BaseSchema]
    if is_probably_graphql(config.schema_or_location):
        # Try GraphQL first, then fallback to Open API
        first, second = (_load_graphql_schema, _load_openapi_schema)
    else:
        # Try Open API first, then fallback to GraphQL
        first, second = (_load_openapi_schema, _load_graphql_schema)
    return _try_load_schema(config, first, second)


def should_try_more(exc: SchemaError) -> bool:
    """Determine if alternative schema loading should be attempted."""
    import requests
    from yaml.reader import ReaderError

    if isinstance(exc.__cause__, ReaderError) and "characters are not allowed" in str(exc.__cause__):
        return False

    # We should not try other loaders for cases when we can't even establish connection
    return not isinstance(exc.__cause__, requests.exceptions.ConnectionError) and exc.type not in (
        SchemaErrorType.OPEN_API_INVALID_SCHEMA,
        SchemaErrorType.OPEN_API_UNSPECIFIED_VERSION,
        SchemaErrorType.OPEN_API_UNSUPPORTED_VERSION,
        SchemaErrorType.OPEN_API_EXPERIMENTAL_VERSION,
    )


def is_specific_exception(loader: Loader, exc: Exception) -> bool:
    """Determine if alternative schema loading should be attempted."""
    return (
        loader is _load_graphql_schema
        and isinstance(exc, SchemaError)
        and exc.type == SchemaErrorType.GRAPHQL_INVALID_SCHEMA
        # In some cases it is not clear that the schema is even supposed to be GraphQL, e.g. an empty input
        and "Syntax Error: Unexpected <EOF>." not in exc.extras
    )


def _load_graphql_schema(config: LoaderConfig) -> GraphQLSchema:
    loader = detect_loader(config.schema_or_location, is_openapi=False)
    kwargs = get_graphql_loader_kwargs(loader, config)
    return loader(config.schema_or_location, **kwargs)


def _load_openapi_schema(config: LoaderConfig) -> BaseSchema:
    loader = detect_loader(config.schema_or_location, is_openapi=True)
    kwargs = get_openapi_loader_kwargs(loader, config)
    return loader(config.schema_or_location, **kwargs)


def detect_loader(schema_or_location: str | dict[str, Any], is_openapi: bool) -> Callable:
    """Detect API schema loader."""
    if isinstance(schema_or_location, str):
        if file_exists(schema_or_location):
            # If there is an existing file with the given name,
            # then it is likely that the user wants to load API schema from there
            return openapi.loaders.from_path if is_openapi else graphql.loaders.from_path  # type: ignore
        # Default behavior
        return openapi.loaders.from_uri if is_openapi else graphql.loaders.from_url  # type: ignore
    return openapi.loaders.from_dict if is_openapi else graphql.loaders.from_dict  # type: ignore


def _try_load_schema(config: LoaderConfig, first: Loader, second: Loader) -> BaseSchema:
    """Attempt to load schema using primary and fallback loaders."""
    from urllib3.exceptions import InsecureRequestWarning

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", InsecureRequestWarning)
        try:
            return first(config)
        except SchemaError as exc:
            if config.force_schema_version is None and should_try_more(exc):
                try:
                    return second(config)
                except Exception as second_exc:
                    if is_specific_exception(second, second_exc):
                        raise second_exc
            # Re-raise the original error
            raise exc


def get_openapi_loader_kwargs(loader: Callable, config: LoaderConfig) -> dict[str, Any]:
    """Get appropriate keyword arguments for OpenAPI schema loader."""
    # These kwargs are shared by all loaders
    kwargs: dict[str, Any] = {
        "base_url": config.base_url,
        "validate_schema": config.validate_schema,
        "force_schema_version": config.force_schema_version,
        "data_generation_methods": config.data_generation_methods,
        "rate_limit": config.rate_limit,
        "output_config": config.output_config,
        "generation_config": config.generation_config,
        "sanitize_output": config.sanitize_output,
    }
    if loader not in (openapi.loaders.from_path, openapi.loaders.from_dict):
        kwargs["headers"] = config.network.headers
    if loader is openapi.loaders.from_uri:
        _add_requests_kwargs(kwargs, config)
    return kwargs


def get_graphql_loader_kwargs(loader: Callable, config: LoaderConfig) -> dict[str, Any]:
    """Get appropriate keyword arguments for GraphQL schema loader."""
    # These kwargs are shared by all loaders
    kwargs: dict[str, Any] = {
        "base_url": config.base_url,
        "data_generation_methods": config.data_generation_methods,
        "rate_limit": config.rate_limit,
    }
    if loader not in (graphql.loaders.from_path, graphql.loaders.from_dict):
        kwargs["headers"] = config.network.headers
    if loader is graphql.loaders.from_url:
        _add_requests_kwargs(kwargs, config)
    return kwargs


def _add_requests_kwargs(kwargs: dict[str, Any], config: LoaderConfig) -> None:
    kwargs["verify"] = config.network.tls_verify
    if config.network.cert is not None:
        kwargs["cert"] = config.network.cert
    if config.network.auth is not None:
        kwargs["auth"] = get_requests_auth(config.network.auth, config.network.auth_type)
    if config.wait_for_schema is not None:
        kwargs["wait_for_schema"] = config.wait_for_schema


def is_probably_graphql(schema_or_location: str | dict[str, Any]) -> bool:
    """Detect whether it is likely that the given location is a GraphQL endpoint."""
    if isinstance(schema_or_location, str):
        return schema_or_location.endswith(("/graphql", "/graphql/", ".graphql", ".gql"))
    return "__schema" in schema_or_location or (
        "data" in schema_or_location and "__schema" in schema_or_location["data"]
    )
