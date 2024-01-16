from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..parameters import ParameterSet

if TYPE_CHECKING:
    from ..models import APIOperation


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
