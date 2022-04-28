from typing import List

from hypothesis_jsonschema._canonicalise import canonicalish

from .types import Schema


def get_type(schema: Schema) -> List[str]:
    type_ = schema.get("type", ["null", "boolean", "integer", "number", "string", "array", "object"])
    if isinstance(type_, str):
        return [type_]
    return type_


def can_negate(schema: Schema) -> bool:
    return canonicalish(schema) != {}
