import json
from functools import partial
from typing import Any, Callable, Dict, Optional, Union

from .parameters import Parameter

SERIALIZERS = {}


Serializer = Callable[[Any, Parameter], Dict[str, Any]]


def register(media_type: str) -> Callable[[Serializer], Serializer]:
    def wrapper(function: Serializer) -> Serializer:
        SERIALIZERS[media_type] = function
        return function

    return wrapper


@register("text/plain")
def serialize_text(value: Any, parameter: Parameter) -> Dict[str, Any]:
    return {"data": str(value)}


@register("application/json")
def serialize_json(value: Any, parameter: Parameter) -> Dict[str, Any]:
    return {"data": json.dumps(value)}


@register("multipart/form-data")
def serialize_multipart(value: Any, parameter: Parameter) -> Dict[str, Any]:
    prepare_form_data(value)
    # TODO. There should be a way to avoid creating a json schema here
    schema = parameter.as_json_schema()  # type: ignore
    files = []
    for name, property_schema in schema.get("properties", {}).items():
        if name in value:
            if isinstance(value[name], list):
                files.extend([(name, item) for item in value[name]])
            elif is_file(property_schema):
                files.append((name, value[name]))
            else:
                files.append((name, (None, value[name])))
    return {"files": files or None}


def prepare_form_data(form_data: Dict[str, Any]) -> Dict[str, Any]:
    for name, value in form_data.items():
        if isinstance(value, list):
            form_data[name] = [to_bytes(item) if not isinstance(item, (bytes, str, int)) else item for item in value]
        elif not isinstance(value, (bytes, str, int)):
            form_data[name] = to_bytes(value)
    return form_data


def to_bytes(value: Union[str, bytes, int, bool, float]) -> bytes:
    return str(value).encode(errors="ignore")


@register("application/x-www-form-urlencoded")
def serialize_form(value: Any, parameter: Parameter) -> Dict[str, Any]:
    return {"data": value}


@register("application/octet-stream")
def serialize_binary(value: Any, parameter: Parameter) -> Dict[str, Any]:
    return {"data": value}


def is_file(schema: Dict[str, Any]) -> bool:
    return schema.get("format") in ("binary", "base64")


def get(parameter: Parameter) -> Optional[Callable[[Any], Dict[str, Any]]]:  # TODO. more specific type
    # if there is a specific serializer for this media type, use it, otherwise use the generic one
    if parameter.media_type is None:
        # TODO. there could be more specific Parameter with media type that always present
        return None
    serializer = SERIALIZERS.get(parameter.media_type)
    if serializer is None:
        return None
    return partial(serializer, parameter=parameter)


def can_serialize(parameter: Parameter) -> bool:
    return parameter.media_type in SERIALIZERS
