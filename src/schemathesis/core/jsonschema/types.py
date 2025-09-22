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
