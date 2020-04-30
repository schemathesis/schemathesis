from copy import deepcopy
from typing import Any, Dict, List, Union, overload


def to_json_schema(schema: Dict[str, Any], nullable_name: str) -> Dict[str, Any]:
    """Convert Open API parameters to JSON Schema.

    NOTE. This function is applied to all keywords (including nested) during schema resolving, thus it is not recursive.
    See a recursive version below.
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


Schema = Union[Dict[str, Any], List, str, float, int]


@overload
def to_json_schema_recursive(schema: Dict[str, Any], nullable_name: str) -> Dict[str, Any]:
    pass


@overload
def to_json_schema_recursive(schema: List, nullable_name: str) -> List:
    pass


@overload
def to_json_schema_recursive(schema: str, nullable_name: str) -> str:
    pass


@overload
def to_json_schema_recursive(schema: float, nullable_name: str) -> float:
    pass


def to_json_schema_recursive(schema: Schema, nullable_name: str) -> Schema:
    """Apply ``to_json_schema`` recursively.

    This version is needed for cases where the input schema was not resolved and ``to_json_schema`` wasn't applied
    recursively.
    """
    if isinstance(schema, dict):
        schema = to_json_schema(schema, nullable_name)
        for key, sub_item in schema.items():
            schema[key] = to_json_schema_recursive(sub_item, nullable_name)
    elif isinstance(schema, list):
        for idx, sub_item in enumerate(schema):
            schema[idx] = to_json_schema_recursive(sub_item, nullable_name)
    return schema
