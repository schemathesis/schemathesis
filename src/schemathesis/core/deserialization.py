from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any, BinaryIO, NotRequired, TextIO

from typing_extensions import TypedDict

from schemathesis.core import NOT_SET, media_types
from schemathesis.core.transport import Response

if TYPE_CHECKING:
    import yaml

    from schemathesis.core.jsonschema.types import JsonValue
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation


@dataclass(slots=True)
class DeserializationContext:
    """Context passed to deserializers.

    Attributes:
        operation: The API operation being tested.
        case: The generated test case (`None` when validating responses directly).

    """

    operation: APIOperation
    case: Case | None


ResponseDeserializer = Callable[[DeserializationContext, Response], Any]

_DESERIALIZERS: dict[str, ResponseDeserializer] = {}


def _iter_matching_deserializers(media_type: str) -> Iterator[tuple[str, ResponseDeserializer]]:
    parsed = media_types.parse(media_type)
    for registered_media_type, deserializer in _DESERIALIZERS.items():
        if media_types.same_family(media_type, registered_media_type) or media_types.matches_either(
            parsed, media_types.parse(registered_media_type)
        ):
            yield registered_media_type, deserializer


def has_deserializer(media_type: str) -> bool:
    """True if a registered or built-in (JSON, YAML, XML, plain text) deserializer handles `media_type`."""
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


def deserializer(*media_types: str) -> Callable[[ResponseDeserializer], ResponseDeserializer]:
    """Register a deserializer for custom response media types.

    Converts responses with custom content types (MessagePack, domain-specific formats, etc.)
    into Python objects for schema validation. JSON and YAML need no registration.

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
        - Raise an exception when deserialization fails; Schemathesis reports it
        - `ctx.operation` is the API operation under test (always set)
        - `ctx.case` is the generated test case (`None` when validating responses directly)
        - Validation skips responses with unsupported media types without a warning
        - Handle unexpected data, especially during negative testing

    """

    def decorator(func: ResponseDeserializer) -> ResponseDeserializer:
        return register_deserializer(func, *media_types)

    return decorator


