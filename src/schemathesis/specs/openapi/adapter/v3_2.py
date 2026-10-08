from functools import partial

from schemathesis.specs.openapi.adapter import responses
from schemathesis.specs.openapi.adapter.protocol import ExtractRawResponseSchema, ExtractSchemaForMediaType
from schemathesis.specs.openapi.adapter.v3_1 import (
    build_path_parameter,
    example_keyword,
    examples_container_keyword,
    extract_header_schema,
    extract_parameter_schema,
    extract_security_definitions,
    extract_security_parameters,
    get_base_path,
    get_base_url,
    get_default_media_types,
    get_default_response_media_type,
    get_parameter_serializer,
    get_request_payload_content_types,
    get_response_content_types,
    header_required_keyword,
    iter_parameters,
    iter_response_examples,
    jsonschema_validator_cls,
    links_keyword,
    nullable_keyword,
    prepare_multipart,
    ref_siblings,
    resolve_response_media_type,
    validate_schema,
)

# Open API 3.2 has `schema` of a sequential media type cover the whole stream, and `itemSchema` each item
extract_raw_response_schema: ExtractRawResponseSchema = partial(
    responses.extract_raw_response_schema_v3, schema_covers_stream=True
)
extract_schema_for_media_type: ExtractSchemaForMediaType = partial(
    responses.extract_schema_for_media_type_v3,
    schema_covers_stream=True,
    upgrade_legacy_exclusive_bounds=True,
    merge_ref_siblings=True,
)
extract_stream_schema: ExtractSchemaForMediaType = partial(
    responses.extract_stream_schema_v3,
    schema_covers_stream=True,
    upgrade_legacy_exclusive_bounds=True,
    merge_ref_siblings=True,
)

__all__ = [
    "build_path_parameter",
    "example_keyword",
    "examples_container_keyword",
    "extract_header_schema",
    "extract_parameter_schema",
    "extract_raw_response_schema",
    "extract_schema_for_media_type",
    "extract_security_definitions",
    "extract_security_parameters",
    "extract_stream_schema",
    "get_base_path",
    "get_base_url",
    "get_default_media_types",
    "get_default_response_media_type",
    "get_parameter_serializer",
    "get_request_payload_content_types",
    "get_response_content_types",
    "header_required_keyword",
    "iter_parameters",
    "iter_response_examples",
    "jsonschema_validator_cls",
    "links_keyword",
    "nullable_keyword",
    "prepare_multipart",
    "ref_siblings",
    "resolve_response_media_type",
    "validate_schema",
]
