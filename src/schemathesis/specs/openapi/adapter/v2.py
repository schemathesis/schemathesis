from jsonschema import Draft4Validator

from schemathesis.specs.openapi.adapter import parameters, responses, security
from schemathesis.specs.openapi.adapter.protocol import (
    BuildPathParameter,
    ExtractHeaderSchema,
    ExtractParameterSchema,
    ExtractRawResponseSchema,
    ExtractResponseSchema,
    ExtractSecurityParameters,
    IterParameters,
    IterResponseExamples,
)

nullable_keyword = "x-nullable"
header_required_keyword = "x-required"
links_keyword = "x-links"
example_keyword = "x-example"
examples_container_keyword = "x-examples"

extract_parameter_schema: ExtractParameterSchema = parameters.extract_parameter_schema_v2
extract_raw_response_schema: ExtractRawResponseSchema = responses.extract_raw_response_schema_v2
extract_response_schema: ExtractResponseSchema = responses.extract_response_schema_v2
extract_header_schema: ExtractHeaderSchema = responses.extract_header_schema_v2
iter_parameters: IterParameters = parameters.iter_parameters_v2
build_path_parameter: BuildPathParameter = parameters.build_path_parameter_v2
iter_response_examples: IterResponseExamples = responses.iter_response_examples_v2
extract_security_parameters: ExtractSecurityParameters = security.extract_security_parameters_v2

jsonschema_validator_cls = Draft4Validator
