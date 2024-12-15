from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from schemathesis.core.errors import IncorrectUsage
from schemathesis.core.marks import Mark

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation

    from .parameters import ParameterSet


@dataclass
class CaseOverride:
    """Overrides for various parts of a test case."""

    query: dict[str, str]
    headers: dict[str, str]
    cookies: dict[str, str]
    path_parameters: dict[str, str]

    def for_operation(self, operation: APIOperation) -> dict[str, dict[str, str]]:
        return {
            "query": (_for_parameters(self.query, operation.query)),
            "headers": (_for_parameters(self.headers, operation.headers)),
            "cookies": (_for_parameters(self.cookies, operation.cookies)),
            "path_parameters": (_for_parameters(self.path_parameters, operation.path_parameters)),
        }


def _for_parameters(overridden: dict[str, str], defined: ParameterSet) -> dict[str, str]:
    output = {}
    for param in defined:
        if param.name in overridden:
            output[param.name] = overridden[param.name]
    return output


OverrideMark = Mark[CaseOverride](attr_name="override")


def check_no_override_mark(test: Callable) -> None:
    if OverrideMark.is_set(test):
        raise IncorrectUsage(f"`{test.__name__}` has already been decorated with `override`.")
