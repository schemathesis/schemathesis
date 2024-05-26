from typing import Iterable

from .types import ObjectSchema


def iter_subschemas(schema: ObjectSchema) -> Iterable[ObjectSchema]:
    """Iterate over all subschemas in the given schema."""
    for key, value in schema.items():
        if key in ("additionalProperties", "not", "items") and isinstance(value, dict):
            yield value
        elif key in ("properties", "patternProperties"):
            for subschema in value.values():
                if isinstance(subschema, dict):
                    yield subschema
        elif key == "items" and isinstance(value, list):
            for subschema in value:
                if isinstance(subschema, dict):
                    yield subschema
        elif key in ("anyOf", "oneOf", "allOf"):
            for subschema in value:
                if isinstance(subschema, dict):
                    yield subschema
