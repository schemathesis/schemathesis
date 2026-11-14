from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import (
    TYPE_CHECKING,
    Any,
    Protocol,
    Union,
)

import jsonschema_rs

from schemathesis.core.jsonschema.resolver import Resolver

if TYPE_CHECKING:
    from schemathesis.core.adapter import OperationParameter
    from schemathesis.core.jsonschema.bundler import Bundle, BundleCache, Bundler
    from schemathesis.core.jsonschema.types import JsonSchema
    from schemathesis.core.parameters import SkippedParameter
    from schemathesis.core.transport import Response
    from schemathesis.schemas import APIOperation
    from schemathesis.specs.openapi.adapter.responses import ResponseSchemaCache
    from schemathesis.specs.openapi.adapter.security import SecurityParameter

IterResponseExamples = Callable[[Mapping[str, Any], str], Iterator[tuple[str, object]]]
ExtractRawResponseSchema = Callable[[Mapping[str, Any]], tuple[str | None, Union["JsonSchema", None]]]
ExtractHeaderSchema = Callable[[Mapping[str, Any], "Resolver", str, str], "Bundle"]
ResolveResponseMediaType = Callable[[Mapping[str, Any], str | None], str | None]
ExtractSchemaForMediaType = Callable[
    [Mapping[str, Any], str | None, "Resolver", str, str, "ResponseSchemaCache"], Union["Bundle", None]
]
ExtractParameterSchema = Callable[[Mapping[str, Any]], "JsonSchema"]
ExtractSecurityParameters = Callable[
    [Mapping[str, Any], Mapping[str, Any], "Resolver"],
    Iterator["SecurityParameter"],
]
PrepareMultipart = Callable[
    ["APIOperation", dict[str, Any], str, dict[str, str] | None],
    tuple[list[tuple[str, Any]] | None, dict[str, Any] | None],
]
GetResponseContentTypes = Callable[["APIOperation", "Response"], list[str]]
GetRequestPayloadContentTypes = Callable[["APIOperation"], list[str]]
GetDefaultMediaTypes = Callable[[Mapping[str, Any]], list[str]]
GetBasePath = Callable[[Mapping[str, Any], "Mapping[str, str] | None"], str]
GetBaseUrl = Callable[[Mapping[str, Any]], "str | None"]
ValidateSchema = Callable[[Mapping[str, Any]], None]
GetParameterSerializer = Callable[[list[dict[str, Any]]], Callable | None]
IterParameters = Callable[
    [
        Mapping[str, Any],
        Sequence[Mapping[str, Any]],
        list[str],
        "Resolver",
        "ParameterAdapter",
        "Bundler",
        "BundleCache",
        list["SkippedParameter"],
    ],
    Iterable["OperationParameter"],
]
BuildPathParameter = Callable[[Mapping[str, Any]], "OperationParameter"]
ExtractSecurityDefinitions = Callable[[Mapping[str, Any], "Resolver"], Mapping[str, Mapping[str, Any]]]


class SchemaVocabulary(Protocol):
    """JSON Schema dialect of one specification version: how nullability is spelled and which validator matches."""

    # `x-nullable` in Open API 2.0, `nullable` in 3.x
    nullable_keyword: str

    # Read-only, so an adapter may pin one draft's validator class.
    @property
    def jsonschema_validator_cls(self) -> type[jsonschema_rs.Validator]: ...


def is_draft_2020_12(vocabulary: SchemaVocabulary) -> bool:
    """Whether schemas follow JSON Schema Draft 2020-12, as in Open API 3.1 and later."""
    return vocabulary.jsonschema_validator_cls is jsonschema_rs.Draft202012Validator


class ParameterAdapter(SchemaVocabulary, Protocol):
    """Parameter definitions: enumeration, schema extraction, and serialization."""

    # True in Open API 3.1+, which allows keywords next to `$ref`
    ref_siblings: bool
    example_keyword: str
    examples_container_keyword: str

    extract_parameter_schema: ExtractParameterSchema
    iter_parameters: IterParameters
    build_path_parameter: BuildPathParameter
    get_parameter_serializer: GetParameterSerializer


class ResponseAdapter(SchemaVocabulary, Protocol):
    """Response and header definitions: schema extraction, media-type resolution, examples."""

    # `x-required` in Open API 2.0, which has no `required` for headers
    header_required_keyword: str
    links_keyword: str

    extract_raw_response_schema: ExtractRawResponseSchema
    resolve_response_media_type: ResolveResponseMediaType
    extract_schema_for_media_type: ExtractSchemaForMediaType
    # Schema of a sequential media type that covers the whole stream, read as an array
    extract_stream_schema: ExtractSchemaForMediaType
    extract_header_schema: ExtractHeaderSchema
    iter_response_examples: IterResponseExamples
    get_response_content_types: GetResponseContentTypes


class SecurityAdapter(Protocol):
    """Security scheme definitions and the parameters they contribute to an operation."""

    extract_security_parameters: ExtractSecurityParameters
    # Security scheme definitions with references resolved
    extract_security_definitions: ExtractSecurityDefinitions


class SpecificationAdapter(ParameterAdapter, ResponseAdapter, SecurityAdapter, SchemaVocabulary, Protocol):
    """Full contract for one API specification format; composed of role protocols so callers can depend on a slice."""

    prepare_multipart: PrepareMultipart
    get_request_payload_content_types: GetRequestPayloadContentTypes
    get_default_media_types: GetDefaultMediaTypes
    get_base_path: GetBasePath
    get_base_url: GetBaseUrl
    validate_schema: ValidateSchema
