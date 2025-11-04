from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeAlias

from schemathesis.core import NOT_SET, deserialization
from schemathesis.core.jsonschema.types import JsonSchema
from schemathesis.core.parameters import ParameterLocation
from schemathesis.resources import ExtraDataSource
from schemathesis.resources.repository import ResourceRepository
from schemathesis.specs.openapi.stateful.dependencies.models import DependencyGraph

if TYPE_CHECKING:
    from schemathesis.core.transport import Response
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation

RequirementKey = tuple[str, ParameterLocation, str]
DedupKey: TypeAlias = tuple[type, str | int | float | bool | None]


@dataclass(slots=True, frozen=True)
class ParameterRequirement:
    resource_name: str
    resource_field: str


def build_parameter_requirements(graph: DependencyGraph) -> dict[RequirementKey, ParameterRequirement]:
    """Index resource inputs by operation / location / parameter name."""
    requirements: dict[RequirementKey, ParameterRequirement] = {}
    for label, operation in graph.operations.items():
        for slot in operation.inputs:
            if not isinstance(slot.parameter_name, str) or slot.resource_field is None:
                continue
            key = (label, slot.parameter_location, slot.parameter_name)
            requirements[key] = ParameterRequirement(
                resource_name=slot.resource.name, resource_field=slot.resource_field
            )
    return requirements


@dataclass(slots=True)
class OpenApiExtraDataSource(ExtraDataSource):
    """Provides extra data from captured API responses to augment parameter schemas."""

    repository: ResourceRepository
    requirements: dict[RequirementKey, ParameterRequirement]

    def augment(
        self,
        *,
        operation: APIOperation,
        location: ParameterLocation,
        schema: JsonSchema,
    ) -> JsonSchema:
        # Augment parameter schemas with enum values from captured responses.
        # For each property with a requirement mapping, wrap its schema in anyOf
        # with an enum containing real values from successful API calls.
        if not isinstance(schema, dict):
            return schema

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
            # Copy-on-write: only mutate when we have values to add
            if augmented is None:
                augmented = dict(schema)
                new_properties = dict(properties)
                augmented["properties"] = new_properties
            assert new_properties is not None
            new_properties[name] = self._wrap_with_enum(property_schema, enum_values)

        return augmented or schema

    def _collect_values(self, requirement: ParameterRequirement) -> list[Any]:
        """Collect unique values from captured resource instances."""
        instances = self.repository.iter_instances(requirement.resource_name)
        values: list[Any] = []
        seen: set[DedupKey] = set()

        for instance in instances:
            value = instance.data.get(requirement.resource_field, NOT_SET)
            if value is NOT_SET:
                continue

            dedup_key: DedupKey
            if isinstance(value, (str, int, float, bool)) or value is None:
                dedup_key = (type(value), value)
            else:
                try:
                    serialized = json.dumps(value, sort_keys=True, default=str)
                    dedup_key = (type(value), serialized)
                except (TypeError, ValueError):
                    continue

            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            values.append(value)

        return values

    def _wrap_with_enum(self, schema: dict[str, Any], values: list[Any]) -> dict[str, Any]:
        variants = {"enum": values}
        any_of = schema.get("anyOf")
        if isinstance(any_of, list):
            return {**schema, "anyOf": [*any_of, variants]}
        return {"anyOf": [schema, variants]}

    def should_record(self, *, operation: str) -> bool:
        """Check if responses should be recorded for this operation."""
        return bool(self.repository.descriptors_for_operation(operation))

    def record_response(
        self,
        *,
        operation: APIOperation,
        response: Response,
        case: Case,
    ) -> None:
        """Record a response for later use in test generation.

        Handles deserialization and extraction of response data internally.
        """
        content_types = response.headers.get("content-type")
        if not content_types:
            return
        content_type = content_types[0]
        context = deserialization.DeserializationContext(operation=operation, case=case)
        try:
            payload = deserialization.deserialize_response(response, content_type, context=context)
        except (TypeError, ValueError, json.JSONDecodeError, NotImplementedError):
            return
        self.repository.record_response(
            operation=operation.label,
            status_code=response.status_code,
            payload=payload,
        )
