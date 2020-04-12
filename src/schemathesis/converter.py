from copy import deepcopy
from typing import Any, Dict


def to_json_schema(schema: Dict[str, Any], nullable_name: str) -> Dict[str, Any]:
    """Convert Open API parameters to JSON Schema.

    NOTE. This function is applied to all keywords (including nested) during schema resolving, thus it is not recursive
    """
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
    return schema
