from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, NamedTuple

from schemathesis.core.transport import CallOutcome

if TYPE_CHECKING:
    from schemathesis.core.transport import Response


class GrpcStatus(NamedTuple):
    name: str
    # The HTTP status with the same meaning.
    http_status: int
    # What the status says about the call.
    outcome: CallOutcome


# gRPC status code -> its meaning.
# FAILED_PRECONDITION and OUT_OF_RANGE depend on system state, not on the data sent, so they are conflicts.
# UNIMPLEMENTED answers calls to methods the server lacks or has disabled.
GRPC_STATUSES: dict[int, GrpcStatus] = {
    0: GrpcStatus("OK", 200, CallOutcome.ACCEPTED),
    1: GrpcStatus("CANCELLED", 499, CallOutcome.UNINFORMATIVE),
    2: GrpcStatus("UNKNOWN", 500, CallOutcome.UNINFORMATIVE),
    3: GrpcStatus("INVALID_ARGUMENT", 400, CallOutcome.REJECTED),
    4: GrpcStatus("DEADLINE_EXCEEDED", 504, CallOutcome.UNINFORMATIVE),
    5: GrpcStatus("NOT_FOUND", 404, CallOutcome.UNREACHABLE),
    6: GrpcStatus("ALREADY_EXISTS", 409, CallOutcome.CONFLICT),
    7: GrpcStatus("PERMISSION_DENIED", 403, CallOutcome.UNINFORMATIVE),
    8: GrpcStatus("RESOURCE_EXHAUSTED", 429, CallOutcome.UNINFORMATIVE),
    9: GrpcStatus("FAILED_PRECONDITION", 409, CallOutcome.CONFLICT),
    10: GrpcStatus("ABORTED", 409, CallOutcome.CONFLICT),
    11: GrpcStatus("OUT_OF_RANGE", 409, CallOutcome.CONFLICT),
    12: GrpcStatus("UNIMPLEMENTED", 501, CallOutcome.NOT_SERVED),
    13: GrpcStatus("INTERNAL", 500, CallOutcome.UNINFORMATIVE),
    14: GrpcStatus("UNAVAILABLE", 503, CallOutcome.UNINFORMATIVE),
    15: GrpcStatus("DATA_LOSS", 500, CallOutcome.UNINFORMATIVE),
    16: GrpcStatus("UNAUTHENTICATED", 401, CallOutcome.UNINFORMATIVE),
}

_HTTP_STATUS_COUNTS = Counter(status.http_status for status in GRPC_STATUSES.values())
# Only an HTTP status that stands for exactly one gRPC status names it; 409 and 500 stand for several.
STATUS_NAMES_BY_HTTP_STATUS = {
    status.http_status: status.name for status in GRPC_STATUSES.values() if _HTTP_STATUS_COUNTS[status.http_status] == 1
}

OK = 0
INTERNAL = 13

# How gRPC servers start the INTERNAL message they send when a request does not decode, before any handler runs.
# Go clients word a response that does not decode the same way, so a handler passing one on is reported here too.
LIBRARY_DECODE_ERRORS = (
    "grpc: failed to unmarshal the received message",
    "grpc: error unmarshalling request",
    "Exception deserializing request!",
    "Error deserializing request: ",
)


def grpc_status(response: Response) -> int:
    return int(response.headers["grpc-status"][0])


def starts_with_verb(name: str, verbs: tuple[str, ...]) -> bool:
    for verb in verbs:
        rest = name.removeprefix(verb)
        # `Listen` does not start with `List`: the verb must end where the next word starts.
        if rest != name and (not rest or rest[0].isupper()):
            return True
    return False
