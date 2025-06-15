from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterator

from schemathesis.config import ProjectConfig
from schemathesis.core.transforms import diff
from schemathesis.generation.meta import ComponentKind

if TYPE_CHECKING:
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation, Parameter


@dataclass
class Override:
    """Overrides for various parts of a test case."""

    query: dict[str, str]
    headers: dict[str, str]
    cookies: dict[str, str]
    path_parameters: dict[str, str]

    __slots__ = ("query", "headers", "cookies", "path_parameters")

    def items(self) -> Iterator[tuple[str, dict[str, str]]]:
        for key, value in (
            ("query", self.query),
            ("headers", self.headers),
            ("cookies", self.cookies),
            ("path_parameters", self.path_parameters),
        ):
            if value:
                yield key, value

    @classmethod
    def from_components(cls, components: dict[ComponentKind, StoredValue], case: Case) -> Override:
        return Override(
            **{
                kind.value: get_component_diff(stored=stored, current=getattr(case, kind.value))
                for kind, stored in components.items()
            }
        )


def for_operation(config: ProjectConfig, *, operation: APIOperation) -> Override:
    operation_config = config.operations.get_for_operation(operation)

    output = Override(query={}, headers={}, cookies={}, path_parameters={})
    groups = [
        (output.query, operation.query),
        (output.headers, operation.headers),
        (output.cookies, operation.cookies),
        (output.path_parameters, operation.path_parameters),
    ]
    for container, params in groups:
        for param in params:
            # Attempt to get the override from the operation-specific configuration.
            value = None
            if operation_config:
                value = _get_override_value(param, operation_config.parameters)
            # Fallback to the global project configuration.
            if value is None:
                value = _get_override_value(param, config.parameters)
            if value is not None:
                container[param.name] = value

    return output


def _get_override_value(param: Parameter, parameters: dict[str, Any]) -> Any:
    key = param.name
    full_key = f"{param.location}.{param.name}"
    if key in parameters:
        return parameters[key]
    elif full_key in parameters:
        return parameters[full_key]
    return None


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
