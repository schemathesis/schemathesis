from jsonschema import Draft202012Validator

from schemathesis.specs.openapi.adapter import (
    base_paths,
    content_types,
    formdata,
    parameter_serializers,
    parameters,
    responses,
    security,
    validators,
)
from schemathesis.specs.openapi.adapter.protocol import (
    BuildPathParameter,
    ExtractHeaderSchema,
    ExtractParameterSchema,
    ExtractRawResponseSchema,
    ExtractResponseSchema,
    ExtractSchemaForMediaType,
    ExtractSecurityDefinitions,
    ExtractSecurityParameters,
    GetBasePath,
    GetDefaultMediaTypes,
    GetDefaultResponseMediaType,
    GetParameterSerializer,
    GetRequestPayloadContentTypes,
    GetResponseContentTypes,
    IterParameters,
    IterResponseExamples,
    PrepareMultipart,
    PrepareResponseMediaTypeSchema,
    ResolveResponseMediaType,
    ValidateSchema,
)

nullable_keyword = "nullable"
header_required_keyword = "required"
links_keyword = "links"
example_keyword = "example"
examples_container_keyword = "examples"

extract_parameter_schema: ExtractParameterSchema = parameters.extract_parameter_schema_v3
extract_raw_response_schema: ExtractRawResponseSchema = responses.extract_raw_response_schema_v3
extract_response_schema: ExtractResponseSchema = responses.extract_response_schema_v3
prepare_response_media_type_schema: PrepareResponseMediaTypeSchema = responses.prepare_response_media_type_schema
get_default_response_media_type: GetDefaultResponseMediaType = responses.get_default_response_media_type_v3
resolve_response_media_type: ResolveResponseMediaType = responses.resolve_response_media_type_v3
extract_schema_for_media_type: ExtractSchemaForMediaType = responses.extract_schema_for_media_type_v3
extract_header_schema: ExtractHeaderSchema = responses.extract_header_schema_v3
iter_parameters: IterParameters = parameters.iter_parameters_v3
build_path_parameter: BuildPathParameter = parameters.build_path_parameter_v3_1
iter_response_examples: IterResponseExamples = responses.iter_response_examples_v3
extract_security_parameters: ExtractSecurityParameters = security.extract_security_parameters_v3
prepare_multipart: PrepareMultipart = formdata.prepare_multipart_v3
get_response_content_types: GetResponseContentTypes = content_types.response_content_types_v3
get_request_payload_content_types: GetRequestPayloadContentTypes = content_types.request_content_types_v3
get_default_media_types: GetDefaultMediaTypes = content_types.default_media_types_v3
get_base_path: GetBasePath = base_paths.base_path_v3
validate_schema: ValidateSchema = validators.validate_v3
get_parameter_serializer: GetParameterSerializer = parameter_serializers.serializer_v3
extract_security_definitions: ExtractSecurityDefinitions = security.extract_security_definitions_v3

jsonschema_validator_cls = Draft202012Validator
