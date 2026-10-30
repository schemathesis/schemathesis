from __future__ import annotations

from schemathesis.core.failures import Failure, ServerError, Severity


class MalformedGrpcMessage(Failure):
    """gRPC response or status details that do not decode as the declared message."""

    code = "ST117"
    checks = ("response_schema_conformance",)
    kind_title = "Response does not decode as the declared message"

    __slots__ = ("type_name",)

    def __init__(
        self,
        *,
        operation: str,
        type_name: str,
        message: str,
        title: str | None = None,
        case_id: str | None = None,
    ) -> None:
        super().__init__(operation=operation, title=title, message=message, case_id=case_id, severity=Severity.MEDIUM)
        self.type_name = type_name

    @property
    def _unique_key(self) -> str:
        return self.type_name


class UndecodableGrpcRequest(ServerError):
    """gRPC request the server's gRPC library failed to decode before any handler ran."""

    code = "ST119"
    checks = ("not_a_server_error",)
    kind_title = "Server could not decode the request"

    __slots__ = ()

    def __init__(
        self, *, operation: str, status_code: int, status_name: str | None = None, case_id: str | None = None
    ) -> None:
        super().__init__(
            operation=operation,
            status_code=status_code,
            status_name=status_name,
            message="The gRPC library rejected the request before any handler ran. The server's message type accepts "
            "less than the schema declares, such as a fixed-length `bytes` ID.",
            case_id=case_id,
        )


class UndeclaredGrpcFields(Failure):
    """gRPC response with fields its message, as the server reflects it, does not declare."""

    code = "ST118"
    checks = ("response_schema_conformance",)
    kind_title = "Response carries fields the schema does not declare"

    @property
    def _unique_key(self) -> str:
        # One finding per operation, whatever the fields.
        return ""
