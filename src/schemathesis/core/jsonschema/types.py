from typing import Any

JsonSchemaObject = dict[str, Any]
JsonSchema = JsonSchemaObject | bool

JsonValue = dict[str, "JsonValue"] | list["JsonValue"] | str | int | float | bool | None

ANY_TYPE = ["null", "boolean", "number", "string", "array", "object"]
ALL_TYPES = ["null", "boolean", "integer", "number", "string", "array", "object"]


def get_type(schema: JsonSchema) -> list[str]:
    if isinstance(schema, bool):
        return ANY_TYPE
    ty = schema.get("type", ANY_TYPE)
    if isinstance(ty, str):
        return [ty]
    if ty is ANY_TYPE:
        return list(ty)
    return [t for t in ALL_TYPES if t in ty]


def to_json_type_name(v: object) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, dict):
        return "object"
    if isinstance(v, list):
        return "array"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string"
    return type(v).__name__
