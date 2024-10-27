from __future__ import annotations

import textwrap
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from ...transports import get_excluded_headers
from .status import Status
from .transport import Request, Response

if TYPE_CHECKING:
    from requests.structures import CaseInsensitiveDict

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
        headers = _get_headers(self.request.headers)
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


def _get_headers(headers: dict[str, Any] | CaseInsensitiveDict) -> dict[str, str]:
    return {
        key: value[0] if isinstance(value, list) else value
        for key, value in headers.items()
        if key not in get_excluded_headers()
    }


def make_unique_by_key(
    check_name: str, check_message: str | None, context: FailureContext | None
) -> tuple[str | None, ...]:
    """A key to distinguish different failed checks.

    It is not only based on `FailureContext`, because the end-user may raise plain `AssertionError` in their custom
    checks, and those won't have any context attached.
    """
    if context is not None:
        return context.unique_by_key(check_message)
    return check_name, check_message


def deduplicate_failures(checks: list[Check]) -> list[Check]:
    """Return only unique checks that should be displayed in the output."""
    seen: set[tuple[str | None, ...]] = set()
    unique_checks = []
    for check in reversed(checks):
        # There are also could be checks that didn't fail
        if check.value == Status.failure:
            key = make_unique_by_key(check.name, check.message, check.context)
            if key not in seen:
                unique_checks.append(check)
                seen.add(key)
    return unique_checks
