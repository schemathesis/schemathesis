from __future__ import annotations

import textwrap
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from ...transports import get_excluded_headers
from .status import Status
from .transport import Request, Response

if TYPE_CHECKING:
    from ...exceptions import FailureContext
    from ...models import Case
    from ...transports import PreparedRequestData


@dataclass(repr=False)
class Check:
    """Single check run result."""

    name: str
    value: Status
    request: Request
    response: Response | None
    case: Case
    message: str | None = None
    # Failure-specific context
    context: FailureContext | None = None

    def prepare_code_sample_data(self) -> PreparedRequestData:
        headers = {key: value[0] for key, value in self.request.headers.items() if key not in get_excluded_headers()}
        return self.case.prepare_code_sample_data(headers)

    @property
    def title(self) -> str:
        if self.context is not None:
            return self.context.title
        return f"Custom check failed: `{self.name}`"

    @property
    def formatted_message(self) -> str | None:
        if self.context is not None:
            if self.context.message:
                message = self.context.message
            else:
                message = None
        else:
            message = self.message
        if message is not None:
            message = textwrap.indent(message, prefix="    ")
        return message

    def asdict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "request": {
                "method": self.request.method,
                "uri": self.request.uri,
                "body": self.request.encoded_body,
                "headers": self.request.headers,
            },
            "response": self.response.asdict() if self.response is not None else None,
            "example": self.case.asdict(),
            "message": self.message,
            "context": asdict(self.context) if self.context is not None else None,  # type: ignore
        }


def deduplicate_failures(checks: list[Check]) -> list[Check]:
    """Return only unique checks that should be displayed in the output."""
    seen: set[tuple[str | None, ...]] = set()
    unique_checks = []
    for check in reversed(checks):
        # There are also could be checks that didn't fail
        if check.value == Status.failure:
            key: tuple
            if check.context is not None:
                key = check.context.unique_by_key(check.message)
            else:
                key = check.name, check.message
            if key not in seen:
                unique_checks.append(check)
                seen.add(key)
    return unique_checks