@lru_cache
def get_yaml_loader() -> type[yaml.SafeLoader]:
    """YAML loader that keeps timestamps, non-string keys and `!!binary` values as strings."""
    import yaml

    try:
        from yaml import CSafeLoader as SafeLoader
    except ImportError:
        from yaml import SafeLoader  # type: ignore[assignment]

    cls: type[yaml.SafeLoader] = type("YAMLLoader", (SafeLoader,), {})
    cls.yaml_implicit_resolvers = {
        key: [
            (tag, regexp)
            for tag, regexp in mapping
            if tag not in ("tag:yaml.org,2002:timestamp", "tag:yaml.org,2002:bool")
        ]
        for key, mapping in cls.yaml_implicit_resolvers.copy().items()
    }

    # YAML 1.2 booleans only: YAML 1.1 would read a parameter named `on`, `off`, `yes` or `no` as a boolean.
    cls.add_implicit_resolver(
        "tag:yaml.org,2002:bool",
        re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
        list("tTfF"),
    )

    # Work around pyyaml's scientific notation parsing, see https://github.com/yaml/pyyaml/pull/174
    cls.add_implicit_resolver(
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
            # Keep non-string keys (integers, `on` / `off`) as their source text
            if key_node.tag != "tag:yaml.org,2002:str":
                key = key_node.value
            else:
                key = self.construct_object(key_node, deep)
            mapping[key] = self.construct_object(value_node, deep)
        return mapping

    cls.construct_mapping = construct_mapping  # type: ignore[method-assign,assignment]

    def construct_binary(self: yaml.SafeLoader, node: yaml.ScalarNode) -> str:
        return self.construct_scalar(node)

    # JSON has no bytes type, so a `!!binary` value stays the base64 text written in the document.
    cls.add_constructor("tag:yaml.org,2002:binary", construct_binary)
    return cls


def deserialize_yaml(stream: str | bytes | TextIO | BinaryIO) -> Any:
    import yaml

    return yaml.load(stream, get_yaml_loader())


def deserialize_response(
    response: Response,
    content_type: str,
    *,
    context: DeserializationContext,
) -> Any:
    if response._deserialized is not NOT_SET:
        return response._deserialized

    for _, deserializer in _iter_matching_deserializers(content_type):
        data = deserializer(context, response)
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
    import yaml

    encoding = response.encoding or "utf-8"
    try:
        return deserialize_yaml(response.content.decode(encoding))
    except yaml.YAMLError as exc:
        # Callers treat `ValueError` as an unreadable body, as with malformed JSON.
        raise ValueError(str(exc)) from exc


class ServerSentEvent(TypedDict):
    data: str
    event: str
    id: NotRequired[str]
    retry: NotRequired[int]


SSE_RETRY_RE = re.compile(r"^[0-9]+$")
# Only CR, LF and CRLF end an SSE line; `str.splitlines` also splits on U+2028 and others inside payloads.
SSE_LINE_RE = re.compile(r"\r\n|\r|\n")


def _flush_sse_event(
    current_event: dict[str, Any],
    current_data_lines: list[str],
    *,
    last_event_id: str | None,
) -> ServerSentEvent | None:
    if not current_data_lines:
        return None
    event_type = current_event.get("event") or "message"
    event: ServerSentEvent = {
        "data": "\n".join(current_data_lines),
        "event": event_type,
    }
    if "id" in current_event:
        event["id"] = current_event["id"]
    elif last_event_id is not None:
        event["id"] = last_event_id
    if "retry" in current_event:
        event["retry"] = current_event["retry"]
    return event


def _parse_sse_events(content: bytes, encoding: str = "utf-8") -> list[ServerSentEvent]:
    # Some servers include a UTF-8 BOM in the first chunk
    text = content.decode(encoding).removeprefix("\ufeff")

    events: list[ServerSentEvent] = []
    current_event: dict[str, Any] = {}
    current_data_lines: list[str] = []
    last_event_id: str | None = None

    for line in SSE_LINE_RE.split(text):
        if line == "":
            flushed = _flush_sse_event(current_event, current_data_lines, last_event_id=last_event_id)
            if flushed is not None:
                events.append(flushed)
            current_event.clear()
            current_data_lines.clear()
        elif line.startswith(":"):
            continue
        else:
            if ":" in line:
                field, _, value = line.partition(":")
                # Per SSE spec, strip a single leading space from the value
                value = value.removeprefix(" ")
            else:
                field = line
                value = ""
            if field == "data":
                current_data_lines.append(value)
            elif field == "event":
                current_event[field] = value
            elif field == "retry":
                if SSE_RETRY_RE.fullmatch(value):
                    current_event[field] = int(value)
            elif field == "id" and "\x00" not in value:
                # Per WHATWG SSE spec: ignore id fields containing null characters
                current_event[field] = value
                last_event_id = value

    flushed = _flush_sse_event(current_event, current_data_lines, last_event_id=last_event_id)
    if flushed is not None:
        events.append(flushed)

    return events


@deserializer("text/event-stream")
def _deserialize_sse(_ctx: DeserializationContext, response: Response) -> list[ServerSentEvent]:
    # The SSE spec mandates UTF-8, whatever encoding the transport guesses.
    return _parse_sse_events(response.content, "utf-8")


def parse_json_sequence(content: bytes, media_type: str) -> list[JsonValue]:
    """Values of a JSON Lines or JSON Text Sequence body, one per line or record."""
    separator = "\x1e" if media_types.is_json_text_sequence(media_type) else "\n"
    text = content.decode("utf-8-sig")
    values: list[JsonValue] = []
    offset = 0
    for record in text.split(separator):
        # Only JSON whitespace makes a record blank, a line of other Unicode whitespace is malformed JSON
        if record.strip(" \t\r\n"):
            try:
                values.append(json.loads(record))
            except json.JSONDecodeError as exc:
                # Point at the malformed record within the whole body, not within the record alone
                raise json.JSONDecodeError(exc.msg, text, offset + exc.pos) from None
        offset += len(record) + len(separator)
    return values
