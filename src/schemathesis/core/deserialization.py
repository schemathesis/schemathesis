from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any, BinaryIO, TextIO

from schemathesis.core import NOT_SET, media_types
from schemathesis.core.transport import Response

if TYPE_CHECKING:
    import yaml

    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation


@dataclass
class DeserializationContext:
    """Context passed to deserializers.

    Attributes:
        operation: The API operation being tested.
        case: The generated test case (`None` when validating responses directly).

    """

    operation: APIOperation
    case: Case | None

    __slots__ = ("operation", "case")


ResponseDeserializer = Callable[[DeserializationContext, Response], Any]

_DESERIALIZERS: dict[str, ResponseDeserializer] = {}


def _iter_matching_deserializers(media_type: str) -> Iterator[tuple[str, ResponseDeserializer]]:
    main, sub = media_types.parse(media_type)
    checks = [
        media_types.is_json,
        media_types.is_xml,
        media_types.is_plain_text,
        media_types.is_yaml,
    ]
    for registered_media_type, deserializer in _DESERIALIZERS.items():
        if any(check(media_type) and check(registered_media_type) for check in checks):
            yield registered_media_type, deserializer
        else:
            target_main, target_sub = media_types.parse(registered_media_type)
            if main in ("*", target_main) and sub in ("*", target_sub):
                yield registered_media_type, deserializer


def has_deserializer(media_type: str) -> bool:
    """Check if a deserializer is registered or built-in for the given media type.

    Args:
        media_type: The media type to check (e.g., "application/msgpack")

    Returns:
        True if a deserializer is available (either registered or built-in like JSON/YAML/XML)

    """
    return (
        media_types.is_json(media_type)
        or media_types.is_yaml(media_type)
        or media_types.is_xml(media_type)
        or media_types.is_plain_text(media_type)
        or any(_iter_matching_deserializers(media_type))
    )


def register_deserializer(func: ResponseDeserializer, *media_types: str) -> ResponseDeserializer:
    for media_type in media_types:
        _DESERIALIZERS[media_type] = func
    return func


def unregister_deserializer(*media_types: str) -> None:
    for media_type in media_types:
        _DESERIALIZERS.pop(media_type, None)


def deserializer(*media_types: str) -> Callable[[ResponseDeserializer], ResponseDeserializer]:
    """Register a deserializer for custom response media types.

    Converts API responses with custom content types (MessagePack, domain-specific formats, etc.)
    into Python objects for schema validation. Built-in formats (JSON, YAML) work automatically.

    Args:
        *media_types: One or more MIME types (e.g., "application/msgpack", "application/vnd.custom+json")
                      this deserializer handles. Wildcards are supported (e.g., "application/*").

    Returns:
        A decorator that wraps a function taking `(ctx: DeserializationContext, response: Response)`
        and returning the deserialized Python object for schema validation.

    Example:
        >>> import schemathesis
        >>> import msgpack
        >>>
        >>> @schemathesis.deserializer("application/msgpack", "application/x-msgpack")
        ... def deserialize_msgpack(ctx, response):
        ...     try:
        ...         return msgpack.unpackb(response.content, raw=False)
        ...     except Exception as exc:
        ...         raise ValueError(f"Invalid MessagePack: {exc}")

    Notes:
        - Raise appropriate exceptions if deserialization fails; Schemathesis will report them
        - `ctx.operation` provides access to the API operation being tested (always available)
        - `ctx.case` provides the generated test case (None when validating responses directly)
        - Responses with unsupported media types are silently skipped during validation
        - Handle unexpected data defensively, especially during negative testing

    """

    def decorator(func: ResponseDeserializer) -> ResponseDeserializer:
        return register_deserializer(func, *media_types)

    return decorator


@lru_cache
def get_yaml_loader() -> type[yaml.SafeLoader]:
    """Create a YAML loader, that doesn't parse specific tokens into Python objects."""
    import yaml

    try:
        from yaml import CSafeLoader as SafeLoader
    except ImportError:
        from yaml import SafeLoader  # type: ignore[assignment]

    cls: type[yaml.SafeLoader] = type("YAMLLoader", (SafeLoader,), {})
    cls.yaml_implicit_resolvers = {
        key: [(tag, regexp) for tag, regexp in mapping if tag != "tag:yaml.org,2002:timestamp"]
        for key, mapping in cls.yaml_implicit_resolvers.copy().items()
    }

    # Fix pyyaml scientific notation parse bug
    # See PR: https://github.com/yaml/pyyaml/pull/174 for upstream fix
    cls.add_implicit_resolver(  # type: ignore[no-untyped-call]
        "tag:yaml.org,2002:float",
        re.compile(
            r"""^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+]?[0-9]+)?
                       |[-+]?(?:[0-9][0-9_]*)(?:[eE][-+]?[0-9]+)
                       |\.[0-9_]+(?:[eE][-+]?[0-9]+)?
                       |[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*
                       |[-+]?\.(?:inf|Inf|INF)
                       |\.(?:nan|NaN|NAN))$""",
            re.VERBOSE,
        ),
        list("-+0123456789."),
    )

    def construct_mapping(self: SafeLoader, node: yaml.Node, deep: bool = False) -> dict[str, Any]:
        if isinstance(node, yaml.MappingNode):
            self.flatten_mapping(node)
        mapping = {}
        for key_node, value_node in node.value:
            # If the key has a tag different from `str` - use its string value.
            # With this change all integer keys or YAML 1.1 boolean-ish values like "on" / "off" will not be cast to
            # a different type
            if key_node.tag != "tag:yaml.org,2002:str":
                key = key_node.value
            else:
                key = self.construct_object(key_node, deep)  # type: ignore[no-untyped-call]
            mapping[key] = self.construct_object(value_node, deep)  # type: ignore[no-untyped-call]
        return mapping

    cls.construct_mapping = construct_mapping  # type: ignore[method-assign,assignment]
    return cls


def deserialize_yaml(stream: str | bytes | TextIO | BinaryIO) -> Any:
    import yaml

    return yaml.load(stream, get_yaml_loader())


def deserializers() -> dict[str, ResponseDeserializer]:
    """Return a snapshot of the registered deserializers."""
    return dict(_DESERIALIZERS)


def deserialize_response(
    response: Response,
    content_type: str,
    *,
    context: DeserializationContext,
) -> Any:
    # Check cache first to avoid re-parsing the same response
    if response._deserialized is not NOT_SET:
        return response._deserialized

    for _, deserializer in _iter_matching_deserializers(content_type):
        data = deserializer(context, response)
        # Cache the result for future calls
        response._deserialized = data
        return data
    raise NotImplementedError(
        f"Cannot deserialize response with Content-Type: {content_type!r}\n\n"
        f"Register a deserializer with @schemathesis.deserializer() to handle this media type"
    )


@deserializer("application/json")
def _deserialize_json(_ctx: DeserializationContext, response: Response) -> Any:
    return response.json()


@deserializer(*media_types.YAML_MEDIA_TYPES)
def _deserialize_yaml(_ctx: DeserializationContext, response: Response) -> Any:
    encoding = response.encoding or "utf-8"
    return deserialize_yaml(response.content.decode(encoding))
