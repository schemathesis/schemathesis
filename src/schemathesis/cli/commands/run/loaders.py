"""Automatic schema loading.

This module handles the automatic detection and loading of API schemas,
supporting both GraphQL and OpenAPI specifications.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from schemathesis import graphql, openapi
from schemathesis.core import NOT_SET, NotSet
from schemathesis.core.errors import LoaderError, LoaderErrorKind
from schemathesis.core.fs import file_exists
from schemathesis.core.output import OutputConfig
from schemathesis.generation import GenerationConfig

if TYPE_CHECKING:
    from schemathesis.engine.config import NetworkConfig
    from schemathesis.schemas import BaseSchema

Loader = Callable[["AutodetectConfig"], "BaseSchema"]


@dataclass
class AutodetectConfig:
    location: str
    network: NetworkConfig
    wait_for_schema: float | None
    base_url: str | None | NotSet = NOT_SET
    rate_limit: str | None | NotSet = NOT_SET
    generation: GenerationConfig | NotSet = NOT_SET
    output: OutputConfig | NotSet = NOT_SET


def load_schema(config: AutodetectConfig) -> BaseSchema:
    """Load API schema automatically based on the provided configuration."""
    if is_probably_graphql(config.location):
        # Try GraphQL first, then fallback to Open API
        return _try_load_schema(config, graphql, openapi)
    # Try Open API first, then fallback to GraphQL
    return _try_load_schema(config, openapi, graphql)


def should_try_more(exc: LoaderError) -> bool:
    """Determine if alternative schema loading should be attempted."""
    import requests
    from yaml.reader import ReaderError

    if isinstance(exc.__cause__, ReaderError) and "characters are not allowed" in str(exc.__cause__):
        return False

    # We should not try other loaders for cases when we can't even establish connection
    return not isinstance(exc.__cause__, requests.exceptions.ConnectionError) and exc.kind not in (
        LoaderErrorKind.OPEN_API_INVALID_SCHEMA,
        LoaderErrorKind.OPEN_API_UNSPECIFIED_VERSION,
        LoaderErrorKind.OPEN_API_UNSUPPORTED_VERSION,
    )


def detect_loader(schema_or_location: str | dict[str, Any], module: Any) -> Callable:
    """Detect API schema loader."""
    if isinstance(schema_or_location, str):
        if file_exists(schema_or_location):
            return module.from_path  # type: ignore
        return module.from_url  # type: ignore
    raise NotImplementedError


def _try_load_schema(config: AutodetectConfig, first_module: Any, second_module: Any) -> BaseSchema:
    """Try to load schema with fallback option."""
    from urllib3.exceptions import InsecureRequestWarning

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", InsecureRequestWarning)
        try:
            return _load_schema(config, first_module)
        except LoaderError as exc:
            if should_try_more(exc):
                try:
                    return _load_schema(config, second_module)
                except Exception as second_exc:
                    if is_specific_exception(second_exc):
                        raise second_exc
            # Re-raise the original error
            raise exc


def _load_schema(config: AutodetectConfig, module: Any) -> BaseSchema:
    """Unified schema loader for both GraphQL and OpenAPI."""
    loader = detect_loader(config.location, module)

    kwargs: dict = {}
    if loader is module.from_url:
        if config.wait_for_schema is not None:
            kwargs["wait_for_schema"] = config.wait_for_schema
        kwargs["verify"] = config.network.tls_verify
        if config.network.cert:
            kwargs["cert"] = config.network.cert
        if config.network.auth:
            kwargs["auth"] = config.network.auth

    return loader(config.location, **kwargs).configure(
        base_url=config.base_url,
        rate_limit=config.rate_limit,
        output=config.output,
        generation=config.generation,
    )


def is_specific_exception(exc: Exception) -> bool:
    """Determine if alternative schema loading should be attempted."""
    return (
        isinstance(exc, LoaderError)
        and exc.kind == LoaderErrorKind.GRAPHQL_INVALID_SCHEMA
        # In some cases it is not clear that the schema is even supposed to be GraphQL, e.g. an empty input
        and "Syntax Error: Unexpected <EOF>." not in exc.extras
    )


def is_probably_graphql(schema_or_location: str | dict[str, Any]) -> bool:
    """Detect whether it is likely that the given location is a GraphQL endpoint."""
    if isinstance(schema_or_location, str):
        return schema_or_location.endswith(("/graphql", "/graphql/", ".graphql", ".gql"))
    return "__schema" in schema_or_location or (
        "data" in schema_or_location and "__schema" in schema_or_location["data"]
    )
