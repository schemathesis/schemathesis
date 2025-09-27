from jsonschema import Draft4Validator

from schemathesis.specs.openapi.adapter import responses
from schemathesis.specs.openapi.adapter.protocol import ExtractHeaderSchema, ExtractResponseSchema

nullable_keyword = "nullable"
header_required_keyword = "required"
extract_response_schema: ExtractResponseSchema = responses.extract_response_schema_v3
extract_header_schema: ExtractHeaderSchema = responses.extract_header_schema_v3
jsonschema_validator_cls = Draft4Validator
