from copy import deepcopy
from typing import Any, Dict

from ...utils import traverse_schema


def to_json_schema(schema: Dict[str, Any], nullable_name: str) -> Dict[str, Any]:
    """Convert Open API parameters to JSON Schema.

    NOTE. This function is applied to all keywords (including nested) during a schema resolving, thus it is not recursive.
    See a recursive version below.
    """
    schema = deepcopy(schema)
    if schema.get(nullable_name) is True:
        del schema[nullable_name]
        schema = {"anyOf": [schema, {"type": "null"}]}
    if schema.get("type") == "file":
        schema["type"] = "string"
        schema["format"] = "binary"
    return schema


def to_json_schema_recursive(schema: Dict[str, Any], nullable_name: str) -> Dict[str, Any]:
    return traverse_schema(schema, to_json_schema, nullable_name)
