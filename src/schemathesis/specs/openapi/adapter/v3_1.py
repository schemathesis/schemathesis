from jsonschema import Draft202012Validator

from schemathesis.specs.openapi.adapter import responses
from schemathesis.specs.openapi.adapter.protocol import ExtractResponseSchema

nullable_keyword = "nullable"
extract_response_schema: ExtractResponseSchema = responses.extract_response_schema_v3
jsonschema_validator_cls = Draft202012Validator
