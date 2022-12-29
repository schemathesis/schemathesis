from hypothesis_jsonschema._canonicalise import canonicalish

from .types import Schema


def can_negate(schema: Schema) -> bool:
    return canonicalish(schema) != {}
