from __future__ import annotations

import base64
from collections.abc import Iterator
from typing import TYPE_CHECKING, cast

from google.protobuf import message, message_factory

# googleapis-common-protos ships no type information.
from google.rpc import error_details_pb2, status_pb2  # type: ignore[import-untyped]

from schemathesis.core import NotSet
from schemathesis.core.error_feedback import Observation, ObservationKind
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transforms import deepclone
from schemathesis.specs.grpc.converter import request_message, require_set

if TYPE_CHECKING:
    from google.protobuf.descriptor import Descriptor, FieldDescriptor

    from schemathesis.core import Body
    from schemathesis.core.error_feedback import ParameterPath
    from schemathesis.core.jsonschema.types import JsonSchemaObject, JsonValue
    from schemathesis.core.transport import Response
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation


def parse_rejection(operation: APIOperation, case: Case, response: Response) -> tuple[Observation, ...]:
    """Fields that `google.rpc.BadRequest` status details name and the request left unset."""
    violations = list(_field_violations(response))
    if not violations:
        return ()
    descriptor = operation.definition.raw.input_type
    request = _request_message(descriptor, case.body)
    return tuple(
        Observation(
            operation_label=operation.label,
            location=ParameterLocation.BODY,
            parameter_path=path,
            kind=ObservationKind.MUST_NOT_BE_BLANK,
            raw_message=violation.description,
        )
        for violation in violations
        if (path := _missing_path(request, descriptor, violation.field)) is not None
    )


def require_fields(schema: JsonSchemaObject, descriptor: Descriptor, observations: tuple[Observation, ...]) -> None:
    """Make every field on each observed path required and set in the message schema, in place."""
    definitions = cast("dict[str, JsonSchemaObject]", schema.get("$defs", {}))
    # Reported members of one oneof, by the path to their message; the message sets one of them.
    oneofs: dict[tuple[ParameterPath, str], list[JsonValue]] = {}
    for observation in observations:
        if observation.kind != ObservationKind.MUST_NOT_BE_BLANK:
            continue
        message_type = descriptor
        target = schema
        for depth, name in enumerate(observation.parameter_path):
            _inline(target, definitions)
            properties = cast("dict[str, JsonSchemaObject] | None", target.get("properties"))
            # Well-known types have no properties, and `google.protobuf.Any` fields are never generated.
            if properties is None or name not in properties:
                break
            field = next(field for field in message_type.fields if field.json_name == name)
            oneof = field.containing_oneof
            if oneof is None:
                required = cast("list[str]", target.setdefault("required", []))
                if name not in required:
                    required.append(name)
            else:
                key = (observation.parameter_path[:depth], oneof.full_name)
                if key not in oneofs:
                    oneofs[key] = []
                    cast("list[JsonValue]", target.setdefault("allOf", [])).append({"anyOf": oneofs[key]})
                oneofs[key].append({"required": [name]})
            require_set(properties[name], field)
            if field.message_type is None:
                break
            message_type = field.message_type
            # Well-known types are inlined, so a path into one ends at the field that holds it.
            if message_type.full_name not in definitions:
                break
            target = properties[name]


def _inline(schema: JsonSchemaObject, definitions: dict[str, JsonSchemaObject]) -> None:
    """Replace a reference with a copy of its definition, so constraints on `schema` hold only where it is used."""
    reference = schema.pop("$ref", None)
    if isinstance(reference, str):
        schema.update(deepclone(definitions[reference.removeprefix("#/$defs/")]))


def _request_message(descriptor: Descriptor, body: Body) -> message.Message:
    if isinstance(body, bytes):
        return message_factory.GetMessageClass(descriptor).FromString(body)
    return request_message({} if isinstance(body, NotSet) else body, descriptor)


def _field_violations(response: Response) -> Iterator[error_details_pb2.BadRequest.FieldViolation]:
    for encoded in response.headers.get("grpc-status-details-bin", []):
        try:
            status = status_pb2.Status.FromString(base64.b64decode(encoded))
        except message.DecodeError:
            # Response conformance reports malformed details.
            continue
        for detail in status.details:
            bad_request = error_details_pb2.BadRequest()
            try:
                unpacked = detail.Unpack(bad_request)
            except message.DecodeError:
                continue
            # Details of other types, including ones this client has no descriptors for, carry no field paths.
            if unpacked:
                yield from bad_request.field_violations


def _missing_path(request: message.Message, descriptor: Descriptor, path: str) -> ParameterPath | None:
    """JSON names along a dotted `path` of proto or JSON field names, when its last field is unset."""
    resolved: list[str | int] = []
    container = request
    *parents, last = path.split(".")
    for name in parents:
        field = _find_field(descriptor, name)
        if field is None or field.message_type is None or field.is_repeated:
            return None
        resolved.append(field.json_name)
        container = getattr(container, field.name)
        descriptor = field.message_type
    field = _find_field(descriptor, last)
    if field is None or _is_set(container, field):
        return None
    return (*resolved, field.json_name)


def _find_field(descriptor: Descriptor, name: str) -> FieldDescriptor | None:
    return descriptor.fields_by_name.get(name) or next(
        (field for field in descriptor.fields if field.json_name == name), None
    )


def _is_set(container: message.Message, field: FieldDescriptor) -> bool:
    value = getattr(container, field.name)
    if field.is_repeated:
        return len(value) > 0
    if field.has_presence:
        return container.HasField(field.name)
    return bool(value != field.default_value)
