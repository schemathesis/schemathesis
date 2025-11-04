from __future__ import annotations

import difflib
import enum
from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass
from typing import Any, TypeAlias

from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transforms import encode_pointer
from schemathesis.resources.descriptors import Cardinality
from schemathesis.specs.openapi.stateful.links import SCHEMATHESIS_LINK_EXTENSION


@dataclass
class DependencyGraph:
    """Graph of API operations and their resource dependencies."""

    operations: OperationMap
    resources: ResourceMap

    __slots__ = ("operations", "resources")

    def serialize(self) -> dict[str, Any]:
        serialized = asdict(self)

        for operation in serialized["operations"].values():
            del operation["method"]
            del operation["path"]
            for input in operation["inputs"]:
                input["resource"] = input["resource"]["name"]
            for output in operation["outputs"]:
                output["resource"] = output["resource"]["name"]

        for resource in serialized["resources"].values():
            del resource["name"]
            del resource["source"]

        return serialized

    def iter_links(self) -> Iterator[ResponseLinks]:
        """Generate OpenAPI Links connecting producer and consumer operations.

        Creates links from operations that produce resources to operations that
        consume them. For example: `POST /users` (creates `User`) -> `GET /users/{id}`
        (needs `User.id` parameter).
        """
        encoded_paths = {id(op): encode_pointer(op.path) for op in self.operations.values()}

        # Index consumers by resource
        consumers_by_resource: dict[int, dict[int, tuple[OperationNode, list[InputSlot]]]] = defaultdict(dict)
        for consumer in self.operations.values():
            consumer_id = id(consumer)
            for input_slot in consumer.inputs:
                resource_id = id(input_slot.resource)
                if consumer_id not in consumers_by_resource[resource_id]:
                    consumers_by_resource[resource_id][consumer_id] = (consumer, [])
                consumers_by_resource[resource_id][consumer_id][1].append(input_slot)

        for producer in self.operations.values():
            producer_path = encoded_paths[id(producer)]
            producer_id = id(producer)

            for output_slot in producer.outputs:
                # Only iterate over consumers that match this resource
                relevant_consumers = consumers_by_resource.get(id(output_slot.resource), {})

                for consumer_id, (consumer, input_slots) in relevant_consumers.items():
                    # Skip self-references
                    if consumer_id == producer_id:
                        continue

                    consumer_path = encoded_paths[consumer_id]
                    links: dict[str, LinkDefinition] = {}

                    for input_slot in input_slots:
                        if input_slot.resource_field is not None:
                            body_pointer = extend_pointer(
                                output_slot.pointer, input_slot.resource_field, output_slot.cardinality
                            )
                        else:
                            # No resource field means use the whole resource
                            body_pointer = output_slot.pointer
                        link_name = f"{consumer.method.capitalize()}{input_slot.resource.name}"
                        parameters = {}
                        request_body: dict[str, Any] | list = {}
                        # Data is extracted from response body
                        if input_slot.parameter_location == ParameterLocation.BODY:
                            if isinstance(input_slot.parameter_name, int):
                                request_body = [f"$response.body#{body_pointer}"]
                            else:
                                request_body = {
                                    input_slot.parameter_name: f"$response.body#{body_pointer}",
                                }
                        else:
                            parameters = {
                                f"{input_slot.parameter_location.value}.{input_slot.parameter_name}": f"$response.body#{body_pointer}",
                            }
                        existing = links.get(link_name)
                        if existing is not None:
                            existing.parameters.update(parameters)
                            if isinstance(existing.request_body, dict) and isinstance(request_body, dict):
                                existing.request_body.update(request_body)
                            else:
                                existing.request_body = request_body
                            continue
                        links[link_name] = LinkDefinition(
                            operation_ref=f"#/paths/{consumer_path}/{consumer.method}",
                            parameters=parameters,
                            request_body=request_body,
                        )

                    if links:
                        yield ResponseLinks(
                            producer_operation_ref=f"#/paths/{producer_path}/{producer.method}",
                            status_code=output_slot.status_code,
                            links=links,
                        )

    def assert_fieldless_resources(self, key: str, known: dict[str, frozenset[str]]) -> None:  # pragma: no cover
        """Verify all resources have at least one field."""
        # Fieldless resources usually indicate failed schema extraction, which can be caused by a bug
        known_fieldless = known.get(key, frozenset())

        for name, resource in self.resources.items():
            if not resource.fields and name not in known_fieldless:
                raise AssertionError(f"Resource {name} has no fields")

    def assert_incorrect_field_mappings(self, key: str, known: dict[str, frozenset[str]]) -> None:
        """Verify all input slots reference valid fields in their resources."""
        known_mismatches = known.get(key, frozenset())

        for operation in self.operations.values():
            for input in operation.inputs:
                # Skip unreliable definition sources
                if input.resource.source < DefinitionSource.SCHEMA_WITH_PROPERTIES:
                    continue
                resource = self.resources[input.resource.name]
                if (
                    input.resource_field not in resource.fields
                    and resource.name not in known_mismatches
                    and input.resource_field is not None
                ):  # pragma: no cover
                    message = (
                        f"Operation '{operation.method.upper()} {operation.path}': "
                        f"InputSlot references field '{input.resource_field}' "
                        f"not found in resource '{resource.name}'"
                    )
                    matches = difflib.get_close_matches(input.resource_field, resource.fields, n=1, cutoff=0.6)
                    if matches:
                        message += f". Closest field - `{matches[0]}`"
                    if resource.fields:
                        message += f". Available fields - {', '.join(resource.fields)}"
                    else:
                        message += ". Resource has no fields"
                    raise AssertionError(message)


