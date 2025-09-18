from typing import Any


def get_type(schema: dict[str, Any]) -> list[str]:
    type_ = schema.get("type", ["null", "boolean", "integer", "number", "string", "array", "object"])
    if isinstance(type_, str):
        return [type_]
    return type_
