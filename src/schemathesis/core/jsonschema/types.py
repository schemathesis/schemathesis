from typing import Any, Union

JsonSchemaObject = dict[str, Any]
JsonSchema = Union[JsonSchemaObject, bool]

ANY_TYPE = ["null", "boolean", "integer", "number", "string", "array", "object"]


def get_type(schema: JsonSchema) -> list[str]:
    if isinstance(schema, bool):
        return ANY_TYPE
    type_ = schema.get("type", ANY_TYPE)
    if isinstance(type_, str):
        return [type_]
    return type_


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
