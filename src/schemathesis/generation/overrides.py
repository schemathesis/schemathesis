from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from schemathesis.core.errors import IncorrectUsage
from schemathesis.core.marks import Mark
from schemathesis.core.transforms import diff
from schemathesis.generation.meta import ComponentKind

if TYPE_CHECKING:
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation, ParameterSet


@dataclass
class Override:
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

    @classmethod
    def from_components(cls, components: dict[ComponentKind, StoredValue], case: Case) -> Override:
        return Override(
            **{
                kind.value: get_component_diff(stored=stored, current=getattr(case, kind.value))
                for kind, stored in components.items()
            }
        )


def _for_parameters(overridden: dict[str, str], defined: ParameterSet) -> dict[str, str]:
    output = {}
    for param in defined:
        if param.name in overridden:
            output[param.name] = overridden[param.name]
    return output


@dataclass
class StoredValue:
    value: dict[str, Any] | None
    is_generated: bool

    __slots__ = ("value", "is_generated")


def store_original_state(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return value.copy()
    return value


def get_component_diff(stored: StoredValue, current: dict[str, Any] | None) -> dict[str, Any]:
    """Calculate difference between stored and current components."""
    if not (current and stored.value):
        return {}
    if stored.is_generated:
        return diff(stored.value, current)
    return current


def store_components(case: Case) -> dict[ComponentKind, StoredValue]:
    """Store original component states for a test case."""
    return {
        kind: StoredValue(
            value=store_original_state(getattr(case, kind.value)),
            is_generated=bool(case.meta and kind in case.meta.components),
        )
        for kind in [
            ComponentKind.QUERY,
            ComponentKind.HEADERS,
            ComponentKind.COOKIES,
            ComponentKind.PATH_PARAMETERS,
        ]
    }


OverrideMark = Mark[Override](attr_name="override")


def check_no_override_mark(test: Callable) -> None:
    if OverrideMark.is_set(test):
        raise IncorrectUsage(f"`{test.__name__}` has already been decorated with `override`.")