def extend_pointer(base: str, field: str, cardinality: Cardinality) -> str:
    if not base.endswith("/"):
        base += "/"
    if cardinality == Cardinality.MANY:
        # For arrays, reference first element: /data → /data/0
        base += "0/"
    base += encode_pointer(field)
    return base


@dataclass
class LinkDefinition:
    """OpenAPI Link Object definition.

    Represents a single link from a producer operation's response to a
    consumer operation's input parameter.
    """

    operation_ref: str
    """Reference to target operation (e.g., '#/paths/~1users~1{id}/get')"""

    parameters: dict[str, str]
    """Parameter mappings (e.g., {'path.id': '$response.body#/id'})"""

    request_body: dict[str, str] | list
    """Request body (e.g., {'path.id': '$response.body#/id'})"""

    __slots__ = ("operation_ref", "parameters", "request_body")

    def to_openapi(self) -> dict[str, Any]:
        """Convert to OpenAPI Links format."""
        links: dict[str, Any] = {
            "operationRef": self.operation_ref,
            SCHEMATHESIS_LINK_EXTENSION: {"is_inferred": True},
        }
        if self.parameters:
            links["parameters"] = self.parameters
        if self.request_body:
            links["requestBody"] = self.request_body
            links[SCHEMATHESIS_LINK_EXTENSION]["merge_body"] = True
        return links


@dataclass
class ResponseLinks:
    """Collection of OpenAPI Links for a producer operation's response.

    Represents all links from a single response (e.g., POST /users -> 201)
    to consumer operations that can use the produced resource.

    Example:
        POST /users -> 201 might have links to:
        - GET /users/{id}
        - PATCH /users/{id}
        - DELETE /users/{id}

    """

    producer_operation_ref: str
    """Reference to producer operation (e.g., '#/paths/~1users/post')"""

    status_code: str
    """Response status code (e.g., '201', '200', 'default')"""

    links: dict[str, LinkDefinition]
    """Named links (e.g., {'GetUserById': LinkDefinition(...)})"""

    __slots__ = ("producer_operation_ref", "status_code", "links")

    def to_openapi(self) -> dict[str, Any]:
        """Convert to OpenAPI response links format."""
        return {name: link_def.to_openapi() for name, link_def in self.links.items()}


@dataclass
class NormalizedLink:
    """Normalized representation of a link."""

    path: str
    method: str
    parameters: set[str]
    request_body: Any

    __slots__ = ("path", "method", "parameters", "request_body")


@dataclass
class OperationNode:
    """An API operation with its input/output dependencies."""

    method: str
    path: str
    # What this operation NEEDS
    inputs: list[InputSlot]
    # What this operation PRODUCES
    outputs: list[OutputSlot]

    __slots__ = ("method", "path", "inputs", "outputs")


@dataclass
class InputSlot:
    """A required input for an operation."""

    # Which resource is needed
    resource: ResourceDefinition
    # Which field from that resource (e.g., "id").
    # None if passing the whole resource
    resource_field: str | None
    # Where it goes in the request (e.g., "userId")
    # Integer means index in an array (only single items are supported)
    parameter_name: str | int
    parameter_location: ParameterLocation

    __slots__ = ("resource", "resource_field", "parameter_name", "parameter_location")


@dataclass
class OutputSlot:
    """Describes how to extract a resource from an operation's response."""

    # Which resource type
    resource: ResourceDefinition
    # Where in response body (JSON pointer)
    pointer: str
    # Is this a single resource or an array?
    cardinality: Cardinality
    # HTTP status code
    status_code: str

    __slots__ = ("resource", "pointer", "cardinality", "status_code")


@dataclass
class ResourceDefinition:
    """A minimal description of a resource structure."""

    name: str
    # A sorted list of resource fields
    fields: list[str]
    # Field types mapping
    types: dict[str, set[str]]
    # How this resource was created
    source: DefinitionSource

    __slots__ = ("name", "fields", "types", "source")

    @classmethod
    def without_properties(cls, name: str) -> ResourceDefinition:
        return cls(name=name, fields=[], types={}, source=DefinitionSource.SCHEMA_WITHOUT_PROPERTIES)

    @classmethod
    def inferred_from_parameter(cls, name: str, parameter_name: str | None) -> ResourceDefinition:
        fields = [parameter_name] if parameter_name is not None else []
        return cls(name=name, fields=fields, types={}, source=DefinitionSource.PARAMETER_INFERENCE)


class DefinitionSource(enum.IntEnum):
    """Quality level of resource information.

    Lower values are less reliable and should be replaced by higher values.
    Same values should be merged (union of fields).
    """

    # From spec but no structural information
    SCHEMA_WITHOUT_PROPERTIES = 0
    # Guessed from parameter names (not in spec)
    PARAMETER_INFERENCE = 1
    # From spec with actual field definitions
    SCHEMA_WITH_PROPERTIES = 2


OperationMap: TypeAlias = dict[str, OperationNode]
ResourceMap: TypeAlias = dict[str, ResourceDefinition]
CanonicalizationCache: TypeAlias = dict[str, Mapping[str, Any]]
