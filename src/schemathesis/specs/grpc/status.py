from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, NamedTuple

from schemathesis.core.transport import CallOutcome, StatusClass

if TYPE_CHECKING:
    from schemathesis.core.transport import Response


class GrpcStatus(NamedTuple):
    name: str
    status_class: StatusClass
    # What the status says about the call.
    outcome: CallOutcome


# gRPC status code -> its meaning.
# FAILED_PRECONDITION and OUT_OF_RANGE depend on system state, not on the data sent, so they are conflicts.
# UNIMPLEMENTED answers calls to methods the server lacks or has disabled.
GRPC_STATUSES: dict[int, GrpcStatus] = {
    0: GrpcStatus("OK", StatusClass.SUCCESS, CallOutcome.ACCEPTED),
    1: GrpcStatus("CANCELLED", StatusClass.CLIENT_ERROR, CallOutcome.UNINFORMATIVE),
    2: GrpcStatus("UNKNOWN", StatusClass.INTERNAL_ERROR, CallOutcome.UNINFORMATIVE),
    3: GrpcStatus("INVALID_ARGUMENT", StatusClass.CLIENT_ERROR, CallOutcome.REJECTED),
    4: GrpcStatus("DEADLINE_EXCEEDED", StatusClass.SERVER_ERROR, CallOutcome.UNINFORMATIVE),
    5: GrpcStatus("NOT_FOUND", StatusClass.NOT_FOUND, CallOutcome.UNREACHABLE),
    6: GrpcStatus("ALREADY_EXISTS", StatusClass.CONFLICT, CallOutcome.CONFLICT),
    7: GrpcStatus("PERMISSION_DENIED", StatusClass.FORBIDDEN, CallOutcome.UNINFORMATIVE),
    8: GrpcStatus("RESOURCE_EXHAUSTED", StatusClass.RATE_LIMITED, CallOutcome.UNINFORMATIVE),
    9: GrpcStatus("FAILED_PRECONDITION", StatusClass.CONFLICT, CallOutcome.CONFLICT),
    10: GrpcStatus("ABORTED", StatusClass.CONFLICT, CallOutcome.CONFLICT),
    11: GrpcStatus("OUT_OF_RANGE", StatusClass.CONFLICT, CallOutcome.CONFLICT),
    12: GrpcStatus("UNIMPLEMENTED", StatusClass.NOT_IMPLEMENTED, CallOutcome.NOT_SERVED),
    13: GrpcStatus("INTERNAL", StatusClass.INTERNAL_ERROR, CallOutcome.UNINFORMATIVE),
    14: GrpcStatus("UNAVAILABLE", StatusClass.SERVER_ERROR, CallOutcome.UNINFORMATIVE),
    15: GrpcStatus("DATA_LOSS", StatusClass.INTERNAL_ERROR, CallOutcome.UNINFORMATIVE),
    16: GrpcStatus("UNAUTHENTICATED", StatusClass.UNAUTHENTICATED, CallOutcome.UNINFORMATIVE),
}
GRPC_STATUS_CODES_BY_NAME = {status.name: code for code, status in GRPC_STATUSES.items()}
_CLASS_COUNTS = Counter(status.status_class for status in GRPC_STATUSES.values())
# Only a class that exactly one status belongs to names it; several statuses are conflicts or internal errors.
GRPC_STATUS_CODES_BY_CLASS = {
    status.status_class: code for code, status in GRPC_STATUSES.items() if _CLASS_COUNTS[status.status_class] == 1
}

OK = 0

# How gRPC servers start messages for requests that do not decode before any handler runs.
LIBRARY_DECODE_ERRORS = {
    3: ("unmarshal message: ",),
    13: (
        "grpc: failed to unmarshal the received message",
        "grpc: error unmarshalling request",
        "Exception deserializing request!",
        "Error deserializing request: ",
    ),
}


def grpc_status(response: Response) -> int:
    return int(response.headers["grpc-status"][0])


def is_library_decode_error(response: Response) -> bool:
    prefixes = LIBRARY_DECODE_ERRORS.get(grpc_status(response), ())
    return response.headers.get("grpc-message", [""])[0].startswith(prefixes)


def starts_with_verb(name: str, verbs: tuple[str, ...]) -> bool:
    for verb in verbs:
        rest = name.removeprefix(verb)
        # `Listen` does not start with `List`: the verb must end where the next word starts.
        if rest != name and (not rest or rest[0].isupper()):
            return True
    return False
