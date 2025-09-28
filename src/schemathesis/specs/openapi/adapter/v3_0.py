from jsonschema import Draft4Validator

from schemathesis.specs.openapi.adapter import parameters, responses
from schemathesis.specs.openapi.adapter.protocol import (
    BuildPathParameter,
    ExtractHeaderSchema,
    ExtractResponseSchema,
    IterParameters,
    IterResponseExamples,
)

nullable_keyword = "nullable"
header_required_keyword = "required"
links_keyword = "links"
iter_response_examples: IterResponseExamples = responses.iter_response_examples_v3
extract_response_schema: ExtractResponseSchema = responses.extract_response_schema_v3
extract_header_schema: ExtractHeaderSchema = responses.extract_header_schema_v3
iter_parameters: IterParameters = parameters.iter_parameters_v3
build_path_parameter: BuildPathParameter = parameters.build_path_parameter_v3
jsonschema_validator_cls = Draft4Validator
