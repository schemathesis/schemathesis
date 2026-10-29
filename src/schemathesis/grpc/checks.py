from __future__ import annotations

from schemathesis.core.failures import Failure, Severity


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
