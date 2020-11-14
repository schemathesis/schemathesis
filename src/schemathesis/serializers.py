from typing import Any, Callable, Dict, Optional

SERIALIZERS = {}


Serializer = Callable[[Any], Dict[str, Any]]


def register(media_type: str) -> Callable[[Serializer], Serializer]:
    def wrapper(function: Serializer) -> Serializer:
        SERIALIZERS[media_type] = function
        return function

    return wrapper


# TODO.
# SerializationContext
# - raw value
# -


@register("text/plain")
def serialize_text(value: Any) -> Dict[str, Any]:
    return {"data": str(value)}


@register("application/json")
def serialize_json(value: Any) -> Dict[str, Any]:
    return {"json": value}


@register("multipart/form-data")
def serialize_multipart(value: Any) -> Dict[str, Any]:
    return {"files": value or None}


@register("application/x-www-form-urlencoded")
def serialize_form(value: Any) -> Dict[str, Any]:
    return {"data": value}


@register("application/octet-stream")
def serialize_binary(value: Any) -> Dict[str, Any]:
    return {"data": value}


def get(media_type: str) -> Optional[Callable[[Any], Dict[str, Any]]]:
    # if there is a specific serializer for this media type, use it, otherwise use the generic one
    return SERIALIZERS.get(media_type)
