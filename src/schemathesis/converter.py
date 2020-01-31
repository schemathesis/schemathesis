from copy import deepcopy
from typing import Any, Dict


def to_json_schema(schema: Dict[str, Any], nullable_name: str) -> Dict[str, Any]:
    """Convert Open API parameters to JSON Schema."""
    schema = deepcopy(schema)
    if schema.get(nullable_name) is True:
        del schema[nullable_name]
        if schema.get("in"):
            initial_type = {"type": schema["type"]}
            if schema.get("enum"):
                initial_type["enum"] = schema.pop("enum")
            schema["anyOf"] = [initial_type, {"type": "null"}]
            del schema["type"]
        else:
            schema = {"anyOf": [schema, {"type": "null"}]}
    if schema.get("type") == "file":
        schema["type"] = "string"
        schema["format"] = "binary"
    _handle_boundaries(schema, "maximum", "exclusiveMaximum")
    _handle_boundaries(schema, "minimum", "exclusiveMinimum")
    return schema


def _handle_boundaries(schema: Dict[str, Any], boundary_name: str, boundary_exclusive_name: str) -> None:
    # Replace exclusive field only if it is True
    # if it is non boolean, then leave as is
    exclusive_maximum = schema.get(boundary_exclusive_name)
    if exclusive_maximum is True:
        schema[boundary_exclusive_name] = schema.pop(boundary_name)
    elif exclusive_maximum is False:
        del schema[boundary_exclusive_name]
