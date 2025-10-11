from jsonschema import Draft202012Validator

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

nullable_keyword = "nullable"
header_required_keyword = "required"
links_keyword = "links"
example_keyword = "example"
examples_container_keyword = "examples"

extract_parameter_schema: ExtractParameterSchema = parameters.extract_parameter_schema_v3
extract_raw_response_schema: ExtractRawResponseSchema = responses.extract_raw_response_schema_v3
extract_response_schema: ExtractResponseSchema = responses.extract_response_schema_v3
extract_header_schema: ExtractHeaderSchema = responses.extract_header_schema_v3
iter_parameters: IterParameters = parameters.iter_parameters_v3
build_path_parameter: BuildPathParameter = parameters.build_path_parameter_v3_1
iter_response_examples: IterResponseExamples = responses.iter_response_examples_v3
extract_security_parameters: ExtractSecurityParameters = security.extract_security_parameters_v3

jsonschema_validator_cls = Draft202012Validator
