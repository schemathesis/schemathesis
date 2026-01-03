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

# Marker indicating that the schema has been augmented with captured response values
AUGMENTED_MARKER = "x-schemathesis-augmented"


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
        # For properties with requirement mappings, wrap schemas with anyOf
        # containing real values from successful API calls.
        if not isinstance(schema, dict):
            return schema

        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return schema

        # Collect all requirements for properties in this schema
        property_requirements: dict[str, ParameterRequirement] = {}
        for name in properties:
            requirement = self.requirements.get((operation.label, location, name))
            if requirement is not None:
                property_requirements[name] = requirement

        if not property_requirements:
            return schema

        # Single requirement: use property-level anyOf
        if len(property_requirements) == 1:
            return self._augment_property_level(schema, properties, property_requirements)

        # Multiple requirements: use object-level anyOf to preserve relationships
        return self._augment_object_level(schema, property_requirements)

    def _augment_property_level(
        self,
        schema: dict[str, Any],
        properties: dict[str, Any],
        requirements: dict[str, ParameterRequirement],
    ) -> JsonSchema:
        """Wrap individual properties with anyOf."""
        augmented: dict[str, Any] | None = None
        new_properties: dict[str, Any] | None = None

        for name, requirement in requirements.items():
            property_schema = properties.get(name)
            if not isinstance(property_schema, dict):
                continue
            enum_values = self._collect_values(requirement)
            if not enum_values:
                continue
            # Copy-on-write: only mutate when we have values to add
            if augmented is None:
                augmented = dict(schema)
                new_properties = dict(properties)
                augmented["properties"] = new_properties
                augmented[AUGMENTED_MARKER] = True
            assert new_properties is not None
            new_properties[name] = self._wrap_with_enum(property_schema, enum_values)

        return augmented or schema

    def _augment_object_level(
        self,
        schema: dict[str, Any],
        requirements: dict[str, ParameterRequirement],
    ) -> JsonSchema:
        """Use anyOf with complete schemas to preserve relationships between properties.

        Creates anyOf variants where each variant is a complete schema:
        - First variant: original schema (generates random valid values)
        - Additional variants: schemas with const values for captured data
        """
        variants = self._collect_object_variants(requirements)
        if not variants:
            return schema

        # Build anyOf with complete schemas
        any_of_variants: list[dict[str, Any]] = [schema]  # Original schema first
        for variant in variants:
            any_of_variants.append(
                {
                    "type": "object",
                    "properties": {name: {"const": value} for name, value in variant.items()},
                    "required": list(variant.keys()),
                }
            )

        return {"anyOf": any_of_variants, AUGMENTED_MARKER: True}

    def _collect_object_variants(self, requirements: dict[str, ParameterRequirement]) -> list[dict[str, Any]]:
        """Collect complete value sets that preserve relationships between properties."""
        # Get all resource types involved
        resource_names = {req.resource_name for req in requirements.values()}

        variants: list[dict[str, Any]] = []
        seen: set[str] = set()

        # For each resource instance, try to build a complete variant
        for resource_name in resource_names:
            for instance in self.repository.iter_instances(resource_name):
                variant: dict[str, Any] = {}

                for param_name, req in requirements.items():
                    if req.resource_name == resource_name:
                        # Value from the resource data (e.g., Pet.id from response)
                        value = instance.data.get(req.resource_field)
                    else:
                        # Value from context (e.g., ownerId from request path)
                        value = instance.context.get(param_name)

                    if value is not None:
                        variant[param_name] = value

                # Only include if we filled ALL requirements
                if len(variant) == len(requirements):
                    # Deduplicate by serializing the variant
                    key = json.dumps(variant, sort_keys=True, default=str)
                    if key not in seen:
                        seen.add(key)
                        variants.append(variant)

        return variants

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
            context=case.path_parameters or {},
        )
