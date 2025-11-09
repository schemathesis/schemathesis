from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple, Tuple

from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transforms import deepclone
from schemathesis.resources.interfaces import ParameterSchemaAugmenter
from schemathesis.resources.repository import ResourceRepository
from schemathesis.specs.openapi.stateful.dependencies.models import DependencyGraph, InputSlot

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation

RequirementKey = Tuple[str, ParameterLocation, str]
SUPPORTED_LOCATIONS = frozenset({ParameterLocation.PATH})
MAX_ENUM_SIZE = 20


class ParameterRequirement(NamedTuple):
    resource_name: str
    resource_field: str


def _is_supported_input(slot: InputSlot) -> bool:
    return (
        isinstance(slot.parameter_name, str)
        and slot.resource_field is not None
        and slot.parameter_location in SUPPORTED_LOCATIONS
    )


def build_parameter_requirements(graph: DependencyGraph) -> dict[RequirementKey, ParameterRequirement]:
    """Index resource inputs by operation / location / parameter name."""
    requirements: dict[RequirementKey, ParameterRequirement] = {}
    for label, operation in graph.operations.items():
        for slot in operation.inputs:
            if not _is_supported_input(slot):
                continue
            assert isinstance(slot.parameter_name, str)
            key = (label, slot.parameter_location, slot.parameter_name)
            requirements[key] = ParameterRequirement(
                resource_name=slot.resource.name,
                resource_field=slot.resource_field,  # type: ignore[arg-type]
            )
    return requirements


@dataclass
class OpenApiResourceProvider(ParameterSchemaAugmenter):
    """Augment parameter schemas with enums built from captured resources."""

    repository: ResourceRepository
    requirements: dict[RequirementKey, ParameterRequirement]

    __slots__ = ("repository", "requirements")

    def augment(
        self,
        *,
        operation: APIOperation,
        location: ParameterLocation,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return schema

        augmented: dict[str, Any] | None = None
        new_properties: dict[str, Any] | None = None

        for name, property_schema in properties.items():
            if not isinstance(property_schema, dict):
                continue
            requirement = self.requirements.get((operation.label, location, name))
            if requirement is None:
                continue
            enum_values = self._collect_values(requirement)
            if not enum_values:
                continue
            if augmented is None:
                augmented = dict(schema)
                new_properties = dict(properties)
                augmented["properties"] = new_properties
            assert new_properties is not None
            new_properties[name] = self._wrap_with_enum(property_schema, enum_values)

        return augmented or schema

    def _collect_values(self, requirement: ParameterRequirement) -> list[Any]:
        instances = tuple(self.repository.iter_instances(requirement.resource_name))
        if not instances:
            return []
        values: list[Any] = []
        seen: set[Any] = set()
        for instance in reversed(instances):
            value = instance.data.get(requirement.resource_field)
            if not isinstance(value, (str, int, float, bool)):
                continue
            if value in seen:
                continue
            seen.add(value)
            values.append(value)
            if len(values) >= MAX_ENUM_SIZE:
                break
        return values

    def _wrap_with_enum(self, schema: dict[str, Any], enum_values: list[Any]) -> dict[str, Any]:
        candidate = {"enum": enum_values}
        cloned = deepclone(schema)
        any_of = cloned.get("anyOf")
        if isinstance(any_of, list):
            return {**cloned, "anyOf": [*any_of, candidate]}
        return {"anyOf": [cloned, candidate]}
