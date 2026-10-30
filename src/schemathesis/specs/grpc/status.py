from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.core.transport import CallOutcome

if TYPE_CHECKING:
    from schemathesis.core.transport import Response

# gRPC status code -> its name, the HTTP status with the same meaning and the call outcome it signals.
# FAILED_PRECONDITION and OUT_OF_RANGE depend on system state, not on the data sent, so they are conflicts.
# UNIMPLEMENTED answers calls to methods the server lacks or has disabled.
GRPC_STATUSES: dict[int, tuple[str, int, CallOutcome]] = {
    0: ("OK", 200, CallOutcome.ACCEPTED),
    1: ("CANCELLED", 499, CallOutcome.UNINFORMATIVE),
    2: ("UNKNOWN", 500, CallOutcome.UNINFORMATIVE),
    3: ("INVALID_ARGUMENT", 400, CallOutcome.REJECTED),
    4: ("DEADLINE_EXCEEDED", 504, CallOutcome.UNINFORMATIVE),
    5: ("NOT_FOUND", 404, CallOutcome.UNREACHABLE),
    6: ("ALREADY_EXISTS", 409, CallOutcome.CONFLICT),
    7: ("PERMISSION_DENIED", 403, CallOutcome.UNINFORMATIVE),
    8: ("RESOURCE_EXHAUSTED", 429, CallOutcome.UNINFORMATIVE),
    9: ("FAILED_PRECONDITION", 409, CallOutcome.CONFLICT),
    10: ("ABORTED", 409, CallOutcome.CONFLICT),
    11: ("OUT_OF_RANGE", 409, CallOutcome.CONFLICT),
    12: ("UNIMPLEMENTED", 501, CallOutcome.NOT_SERVED),
    13: ("INTERNAL", 500, CallOutcome.UNINFORMATIVE),
    14: ("UNAVAILABLE", 503, CallOutcome.UNINFORMATIVE),
    15: ("DATA_LOSS", 500, CallOutcome.UNINFORMATIVE),
    16: ("UNAUTHENTICATED", 401, CallOutcome.UNINFORMATIVE),
}


INTERNAL = 13

# How gRPC servers start the INTERNAL message they send when a request does not decode, before any handler runs.
# Client libraries word a response that does not decode differently, so a handler passing one on does not match.
LIBRARY_DECODE_ERRORS = (
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
