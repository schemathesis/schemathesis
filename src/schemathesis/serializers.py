from typing import TYPE_CHECKING, Any, Callable, Dict, Type, Union

import attr
from typing_extensions import Protocol

if TYPE_CHECKING:
    from .models import Case


SERIALIZERS = {}


@attr.s(slots=True)
class SerializerContext:
    """Context for serialization process."""

    case: "Case" = attr.ib()


class Serializer(Protocol):
    """Transform generated data to a form, supported by the transport layer.

    For example, to handle multipart forms we need to serialize them differently for
    `requests` and `werkzeug` transports.
    """

    def as_requests(self, context: SerializerContext, payload: Any) -> Any:
        raise NotImplementedError

    def as_werkzeug(self, context: SerializerContext, payload: Any) -> Any:
        raise NotImplementedError


def register(media_type: str) -> Callable[[Type[Serializer]], Type[Serializer]]:
    def wrapper(function: Type[Serializer]) -> Type[Serializer]:
        SERIALIZERS[media_type] = function
        return function

    return wrapper


@register("application/json")
class JSONSerializer:
    def as_requests(self, context: SerializerContext, value: Any) -> Any:
        return {"json": value}

    def as_werkzeug(self, context: SerializerContext, value: Any) -> Any:
        return {"json": value}


def prepare_form_data(form_data: Dict[str, Any]) -> Dict[str, Any]:
    for name, value in form_data.items():
        if isinstance(value, list):
            form_data[name] = [to_bytes(item) if not isinstance(item, (bytes, str, int)) else item for item in value]
        elif not isinstance(value, (bytes, str, int)):
            form_data[name] = to_bytes(value)
    return form_data


def to_bytes(value: Union[str, bytes, int, bool, float]) -> bytes:
    return str(value).encode(errors="ignore")


@register("multipart/form-data")
class MultipartSerializer:
    def as_requests(self, context: SerializerContext, value: Dict[str, Any]) -> Any:
        # Form data always is generated as a dictionary
        multipart = prepare_form_data(value)
        files, data = context.case.endpoint.prepare_multipart(multipart)
        return {"files": files, "data": data}

    def as_werkzeug(self, context: SerializerContext, value: Any) -> Any:
        return {"data": value}


@register("application/x-www-form-urlencoded")
class URLEncodedFormSerializer:
    def as_requests(self, context: SerializerContext, value: Any) -> Any:
        return {"data": value}

    def as_werkzeug(self, context: SerializerContext, value: Any) -> Any:
        return {"data": value}


@register("text/plain")
class TextSerializer:
    def as_requests(self, context: SerializerContext, value: Any) -> Any:
        return {"data": str(value).encode("utf8")}

    def as_werkzeug(self, context: SerializerContext, value: Any) -> Any:
        return {"data": str(value)}


@register("application/octet-stream")
class GenericPayloadSerializer:
    def as_requests(self, context: SerializerContext, value: Any) -> Any:
        return {"data": value}

    def as_werkzeug(self, context: SerializerContext, value: Any) -> Any:
        return {"data": value}


get = SERIALIZERS.get
