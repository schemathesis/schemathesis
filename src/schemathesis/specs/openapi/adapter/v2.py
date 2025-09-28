from jsonschema import Draft4Validator

from schemathesis.specs.openapi.adapter import parameters, responses
from schemathesis.specs.openapi.adapter.protocol import (
    BuildPathParameter,
    ExtractHeaderSchema,
    ExtractResponseSchema,
    IterParameters,
    IterResponseExamples,
)

nullable_keyword = "x-nullable"
header_required_keyword = "x-required"
links_keyword = "x-links"
iter_response_examples: IterResponseExamples = responses.iter_response_examples_v2
extract_response_schema: ExtractResponseSchema = responses.extract_response_schema_v2
extract_header_schema: ExtractHeaderSchema = responses.extract_header_schema_v2
iter_parameters: IterParameters = parameters.iter_parameters_v2
build_path_parameter: BuildPathParameter = parameters.build_path_parameter_v2
jsonschema_validator_cls = Draft4Validator
