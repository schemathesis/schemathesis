from typing import Any

JsonSchemaObject = dict[str, Any]
JsonSchema = JsonSchemaObject | bool

ANY_TYPE = ["null", "boolean", "number", "string", "array", "object"]
ALL_TYPES = ["null", "boolean", "integer", "number", "string", "array", "object"]


def get_type(schema: JsonSchema, *, _check_type: bool = False) -> list[str]:
    if isinstance(schema, bool):
        return ANY_TYPE
    ty = schema.get("type", ANY_TYPE)
    if isinstance(ty, str):
        if _check_type and ty not in ALL_TYPES:
            raise AssertionError(f"Unknown type: `{ty}`. Should be one of {', '.join(ALL_TYPES)}")
        return [ty]
    if ty is ANY_TYPE:
        return list(ty)
    return [t for t in ALL_TYPES if t in ty]


def _get_type(schema: JsonSchema) -> list[str]:
    # Special version to patch `hypothesis-jsonschema`
    return get_type(schema, _check_type=True)


def to_json_type_name(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, dict):
        return "object"
    if isinstance(v, list):
        return "array"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    return type(v).__name__
