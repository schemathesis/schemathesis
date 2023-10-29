from typing import overload, Dict, Union, Any, List, Callable

JsonValue = Union[Dict[str, Any], List, str, float, int]


@overload
def traverse_schema(schema: Dict[str, Any], callback: Callable, *args: Any, **kwargs: Any) -> Dict[str, Any]:
    pass


@overload
def traverse_schema(schema: List, callback: Callable, *args: Any, **kwargs: Any) -> List:
    pass


@overload
def traverse_schema(schema: str, callback: Callable, *args: Any, **kwargs: Any) -> str:
    pass


@overload
def traverse_schema(schema: float, callback: Callable, *args: Any, **kwargs: Any) -> float:
    pass


def traverse_schema(schema: JsonValue, callback: Callable[..., Dict[str, Any]], *args: Any, **kwargs: Any) -> JsonValue:
    """Apply callback recursively to the given schema."""
    if isinstance(schema, dict):
        schema = callback(schema, *args, **kwargs)
        for key, sub_item in schema.items():
            schema[key] = traverse_schema(sub_item, callback, *args, **kwargs)
    elif isinstance(schema, list):
        schema = [traverse_schema(sub_item, callback, *args, **kwargs) for sub_item in schema]
    return schema
